import asyncio
from abc import ABC, abstractmethod
from typing import List, Optional, Sequence

from chat.application.algorithms.hash import stable_hash
from chat.application.tools.web.services.web_search.cache import (
    SearchCache,
    make_search_cache_descriptor,
)
from chat.application.tools.web.services.web_search.domain.query_planning import QueryVariant
from chat.application.tools.web.services.web_search.domain.variant_execution import VariantSearchResponse
from chat.application.tools.web.services.web_search.enums import ProviderMode, SearchPurpose, SearcherName
from chat.application.tools.web.services.web_search.searcher.base import WebSearcher
from chat.application.tools.web.services.web_search.utils.results import has_response_content
from common.logger import log_fail


class SearchRunner(ABC):
    """检索执行器顶层接口。

    定义多变体并发调度的刚性契约，所有执行器实现此接口。
    """

    @abstractmethod
    async def run_variants(
        self,
        *,
        search_call_id: str,
        variants: Sequence[QueryVariant],
    ) -> List[VariantSearchResponse]:
        """并发调度多条检索变体。

        Args:
            search_call_id: 单次搜索调用的追踪 ID。
            variants: 待执行的检索变体列表。

        Returns:
            每个变体对应的检索结果列表（异常变体自动过滤）。
        """
        ...


class BaseSearchRunner(SearchRunner):
    """检索执行器通用行为模板。

    封装缓存拦截、多路并发平推、异常隔离防线。
    子类只需提供 searcher 实例即可工作。
    """

    def __init__(
        self,
        *,
        searcher: WebSearcher,
        cache: SearchCache,
        purpose: SearchPurpose = SearchPurpose.RECALL,
        provider_mode: ProviderMode = ProviderMode.DEFAULT,
        user_id: Optional[str] = None
    ) -> None:
        self._searcher = searcher
        self._cache = cache
        self._purpose = purpose
        self._provider_mode = provider_mode
        self._user_id = user_id
        self._user_id_hash = stable_hash(user_id) if user_id else None

    async def run_variants(
        self,
        *,
        search_call_id: str,
        variants: Sequence[QueryVariant],
    ) -> List[VariantSearchResponse]:
        """模板方法：并发调度所有变体，过滤异常结果。

        Args:
            search_call_id: 单次搜索调用的追踪 ID。
            variants: 待执行的检索变体列表。

        Returns:
            成功返回的 VariantSearchResponse 列表（异常/空结果变体自动过滤）。
        """
        if not variants:
            return []

        raw_results = await asyncio.gather(
            *(
                self._run_one_variant(
                    variant=variant,
                    search_call_id=search_call_id,
                )
                for variant in variants
            ),
            return_exceptions=True,
        )

        return [
            item
            for item in raw_results
            if isinstance(item, VariantSearchResponse)
        ]

    async def _run_one_variant(
        self,
        variant: QueryVariant,
        *,
        search_call_id: str,
    ) -> Optional[VariantSearchResponse]:
        """执行单条检索变体的完整生命周期：缓存命中拦截 → 搜索 → 缓存回写。

        Args:
            variant: 单条检索变体。
            search_call_id: 搜索调用追踪 ID。

        Returns:
            缓存命中或搜索成功时返回 VariantSearchResponse，异常/空结果返回 None。
        """
        provider_name: SearcherName = self._searcher.name

        desc = make_search_cache_descriptor(
            source=provider_name,
            query=variant.text,
            max_results=variant.max_results,
            purpose=self._purpose,
            provider_mode=self._provider_mode,
            user_id=self._user_id,
        )

        cached = self._cache.get(desc)
        if cached is not None:
            return VariantSearchResponse(
                variant=variant,
                response=cached.response,
                cache_hit=True,
            )

        try:
            response = await self._searcher.search(
                query=variant.text,
                max_results=variant.max_results,
            )
        except Exception as e:
            log_fail(
                f"{provider_name.value} variant 搜索",
                repr(e),
                search_call_id=search_call_id,
                query=variant.text,
                role=variant.role.value,
            )
            return None

        if not has_response_content(response):
            return None

        self._cache.set(desc, response)

        return VariantSearchResponse(
            variant=variant,
            response=response,
            cache_hit=False,
        )