import asyncio
import time
import uuid
from typing import Any, List, Optional, Set, Tuple

from chat.application.web_search.errors import (
    CustomSearchProviderUnavailableError,
    EmptySearchResultError,
)
from chat.application.web_search.internal.cache import SearchCache
from chat.application.web_search.internal.planning.models import (
    VariantSearchResponse,
    WikipediaGroundingResult,
)
from chat.application.web_search.internal.planning.planner import (
    MERGED_CANDIDATE_LIMIT,
    build_search_plan,
)
from chat.application.web_search.internal.ranking.url_ranker import (
    RankedUrlCandidate,
    rank_urls_pipeline,
)
from chat.application.web_search.internal.runner.custom_provider_runner import (
    run_custom_provider_calls,
)
from chat.application.web_search.internal.runner.fourget_variant_runner import (
    run_fourget_variants,
)
from chat.application.web_search.internal.runner.searxng_variant_runner import (
    run_searxng_variants,
)
from chat.application.web_search.internal.runner.serper_variant_runner import (
    run_serper_variants,
)
from chat.application.web_search.internal.runner.wikipedia_grounding_runner import (
    close_wikipedia_grounding_client,
    run_wikipedia_grounding,
)
from chat.application.web_search.internal.searcher.searxng_searcher import (
    SearXNGSearcher,
)
from chat.application.web_search.internal.searcher.fourget_searcher import (
    FourGetSearcher,
)
from chat.application.web_search.internal.searcher.serper_searcher import SerperSearcher
from chat.application.web_search.models.common import (
    ImageResult,
    SearchResponse,
    SearchResult,
)
from chat.application.web_search.internal.provider_policy import (
    select_custom_provider_calls,
    select_default_provider_calls,
)
from chat.application.web_search.search_provider_config.constants import (
    ERROR_NOT_CONFIGURED,
    ERROR_PROVIDER_ERROR,
    PUBLIC_ERROR_NOT_CONFIGURED,
    PUBLIC_ERROR_PROVIDER_ERROR,
    STATUS_PROVIDER_ERROR,
)
from chat.application.web_search.utils.queries import normalize_queries
from common.logger import log_event, log_fail

_DEFAULT_MAX_PER_DOMAIN = 2


class SearchManyRequest:
    __slots__ = (
        "queries",
        "language",
        "mode",
        "with_images",
        "custom_provider_params",
        "provider_mode",
        "user_id",
        "wikipedia_keywords",
    )

    def __init__(
        self,
        queries: List[str],
        *,
        language: Optional[str] = None,
        mode: str = "normal",
        with_images: bool = False,
        custom_provider_params: Optional[Any] = None,
        provider_mode: str = "default",
        user_id: Optional[str] = None,
        wikipedia_keywords: Optional[List[str]] = None,
    ) -> None:
        self.queries = list(queries)
        self.language = language
        self.mode = mode
        self.with_images = with_images
        self.custom_provider_params = custom_provider_params
        self.provider_mode = provider_mode
        self.user_id = user_id
        self.wikipedia_keywords = list(wikipedia_keywords) if wikipedia_keywords else []


class SearchManyResult:
    __slots__ = ("response", "grounding")

    def __init__(
        self,
        response: SearchResponse,
        grounding: Tuple[WikipediaGroundingResult, ...] = (),
    ) -> None:
        self.response = response
        self.grounding = grounding


