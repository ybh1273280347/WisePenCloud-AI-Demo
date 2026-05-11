import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Set, Tuple

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
    normalize_queries,
    normalize_url_for_dedup,
)
from chat.core.config.app_settings import settings
from common.logger import log_event, log_fail, log_ok

SearchStageFunc = Callable[..., Awaitable[Optional[SearchResponse]]]

MAX_BROAD_SEARCH_QUERIES = 4
MAX_BROAD_SEARCH_CONCURRENCY = 3

DEFAULT_FINAL_RESULTS = 20

DEFAULT_DEDUPE_DOMAINS = True
DEFAULT_MAX_PER_DOMAIN = 2


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
    """协调多阶段网页搜索：新鲜缓存、搜索引擎、过期缓存、可选的 Tavily 回退。"""

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
        key = make_search_cache_key(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

        fresh = await self._cache.get_fresh(key)
        if fresh is not None:
            log_ok("网页搜索", stage="fresh_cache", query=query, max_results=max_results, with_images=with_images, results=len(fresh.results), images=len(fresh.images))
            return _with_source(fresh, "fresh_cache")

        last_empty: Optional[SearchResponse] = None
        failures: List[str] = []

        for stage in self._chain:
            if stage.name in self._disabled_stages:
                failures.append(f"{stage.name}: disabled_by_test")

                log_event("网页搜索跳过：测试注入禁用阶段", stage=stage.name, query=query, max_results=max_results, with_images=with_images)
                continue

            if stage.name == "stale_cache" and freshness_required:
                failures.append("stale_cache: skipped_for_freshness_required")

                log_event("网页搜索跳过：要求新鲜度，跳过过期缓存", stage=stage.name, query=query, max_results=max_results, with_images=with_images)
                continue

            if stage.name == "tavily" and not allow_paid_fallback:
                failures.append("tavily: skipped_for_paid_fallback_disabled")

                log_event("网页搜索跳过：禁用付费回退，跳过 Tavily", stage=stage.name, query=query, max_results=max_results, with_images=with_images)
                continue

            try:
                response = await stage.handler(
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )

            except Exception as e:
                failures.append(f"{stage.name}: {type(e).__name__}: {e}")

                log_fail("网页搜索", e, stage=stage.name, query=query, max_results=max_results, with_images=with_images)
                continue

            if response is None:
                failures.append(f"{stage.name}: returned_none")

                log_event("网页搜索降级：阶段返回空，切换下一阶段", stage=stage.name, query=query, max_results=max_results, with_images=with_images)
                continue

            if not has_response_content(response):
                last_empty = response
                failures.append(f"{stage.name}: empty_result")

                log_event("网页搜索降级：阶段返回空结果，切换下一阶段", stage=stage.name, query=query, max_results=max_results, with_images=with_images, results=len(response.results), images=len(response.images), has_answer=bool(response.answer), source=response.source)

                if self._continue_on_empty:
                    continue

                return _with_source(response, stage.name)

            response = _with_source(response, stage.name)

            if stage.cacheable:
                await self._cache.set(key, response)

            log_ok("网页搜索", stage=stage.name, query=query, max_results=max_results, with_images=with_images, results=len(response.results), images=len(response.images))

            return response

        log_fail("网页搜索", "所有搜索阶段均失败", query=query, max_results=max_results, with_images=with_images, freshness_required=freshness_required, failures=failures)

        return last_empty

    async def search_many(
        self,
        queries: List[str],
        *,
        max_results_per_query: int = 8,
        final_max_results: int = 20,
        with_images: bool = False,
        concurrency: int = MAX_BROAD_SEARCH_CONCURRENCY,
        dedupe_domains: bool = DEFAULT_DEDUPE_DOMAINS,
        max_per_domain: int = DEFAULT_MAX_PER_DOMAIN,
        notes: Optional[List[str]] = None,
    ) -> SearchResponse:
        """并发执行多查询搜索并合并结果集。

        每个子查询的 allow_paid_fallback=False，
        因此 search_many 不会触发 Tavily 付费回退。
        Tavily 仍可通过 search(... allow_paid_fallback=True) 使用。"""
        normalized_queries = normalize_queries(
            queries,
            limit=MAX_BROAD_SEARCH_QUERIES,
            notes=notes,
        )

        if not normalized_queries:
            return SearchResponse(query="", results=(), images=(), source="multi")

        semaphore = asyncio.Semaphore(concurrency)

        async def _run_one(search_query: str) -> Optional[SearchResponse]:
            async with semaphore:
                return await self.search(
                    query=search_query,
                    max_results=max_results_per_query,
                    with_images=with_images,
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

        merged = merge_many_search_responses(
            query=" | ".join(normalized_queries),
            responses=responses,
            final_max_results=final_max_results,
            dedupe_domains=dedupe_domains,
            max_per_domain=max_per_domain,
            notes=notes,
        )

        log_ok(
            "web multi-query search",
            queries=len(normalized_queries),
            results=len(merged.results),
            images=len(merged.images),
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


def _deduplicate_results_by_url(
    results: Tuple[SearchResult, ...],
    *,
    notes: Optional[List[str]] = None,
) -> Tuple[SearchResult, ...]:
    seen: Set[str] = set()
    deduped: List[SearchResult] = []
    removed_count = 0

    for result in results:
        key = normalize_url_for_dedup(result.url)
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


def _deduplicate_images(
    images: Tuple[ImageResult, ...],
    *,
    notes: Optional[List[str]] = None,
) -> Tuple[ImageResult, ...]:
    seen: Set[str] = set()
    deduped: List[ImageResult] = []
    removed_count = 0

    for image in images:
        key = normalize_url_for_dedup(image.url)
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


def merge_many_search_responses(
    *,
    query: str,
    responses: List[SearchResponse],
    final_max_results: int,
    dedupe_domains: bool,
    max_per_domain: int,
    notes: Optional[List[str]] = None,
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
