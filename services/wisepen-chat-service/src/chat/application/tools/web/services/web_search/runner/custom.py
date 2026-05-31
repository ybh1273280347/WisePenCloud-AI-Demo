from __future__ import annotations

from typing import List, Optional, Sequence

import httpx

from chat.application.tools.web.services.web_search.cache import SearchCache
from chat.application.tools.web.services.web_search.domain.provider_routing import ProviderCall
from chat.application.tools.web.services.web_search.domain.variant_execution import VariantSearchResponse
from chat.application.tools.web.services.web_search.enums import ProviderMode, SearchPurpose, SearcherName
from chat.application.tools.web.services.web_search.errors import CustomSearchProviderUnavailableError
from chat.application.tools.web.services.web_search.models import CustomProviderCredential
from chat.application.tools.web.services.web_search.runner.base import BaseSearchRunner
from chat.application.tools.web.services.web_search.searcher import (
    AnySearchSearcher, BraveSearcher, ExaSearcher, PerplexitySearcher,
    SerpApiSearcher, CustomSerperSearcher, TavilySearcher, WebSearcher,
)
from chat.application.tools.web.services.web_search.utils.results import has_response_content
from common.logger import log_ok

_VERIFY_QUERY_TEXT = "OpenAI"


class CustomSearcherFactory:
    """延迟加载单例工厂"""

    def __init__(self, credential: CustomProviderCredential, client: httpx.AsyncClient) -> None:
        from chat.core.config.app_settings import settings
        self._settings = settings
        self._credential = credential
        self._client = client
        self._instance: Optional[WebSearcher] = None

    def get(self, provider_name: SearcherName) -> Optional[WebSearcher]:
        """懒加载当前激活的单一 Searcher 实例"""
        if provider_name != self._credential.provider:
            return None
        if self._instance is not None:
            return self._instance

        searcher: Optional[WebSearcher] = None
        raw_key = self._credential.api_key

        # 兼容无需 api_key 的渠道，如果不传 api_key，直接报错，是符合预期的，这里不做类型检查
        if provider_name == SearcherName.CUSTOM_SERPER:
            searcher = CustomSerperSearcher(client=self._client, api_key=raw_key,
                                            base_url=self._settings.SERPER_BASE_URL)
        elif provider_name == SearcherName.TAVILY:
            searcher = TavilySearcher(client=self._client, api_key=raw_key, base_url=self._settings.TAVILY_BASE_URL)
        elif provider_name == SearcherName.BRAVE:
            searcher = BraveSearcher(client=self._client, api_key=raw_key,
                                     base_url=self._settings.BRAVE_SEARCH_BASE_URL)
        elif provider_name == SearcherName.SERPAPI:
            searcher = SerpApiSearcher(client=self._client, api_key=raw_key, base_url=self._settings.SERPAPI_BASE_URL)
        elif provider_name == SearcherName.EXA:
            searcher = ExaSearcher(client=self._client, api_key=raw_key, base_url=self._settings.EXA_BASE_URL)
        elif provider_name == SearcherName.PERPLEXITY:
            searcher = PerplexitySearcher(client=self._client, api_key=raw_key,
                                          base_url=self._settings.PERPLEXITY_BASE_URL)
        elif provider_name == SearcherName.ANYSEARCH:
            searcher = AnySearchSearcher(client=self._client, api_key=raw_key,
                                         base_url=self._settings.ANYSEARCH_BASE_URL)

        if searcher is not None:
            self._instance = searcher

        return searcher


class CustomProviderRunner(BaseSearchRunner):
    """多路变体并发执行器（基于用户维度隔离）"""

    def __init__(
            self,
            client: httpx.AsyncClient,
            *,
            credential: CustomProviderCredential,
            cache: SearchCache,
            user_id: str,
    ) -> None:
        self._client = client
        self._factory = CustomSearcherFactory(credential, client=self._client)
        self._provider = credential.provider  # 留存一下当前渠道名称，方便实例方法直接用

        active_searcher = self._factory.get(self._provider)

        # 没有传入 searcher 说明没有有效searcher，报错符合预期
        super().__init__(
            searcher=active_searcher,   # type: ignore
            cache=cache,
            purpose=SearchPurpose.RECALL,
            provider_mode=ProviderMode.CUSTOM,
            user_id=user_id,
        )

    async def verify(self) -> None:
        """针对当前用户绑定实例化的 credential 进行闭环热连通性校验"""
        searcher = self._factory.get(self._provider)

        if searcher is None:
            raise CustomSearchProviderUnavailableError(
                provider=self._provider,
                message=f"Verify failed: unsupported provider {self._provider.value}",
            )

        try:
            response = await searcher.search(query=_VERIFY_QUERY_TEXT, max_results=1)
            if not has_response_content(response):
                raise ValueError("Provider returned empty payload.")
        except Exception as e:
            raise CustomSearchProviderUnavailableError(
                provider=self._provider,
                message=f"Hot-verification to provider [{self._provider.value}] failed: {e}",
            ) from e

    async def run(
            self,
            *,
            provider_calls: Sequence[ProviderCall],
    ) -> List[VariantSearchResponse]:
        """多路变体并发调度入口"""
        if not provider_calls:
            return []

        variants = [call.variant for call in provider_calls]

        results = await self.run_variants(
            search_call_id=f"custom:"
                           f"{self._searcher.name.value}:"
                           f"{self._user_id_hash[:8]}",
            variants=variants
        )

        log_ok("Custom provider 搜索", calls=len(provider_calls), results=len(results), user_id=self._user_id)
        return results

