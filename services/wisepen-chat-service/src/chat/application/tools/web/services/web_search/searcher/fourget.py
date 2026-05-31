from __future__ import annotations

import asyncio

import httpx

from chat.application.tools.web.services.web_search.enums import SearcherName
from chat.application.tools.web.services.web_search.errors import (
    SearchProviderError,
    SearchProviderTransientError,
)
from chat.application.tools.web.services.web_search.models import SearchResponse
from chat.application.tools.web.services.web_search.schemas.fourget import (
    FourGetSearchRequest,
    map_fourget_response,
)
from chat.application.tools.web.services.web_search.utils.http_client import fetch_search_json
from common.logger import log_event
from .base import WebSearcher

FOURGET_ENDPOINT = "/api/v1/web"


class FourGetSearcher(WebSearcher):
    def __init__(
            self,
            client: httpx.AsyncClient,
            *,
            base_url: str,
            user_agent: str = "WisePenCloud-AI web_search/1.0",
            timeout: float = 8.0,
            scraper: str = "ddg",
            max_concurrency: int = 5,
    ) -> None:
        self._request_url = f"{base_url.rstrip('/')}{FOURGET_ENDPOINT}"
        self._client = client
        self._timeout = timeout
        self._scraper = scraper
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._headers = {
            "User-Agent": user_agent,
            "Accept": "application/json",
        }

    @property
    def name(self) -> SearcherName:
        return SearcherName.FOURGET

    async def search(
            self,
            query: str,
            *,
            max_results: int = 10,
    ) -> SearchResponse:
        request = FourGetSearchRequest(query=query, scraper=self._scraper)

        data = await fetch_search_json(
            client=self._client,
            url=self._request_url,
            params=request.to_params(),
            query=query,
            timeout_seconds=self._timeout,
            semaphore=self._semaphore,
            provider_name=self.name.value,
            headers=self._headers,
        )

        if not isinstance(data, dict):
            log_event(
                "FourGet 返回了畸形非字典结构",
                data_type=type(data).__name__,
                query=query
            )

            return SearchResponse(
                query=query,
                results=[],
                source=SearcherName.FOURGET,
            )

        # 4get 特有业务状态码检查
        if data.get("status", "ok") != "ok":
            raise SearchProviderError(
                provider=self.name.value,
                status_code=200,
                reason=f"provider_status_error: status={data.get('status')}",
            )

        response = map_fourget_response(
            data,
            query=query,
            scraper=self._scraper,
            max_results=max_results,
        )

        # 空结果抛出可重试异常（上游无结果，非调用错误）
        if not response.results:
            raise SearchProviderTransientError(
                provider=self.name.value,
                queries=[query],
                reason="4get empty_result returned from upstream source",
            )

        return response