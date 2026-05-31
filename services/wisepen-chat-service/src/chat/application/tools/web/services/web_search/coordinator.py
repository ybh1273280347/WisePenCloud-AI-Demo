import asyncio
import uuid
from typing import Callable, List

from chat.application.algorithms.hash import stable_hash
from chat.application.tools.web.services.web_search.domain.provider_routing import (
    ProviderCallSelector,
)
from chat.application.tools.web.services.web_search.domain.query_planning import (
    MERGED_CANDIDATE_LIMIT,
    build_search_plan,
)
from chat.application.tools.web.services.web_search.domain.result_ranking import (
    RankedSearchResultCandidate,
    SearchResultRankingPipeline,
)
from chat.application.tools.web.services.web_search.domain.variant_execution import (
    VariantSearchResponse,
)
from chat.application.tools.web.services.web_search.enums import ProviderMode
from chat.application.tools.web.services.web_search.errors import (
    CustomSearchProviderUnavailableError,
    EmptySearchResultError,
)
from chat.application.tools.web.services.web_search.models import (
    SearchManyRequest,
    SearchManyResult,
    SearchResponse,
    SearchResult,
    WikipediaGroundingResult,
)
from chat.application.tools.web.services.web_search.runner.custom import (
    CustomProviderRunner,
)
from chat.application.tools.web.services.web_search.runner.fourget import (
    FourGetSearchRunner,
)
from chat.application.tools.web.services.web_search.runner.serper import (
    SerperSearchRunner,
)
from chat.application.tools.web.services.web_search.runner.wikipedia import (
    WikipediaRunner,
)


class SearchCoordinator:
    """全网多渠道联网搜索核心调度塔。"""

    def __init__(
            self,
            *,
            fourget_runner: FourGetSearchRunner,
            serper_runner: SerperSearchRunner,
            wikipedia_runner: WikipediaRunner,
            custom_runner_factory: Callable[..., CustomProviderRunner],
    ) -> None:
        # 搜索渠道运行器依赖。
        """初始化对象依赖。"""
        self._fourget_runner = fourget_runner
        self._serper_runner = serper_runner
        self._wikipedia_runner = wikipedia_runner
        self._custom_runner_factory = custom_runner_factory

    async def search_many(self, request: SearchManyRequest) -> SearchManyResult:
        """处理当前流程。"""
        is_custom_mode = request.provider_mode == ProviderMode.CUSTOM

        # 搜索计划构造：包含多查询召回变体与背景实体对齐关键词
        plan = build_search_plan(
            mode=request.mode,
            queries=request.queries,
            wikipedia_keywords=request.wikipedia_keywords,
        )

        # 搜索调用标识构造：自定义模式按用户稳定路由，便于自定义渠道链路追踪。
        search_call_id = (
            f"default:{uuid.uuid4().hex[:12]}"
            if not is_custom_mode
            else f"custom_route:{stable_hash(request.user_id)[:8]}"  # type: ignore
        )

        # 维基百科背景对齐并发启动，不阻塞主搜索召回。
        groundings_task = None
        if plan.wikipedia_keywords:
            groundings_task = asyncio.create_task(
                self._wikipedia_runner.run_keywords(
                    search_call_id=search_call_id,
                    keywords=plan.wikipedia_keywords,
                )
            )

        # 渠道调用选择器初始化。
        collected_responses: List[VariantSearchResponse] = []
        selector = ProviderCallSelector(mode=request.mode, variants=plan.query_variants)

        # 平台默认搜索链路：先跑免费/自部署主召回源 fourget，再视结果决定是否补充 serper
        if not is_custom_mode:
            platform_variant_results: List[VariantSearchResponse] = []

            default_calls = selector.default_calls()
            if default_calls:
                platform_variant_results = await self._fourget_runner.run_variants(
                    search_call_id=search_call_id,
                    variants=[call.variant for call in default_calls],
                )
                collected_responses.extend(platform_variant_results)

            supplement_calls = selector.supplement_calls(primary_responses=platform_variant_results)
            if supplement_calls:
                serper_variant_results = await self._serper_runner.run_variants(
                    search_call_id=search_call_id,
                    variants=[call.variant for call in supplement_calls],
                )
                collected_responses.extend(serper_variant_results)

        # 用户自定义搜索链路：由用户配置的 credential 驱动
        else:
            custom_provider_calls = selector.custom_calls(credential=request.custom_provider_credential)  # type: ignore
            if not custom_provider_calls:
                raise CustomSearchProviderUnavailableError(
                    provider="custom",
                    message="Custom provider is not configured.",
                )

            try:
                custom_runner = self._custom_runner_factory(
                    credential=request.custom_provider_credential,
                    user_id=request.user_id,
                )
                custom_results = await custom_runner.run(
                    provider_calls=custom_provider_calls
                )
                collected_responses.extend(custom_results)
            except Exception as e:
                if isinstance(e, CustomSearchProviderUnavailableError):
                    raise
                raise CustomSearchProviderUnavailableError(
                    provider="custom",
                    message="Custom provider search failed.",
                ) from e

        # 零召回熔断：主搜索无结果时，取消已启动的 Wikipedia grounding 任务
        if not collected_responses:
            if groundings_task is not None:
                groundings_task.cancel()
                try:
                    await groundings_task
                except asyncio.CancelledError:
                    pass

            raise EmptySearchResultError(provider="all", queries=request.queries)

        # Wikipedia grounding 收网
        groundings: List[WikipediaGroundingResult] = []
        if groundings_task is not None:
            groundings = await groundings_task

        # 多路召回结果融合与精排：负责合并、去重、排序和域名多样性控制
        merged_limit = MERGED_CANDIDATE_LIMIT[request.mode]
        search_result_ranking_pipeline = SearchResultRankingPipeline(
            mode=request.mode,
            merged_limit=merged_limit,
            max_urls_per_domain=2,
        )

        ranked_candidates: List[RankedSearchResultCandidate] = search_result_ranking_pipeline.run(variant_responses=collected_responses)

        # 渠道来源汇总。
        sources = [vr.response.source for vr in collected_responses if vr.response.source]
        combined_source = " | ".join(sources) if sources else ""

        # 排序候选转换为标准搜索结果。
        ranked_results = [
            SearchResult(
                title=item.candidate.title,
                url=item.candidate.url,
                snippet=item.candidate.snippet,
            )
            for item in ranked_candidates
        ]

        # 最终搜索响应打包。
        merged_response = SearchResponse(
            query=" | ".join(request.queries),
            results=ranked_results,
            source=combined_source,
        )

        return SearchManyResult(response=merged_response, groundings=groundings)