class SearchCoordinator:
    def __init__(
        self,
        *,
        cache: SearchCache,
        fourget_searcher: FourGetSearcher,
        searxng_searcher: SearXNGSearcher,
        serper_searcher: SerperSearcher,
        fourget_enabled: bool = True,
        searxng_enabled: bool = False,
        serper_enabled: bool = False,
    ) -> None:
        self._cache = cache
        self._fourget_searcher = fourget_searcher
        self._searxng_searcher = searxng_searcher
        self._serper_searcher = serper_searcher
        self._fourget_enabled = fourget_enabled
        self._searxng_enabled = searxng_enabled
        self._serper_enabled = serper_enabled

    async def search_many(self, request: SearchManyRequest) -> SearchManyResult:
        if not request.queries:
            raise ValueError("queries is empty")

        search_call_id = uuid.uuid4().hex[:12]

        normalized_queries, _ = normalize_queries(
            queries=request.queries,
        )
        custom_mode = request.provider_mode == "custom"

        plan = build_search_plan(
            queries=normalized_queries,
            mode=request.mode,
            wikipedia_keywords=[] if custom_mode else request.wikipedia_keywords,
        )

        plan_keyword_values = [kw.text for kw in plan.wikipedia_keywords]

        log_event(
            "Wikipedia grounding 调用检查",
            mode=request.mode,
            keyword_count=len(plan.wikipedia_keywords),
            keyword_values=plan_keyword_values,
        )

        total_started = time.monotonic()
        grounding_task: Optional[asyncio.Task[Tuple[WikipediaGroundingResult, ...]]] = (
            None
        )
        grounding_started: Optional[float] = None
        grounding_elapsed_ms = 0
        if plan.wikipedia_keywords:
            grounding_started = time.monotonic()
            grounding_task = asyncio.create_task(
                self._run_wikipedia_grounding_safe(
                    keywords=list(plan.wikipedia_keywords),
                    mode=request.mode,
                )
            )
        else:
            reason = "fast_mode" if request.mode == "fast" else "empty_keywords"
            log_event(
                "Wikipedia grounding 未调用",
                reason=reason,
                mode=request.mode,
            )

        all_variants: List[VariantSearchResponse] = []
        main_search_started = time.monotonic()

        if not custom_mode:
            platform_variant_results: List[VariantSearchResponse] = []
            platform_source = "fourget" if self._fourget_enabled else "searxng"

            if self._fourget_enabled:
                log_event(
                    "搜索源调度 FourGet",
                    search_call_id=search_call_id,
                    variants=len(plan.query_variants),
                    with_images=request.with_images,
                )
                platform_variant_results = await run_fourget_variants(
                    search_call_id=search_call_id,
                    variants=list(plan.query_variants),
                    searcher=self._fourget_searcher,
                    cache=self._cache,
                    with_images=request.with_images,
                )
                all_variants.extend(platform_variant_results)
                log_event(
                    "搜索源 FourGet 完成",
                    search_call_id=search_call_id,
                    results=len(platform_variant_results),
                )
            elif self._searxng_enabled:
                log_event(
                    "搜索源调度 SearXNG",
                    search_call_id=search_call_id,
                    variants=len(plan.query_variants),
                    with_images=request.with_images,
                )
                platform_variant_results = await run_searxng_variants(
                    search_call_id=search_call_id,
                    variants=list(plan.query_variants),
                    searcher=self._searxng_searcher,
                    cache=self._cache,
                    with_images=request.with_images,
                )
                all_variants.extend(platform_variant_results)
                log_event(
                    "搜索源 SearXNG 完成",
                    search_call_id=search_call_id,
                    results=len(platform_variant_results),
                )
            else:
                log_event(
                    "平台搜索源未启用",
                    search_call_id=search_call_id,
                    fourget_enabled=self._fourget_enabled,
                    searxng_enabled=self._searxng_enabled,
                )

            default_provider_calls = select_default_provider_calls(
                mode=request.mode,
                variants=plan.query_variants,
                primary_responses=platform_variant_results,
                serper_enabled=self._serper_enabled,
                primary_provider=platform_source,
            )

            if not default_provider_calls:
                primary_useful = sum(
                    1
                    for r in platform_variant_results
                    for item in r.response.results
                    if item.title.strip() and item.url.strip()
                )
                log_event(
                    "搜索源 Serper 跳过",
                    search_call_id=search_call_id,
                    serper_enabled=self._serper_enabled,
                    mode=request.mode,
                    primary_source=platform_source,
                    primary_useful=primary_useful,
                )

            if default_provider_calls:
                serper_variants = [call.variant for call in default_provider_calls]
                log_event(
                    "搜索源调度 Serper",
                    search_call_id=search_call_id,
                    variants=len(serper_variants),
                    serper_enabled=self._serper_enabled,
                )
                serper_variant_results = await run_serper_variants(
                    variants=serper_variants,
                    searcher=self._serper_searcher,
                    cache=self._cache,
                    with_images=request.with_images,
                )
                all_variants.extend(serper_variant_results)
                log_event(
                    "搜索源 Serper 完成",
                    search_call_id=search_call_id,
                    results=len(serper_variant_results),
                )
        else:
            log_event(
                "custom 搜索模式跳过平台搜索源",
                search_call_id=search_call_id,
                variants=len(plan.query_variants),
            )

        custom_provider_calls = select_custom_provider_calls(
            mode=request.mode,
            variants=plan.query_variants,
            credentials=request.custom_provider_params or (),
            force=custom_mode,
        )

        if custom_mode and not custom_provider_calls:
            raise _custom_not_configured()

        if custom_provider_calls:
            try:
                custom_variant_results = await run_custom_provider_calls(
                    provider_calls=custom_provider_calls,
                    credentials=request.custom_provider_params or (),
                    cache=self._cache,
                    user_id=request.user_id,
                    with_images=request.with_images,
                    strict=custom_mode,
                )
                all_variants.extend(custom_variant_results)
            except CustomSearchProviderUnavailableError:
                raise
            except Exception as e:
                log_fail(
                    "Custom provider recall",
                    repr(e),
                    calls=len(custom_provider_calls),
                )
                if custom_mode:
                    raise _custom_provider_error()

        main_search_elapsed_ms = int((time.monotonic() - main_search_started) * 1000)

        if not all_variants:
            if grounding_task is not None:
                grounding_task.cancel()
                try:
                    await grounding_task
                except asyncio.CancelledError:
                    pass

            total_elapsed_ms = int((time.monotonic() - total_started) * 1000)
            log_event(
                "web_search 调度耗时",
                search_call_id=search_call_id,
                main_search_elapsed_ms=main_search_elapsed_ms,
                grounding_elapsed_ms=grounding_elapsed_ms,
                total_elapsed_ms=total_elapsed_ms,
                grounding_keyword_count=len(plan.wikipedia_keywords),
            )
            queries_str = " | ".join(normalized_queries)
            raise EmptySearchResultError(
                provider="all",
                query=queries_str,
            )

        grounding: Tuple[WikipediaGroundingResult, ...] = ()
        if grounding_task is not None:
            grounding = await grounding_task
            if grounding_started is not None:
                grounding_elapsed_ms = int(
                    (time.monotonic() - grounding_started) * 1000
                )

        all_images = _collect_images(all_variants)
        merged_limit = MERGED_CANDIDATE_LIMIT.get(
            request.mode, MERGED_CANDIDATE_LIMIT["normal"]
        )
        ranked_urls = rank_urls_pipeline(
            variant_responses=all_variants,
            mode=request.mode,
            merged_limit=merged_limit,
            max_urls_per_domain=_DEFAULT_MAX_PER_DOMAIN,
        )

        source_parts = _collect_source_parts(all_variants)
        merged_response = _ranked_urls_to_search_response(
            ranked_urls=ranked_urls,
            query=" | ".join(normalized_queries),
            images=all_images,
            source_parts=source_parts,
        )

        total_elapsed_ms = int((time.monotonic() - total_started) * 1000)
        log_event(
            "web_search 调度耗时",
            search_call_id=search_call_id,
            main_search_elapsed_ms=main_search_elapsed_ms,
            grounding_elapsed_ms=grounding_elapsed_ms,
            total_elapsed_ms=total_elapsed_ms,
            grounding_keyword_count=len(plan.wikipedia_keywords),
        )

        return SearchManyResult(
            response=merged_response,
            grounding=grounding,
        )

    async def _run_wikipedia_grounding_safe(
        self,
        *,
        keywords: List[Any],
        mode: str,
    ) -> Tuple[WikipediaGroundingResult, ...]:
        try:
            grounding = await run_wikipedia_grounding(
                keywords=keywords,
                cache=self._cache,
                mode=mode,
            )
        except Exception as e:
            log_fail(
                "Wikipedia grounding",
                repr(e),
                keywords=keywords,
            )
            return ()

        return tuple(grounding)

    async def close(self) -> None:
        await self._fourget_searcher.close()
        await self._searxng_searcher.close()
        await self._serper_searcher.close()
        await close_wikipedia_grounding_client()
        log_event("SearchCoordinator 关闭")


