import asyncio
import re
from dataclasses import dataclass
from datetime import date
from typing import Awaitable, Callable, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from chat.application.web_search.cache import (
    SearchCache,
    make_search_cache_key,
)
from chat.application.web_search.models import (
    ImageResult,
    SearchResponse,
    SearchResult,
    has_response_content,
)
from chat.application.web_search.searcher import (
    DuckDuckGoBufferSearcher,
    SearXNGSearcher,
    TavilySearcher,
)
from chat.application.web_search.utils import (
    add_note,
    deduplicate_results_by_domain,
    normalize_int,
)
from chat.application.web_search.utils.domains import _filter_results_by_domains
from chat.core.config.app_settings import settings
from common.logger import log_fail, log_ok

SearchStageFunc = Callable[..., Awaitable[Optional[SearchResponse]]]

MAX_BROAD_SEARCH_QUERIES = 4
MAX_BROAD_SEARCH_CONCURRENCY = 3

MAX_RESULTS_PER_QUERY = 10
DEFAULT_FINAL_RESULTS = 12
MAX_FINAL_RESULTS = 20

DEFAULT_DEDUPE_DOMAINS = True
DEFAULT_MAX_PER_DOMAIN = 2
MAX_PER_DOMAIN = 5

PAID_FALLBACK_MIN_RESULTS = 3
PAID_FALLBACK_LIMIT = 1
MAX_SEARCH_QUERY_CHARS = 400

_TRACKING_QUERY_PARAMS: Set[str] = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "msclkid",
}
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_TIME_WORD_RE = re.compile(
    r"\b(?:latest|recent|current|today|yesterday|week|month|year)\b",
    re.IGNORECASE,
)
_VALID_TIME_RANGES: Set[str] = {"day", "week", "month", "year"}


def _with_source(response: SearchResponse, source: str) -> SearchResponse:
    return SearchResponse(
        query=response.query,
        results=response.results,
        answer=response.answer,
        images=response.images,
        source=source,
    )


@dataclass(frozen=True, slots=True)
class SearchStage:
    name: str
    handler: SearchStageFunc
    cacheable: bool = True


class SearchCoordinator:
    """联网搜索调度器：Fresh Cache + 显式降级链"""

    def __init__(
        self,
        *,
        cache: SearchCache,
        searxng_searcher: SearXNGSearcher,
        duckduckgo_searcher: DuckDuckGoBufferSearcher,
        tavily_searcher: TavilySearcher,
        continue_on_empty: bool = True,
        disabled_stages: Optional[Set[str]] = None,
    ) -> None:
        self._cache = cache
        self._searxng = searxng_searcher
        self._duckduckgo = duckduckgo_searcher
        self._tavily = tavily_searcher
        self._continue_on_empty = continue_on_empty
        self._disabled_stages = disabled_stages or set()

        self._chain: Tuple[SearchStage, ...] = (
            SearchStage("searxng", self._search_searxng, cacheable=True),
            SearchStage("duckduckgo", self._search_duckduckgo, cacheable=True),
            SearchStage("stale_cache", self._search_stale_cache, cacheable=False),
            SearchStage("tavily", self._search_tavily, cacheable=True),
        )

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
        freshness_required: bool = False,
        allow_paid_fallback: bool = True,
    ) -> Optional[SearchResponse]:
        max_results = normalize_int(
            max_results,
            default=5,
            minimum=1,
            maximum=MAX_RESULTS_PER_QUERY,
        )
        key = make_search_cache_key(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

        fresh = await self._cache.get_fresh(key)
        if fresh is not None:
            log_ok(
                "联网搜索",
                stage="fresh_cache",
                query=query,
                max_results=max_results,
                with_images=with_images,
                results=len(fresh.results),
                images=len(fresh.images),
            )
            return _with_source(fresh, "fresh_cache")

        last_empty: Optional[SearchResponse] = None
        failures: List[str] = []

        for stage in self._chain:
            if stage.name in self._disabled_stages:
                failures.append(f"{stage.name}: disabled_by_test")

                log_fail(
                    "联网搜索跳过",
                    "测试注入：stage 被禁用，触发降级",
                    stage=stage.name,
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )
                continue

            if stage.name == "stale_cache" and freshness_required:
                failures.append("stale_cache: skipped_for_freshness_required")

                log_fail(
                    "联网搜索跳过",
                    "freshness_required=True，跳过 stale cache",
                    stage=stage.name,
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )
                continue

            if stage.name == "tavily" and not allow_paid_fallback:
                failures.append("tavily: skipped_for_paid_fallback_disabled")

                log_fail(
                    "联网搜索跳过",
                    "allow_paid_fallback=False, skip Tavily",
                    stage=stage.name,
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )
                continue

            try:
                response = await stage.handler(
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )

            except Exception as e:
                failures.append(f"{stage.name}: {type(e).__name__}: {e}")

                log_fail(
                    "联网搜索",
                    e,
                    stage=stage.name,
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )
                continue

            if response is None:
                failures.append(f"{stage.name}: returned_none")

                log_fail(
                    "联网搜索",
                    "stage 返回 None，触发降级",
                    stage=stage.name,
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )
                continue

            if not has_response_content(response):
                last_empty = response
                failures.append(f"{stage.name}: empty_result")

                log_fail(
                    "联网搜索",
                    "搜索结果为空，触发降级",
                    stage=stage.name,
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                    results=len(response.results),
                    images=len(response.images),
                    has_answer=bool(response.answer),
                    source=response.source,
                )

                if self._continue_on_empty:
                    continue

                return _with_source(response, stage.name)

            response = _with_source(response, stage.name)

            if stage.cacheable:
                await self._cache.set(key, response)

            log_ok(
                "联网搜索",
                stage=stage.name,
                query=query,
                max_results=max_results,
                with_images=with_images,
                results=len(response.results),
                images=len(response.images),
            )

            return response

        log_fail(
            "联网搜索",
            "所有搜索阶段均失败",
            query=query,
            max_results=max_results,
            with_images=with_images,
            freshness_required=freshness_required,
            failures=failures,
        )

        return last_empty

    async def search_many(
        self,
        queries: List[str],
        *,
        max_results_per_query: int = 5,
        final_max_results: int = DEFAULT_FINAL_RESULTS,
        with_images: bool = False,
        freshness_required: bool = False,
        allow_paid_fallback: bool = False,
        concurrency: int = MAX_BROAD_SEARCH_CONCURRENCY,
        dedupe_domains: Optional[bool] = None,
        max_per_domain: int = DEFAULT_MAX_PER_DOMAIN,
        notes: Optional[List[str]] = None,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        time_range: Optional[str] = None,
    ) -> SearchResponse:
        normalized_queries = _normalize_queries(
            queries,
            limit=MAX_BROAD_SEARCH_QUERIES,
            notes=notes,
            time_range=time_range,
        )

        if not normalized_queries:
            return SearchResponse(query="", results=(), images=(), source="multi")

        max_results_per_query = normalize_int(
            max_results_per_query,
            default=5,
            minimum=1,
            maximum=MAX_RESULTS_PER_QUERY,
        )
        final_max_results = normalize_int(
            final_max_results,
            default=DEFAULT_FINAL_RESULTS,
            minimum=1,
            maximum=MAX_FINAL_RESULTS,
        )
        concurrency = normalize_int(
            concurrency,
            default=MAX_BROAD_SEARCH_CONCURRENCY,
            minimum=1,
            maximum=MAX_BROAD_SEARCH_CONCURRENCY,
        )
        max_per_domain = normalize_int(
            max_per_domain,
            default=DEFAULT_MAX_PER_DOMAIN,
            minimum=1,
            maximum=MAX_PER_DOMAIN,
        )

        effective_dedupe_domains = (
            DEFAULT_DEDUPE_DOMAINS if dedupe_domains is None else dedupe_domains
        )

        semaphore = asyncio.Semaphore(concurrency)

        async def _run_one(search_query: str) -> Optional[SearchResponse]:
            async with semaphore:
                return await self.search(
                    query=search_query,
                    max_results=max_results_per_query,
                    with_images=with_images,
                    freshness_required=freshness_required,
                    allow_paid_fallback=False,
                )

        tasks = [_run_one(search_query) for search_query in normalized_queries]
        raw_responses = await asyncio.gather(*tasks, return_exceptions=True)

        responses: List[SearchResponse] = []
        failures: List[str] = []

        for search_query, result in zip(normalized_queries, raw_responses):
            if isinstance(result, Exception):
                failures.append(f"{search_query}: {type(result).__name__}: {result}")
                continue
            if result is None:
                failures.append(f"{search_query}: returned_none")
                continue
            if not has_response_content(result):
                failures.append(f"{search_query}: empty_result")
                continue
            responses.append(result)

        merged = _merge_many_search_responses(
            query=" | ".join(normalized_queries),
            responses=responses,
            final_max_results=final_max_results,
            dedupe_domains=effective_dedupe_domains,
            max_per_domain=max_per_domain,
            notes=notes,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
        )

        tavily_used = False

        if (
            allow_paid_fallback
            and "tavily" not in self._disabled_stages
            and len(merged.results) < PAID_FALLBACK_MIN_RESULTS
            and normalized_queries
        ):
            try:
                paid_response = await self._search_tavily(
                    query=normalized_queries[0],
                    max_results=max_results_per_query,
                    with_images=with_images,
                )
                tavily_used = True
            except Exception as exc:
                failures.append(f"tavily_paid_once: {type(exc).__name__}: {exc}")
                paid_response = None

            if paid_response is not None and has_response_content(paid_response):
                add_note(notes, "Tavily paid fallback was used once.")
                merged = _merge_many_search_responses(
                    query=" | ".join(normalized_queries),
                    responses=[merged, _with_source(paid_response, "tavily")],
                    final_max_results=final_max_results,
                    dedupe_domains=effective_dedupe_domains,
                    max_per_domain=max_per_domain,
                    notes=notes,
                    include_domains=include_domains,
                    exclude_domains=exclude_domains,
                )

        log_ok(
            "联网广搜",
            queries=len(normalized_queries),
            results=len(merged.results),
            images=len(merged.images),
            allow_paid_fallback=allow_paid_fallback,
            tavily_used=tavily_used,
            paid_fallback_min_results=PAID_FALLBACK_MIN_RESULTS,
            failures=failures,
        )

        return merged

    async def _search_searxng(
        self,
        *,
        query: str,
        max_results: int,
        with_images: bool,
    ) -> Optional[SearchResponse]:
        return await self._searxng.search(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

    async def _search_duckduckgo(
        self,
        *,
        query: str,
        max_results: int,
        with_images: bool,
    ) -> Optional[SearchResponse]:
        return await self._duckduckgo.search(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

    async def _search_stale_cache(
        self,
        *,
        query: str,
        max_results: int,
        with_images: bool,
    ) -> Optional[SearchResponse]:
        key = make_search_cache_key(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

        return await self._cache.get_stale(key)

    async def _search_tavily(
        self,
        *,
        query: str,
        max_results: int,
        with_images: bool,
    ) -> Optional[SearchResponse]:
        return await self._tavily.search(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )


def _normalize_queries(
    queries: List[str],
    *,
    limit: int,
    notes: Optional[List[str]] = None,
    time_range: Optional[str] = None,
    current_year: Optional[int] = None,
) -> List[str]:
    normalized: List[str] = []
    seen: Set[str] = set()
    skipped_duplicates = 0
    limit_reached = False

    for query in queries:
        if not isinstance(query, str):
            continue
        value = " ".join(query.strip().split())
        if not value:
            continue

        value, was_truncated = _truncate_query(value)
        if was_truncated:
            add_note(notes, "Query truncated to 400 characters.")

        value = _append_current_year_if_needed(
            value,
            time_range=time_range,
            current_year=current_year,
        )

        key = value.lower()
        if key in seen:
            skipped_duplicates += 1
            continue

        if len(normalized) >= limit:
            limit_reached = True
            break

        seen.add(key)
        normalized.append(value)

    if skipped_duplicates:
        add_note(notes, f"{skipped_duplicates} duplicate search queries were removed.")

    if limit_reached:
        add_note(notes, f"Search queries were limited to {limit} focused queries.")

    return normalized


def _truncate_query(query: str) -> Tuple[str, bool]:
    if len(query) <= MAX_SEARCH_QUERY_CHARS:
        return query, False

    candidate = query[:MAX_SEARCH_QUERY_CHARS].rstrip()
    space_index = candidate.rfind(" ")

    if space_index >= MAX_SEARCH_QUERY_CHARS // 2:
        candidate = candidate[:space_index].rstrip()

    if not candidate:
        candidate = query[:MAX_SEARCH_QUERY_CHARS].rstrip()

    return candidate, True


def _append_current_year_if_needed(
    query: str,
    *,
    time_range: Optional[str],
    current_year: Optional[int],
) -> str:
    normalized_time_range = _normalize_time_range(time_range)
    if normalized_time_range not in {"month", "year"}:
        return query

    if _YEAR_RE.search(query) or _TIME_WORD_RE.search(query):
        return query

    year = current_year if current_year is not None else date.today().year
    return f"{query} {year}"


def _normalize_time_range(time_range: Optional[str]) -> Optional[str]:
    if not isinstance(time_range, str):
        return None

    normalized = time_range.strip().lower()
    if normalized in _VALID_TIME_RANGES:
        return normalized

    return None


def _deduplicate_results_by_url(
    results: Tuple[SearchResult, ...],
    *,
    notes: Optional[List[str]] = None,
) -> Tuple[SearchResult, ...]:
    seen: Set[str] = set()
    deduped: List[SearchResult] = []
    removed_count = 0

    for result in results:
        key = _normalize_url_for_dedup(result.url)
        if not key:
            continue

        if key in seen:
            removed_count += 1
            continue

        seen.add(key)
        deduped.append(result)

    if removed_count:
        add_note(notes, f"{removed_count} duplicate URLs were removed.")

    return tuple(deduped)


def _normalize_url_for_dedup(url: str) -> str:
    value = url.strip()
    if not value:
        return ""

    try:
        parsed = urlparse(value)
    except Exception:
        return value

    if not parsed.scheme or not parsed.hostname:
        return value

    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower().removeprefix("www.")
    try:
        port = parsed.port
    except ValueError:
        port = None

    include_port = port is not None and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    )
    netloc = host if not include_port else f"{host}:{port}"

    path = parsed.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")

    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_QUERY_PARAMS
    ]
    query = urlencode(sorted(query_items))

    return urlunparse((scheme, netloc, path, "", query, ""))


def _deduplicate_images(
    images: Tuple[ImageResult, ...],
    *,
    notes: Optional[List[str]] = None,
) -> Tuple[ImageResult, ...]:
    seen: Set[str] = set()
    deduped: List[ImageResult] = []
    removed_count = 0

    for image in images:
        key = _normalize_url_for_dedup(image.url)
        if not key:
            continue

        if key in seen:
            removed_count += 1
            continue

        seen.add(key)
        deduped.append(image)

    if removed_count:
        add_note(notes, f"{removed_count} duplicate image URLs were removed.")

    return tuple(deduped)


def _merge_many_search_responses(
    *,
    query: str,
    responses: List[SearchResponse],
    final_max_results: int,
    dedupe_domains: bool,
    max_per_domain: int,
    notes: Optional[List[str]] = None,
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
) -> SearchResponse:
    results: List[SearchResult] = []
    images: List[ImageResult] = []
    answers: List[str] = []
    source_parts: Set[str] = set()

    for response in responses:
        results.extend(response.results)
        images.extend(response.images)
        if response.answer:
            answers.append(response.answer)
        source_parts.update(_split_source(response.source))

    deduped_results = _deduplicate_results_by_url(tuple(results), notes=notes)

    filtered_results = _filter_results_by_domains(
        deduped_results,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
    )
    if include_domains and len(filtered_results) < len(deduped_results):
        add_note(
            notes,
            f"include_domains filter reduced results from {len(deduped_results)} to {len(filtered_results)}.",
        )
    if exclude_domains and len(filtered_results) < len(deduped_results):
        add_note(
            notes,
            f"exclude_domains filter removed {len(deduped_results) - len(filtered_results)} results.",
        )

    deduped_results = filtered_results

    if dedupe_domains:
        before_domain_dedupe = len(deduped_results)
        deduped_results = deduplicate_results_by_domain(
            deduped_results,
            max_per_domain=max_per_domain,
        )
        if len(deduped_results) < before_domain_dedupe:
            add_note(
                notes,
                f"{before_domain_dedupe - len(deduped_results)} same-domain results were removed by domain dedupe.",
            )

    source = "multi"
    if source_parts:
        source = "multi:" + ",".join(sorted(source_parts))

    return SearchResponse(
        query=query,
        results=deduped_results[:final_max_results],
        answer=answers[0] if answers else None,
        images=_deduplicate_images(tuple(images), notes=notes)[:final_max_results],
        source=source,
    )


def _split_source(source: Optional[str]) -> Set[str]:
    if not source or source == "multi":
        return set()

    normalized = source.removeprefix("multi:")
    return {part for part in normalized.split(",") if part}


def create_search_coordinator() -> SearchCoordinator:
    cache = SearchCache(
        fresh_ttl=settings.WEB_SEARCH_FRESH_CACHE_TTL,
        stale_ttl=settings.WEB_SEARCH_STALE_CACHE_TTL,
        maxsize=settings.WEB_SEARCH_CACHE_MAXSIZE,
    )

    return SearchCoordinator(
        cache=cache,
        searxng_searcher=SearXNGSearcher(
            base_url=settings.SEARXNG_BASE_URL,
            timeout=settings.SEARXNG_TIMEOUT,
            language=settings.SEARXNG_LANGUAGE or None,
            safesearch=settings.SEARXNG_SAFESEARCH,
        ),
        duckduckgo_searcher=DuckDuckGoBufferSearcher(
            timeout=settings.DUCKDUCKGO_TIMEOUT,
            region=settings.DUCKDUCKGO_REGION,
            safesearch=settings.DUCKDUCKGO_SAFESEARCH,
        ),
        tavily_searcher=TavilySearcher(
            api_key=settings.TAVILY_API_KEY,
            timeout=settings.TAVILY_TIMEOUT,
        ),
    )