def _collect_images(all_variants: List[VariantSearchResponse]) -> List[ImageResult]:
    images: List[ImageResult] = []
    for vr in all_variants:
        images.extend(vr.response.images or [])
    return images


def _collect_source_parts(all_variants: List[VariantSearchResponse]) -> Set[str]:
    source_parts: Set[str] = set()
    for vr in all_variants:
        source = vr.response.source
        if source:
            normalized = source.removeprefix("multi:")
            source_parts.update(part for part in normalized.split(",") if part)
    return source_parts


def _ranked_urls_to_search_response(
    *,
    ranked_urls: List[RankedUrlCandidate],
    query: str,
    images: List[ImageResult],
    source_parts: Set[str],
) -> SearchResponse:
    ranked_results = tuple(
        SearchResult(
            title=item.candidate.title,
            url=item.candidate.url,
            snippet=item.candidate.snippet,
        )
        for item in ranked_urls
    )

    source = "multi"
    if source_parts:
        source = "multi:" + ",".join(sorted(source_parts))

    return SearchResponse(
        query=query,
        results=ranked_results,
        images=tuple(images),
        source=source,
    )


def _custom_not_configured() -> CustomSearchProviderUnavailableError:
    return CustomSearchProviderUnavailableError(
        provider="custom",
        public_code=PUBLIC_ERROR_NOT_CONFIGURED,
        status=STATUS_PROVIDER_ERROR,
        last_error_code=ERROR_NOT_CONFIGURED,
        message="Custom provider is not configured.",
    )


def _custom_provider_error() -> CustomSearchProviderUnavailableError:
    return CustomSearchProviderUnavailableError(
        provider="custom",
        public_code=PUBLIC_ERROR_PROVIDER_ERROR,
        status=STATUS_PROVIDER_ERROR,
        last_error_code=ERROR_PROVIDER_ERROR,
        message="Custom provider search failed.",
    )
