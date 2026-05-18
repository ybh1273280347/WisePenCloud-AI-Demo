import asyncio
from typing import Any, Optional

import httpx
from chat.application.web_search.errors import (
    SearchProviderError,
    SearchRateLimitError,
    SearchTimeoutError,
)
from chat.application.web_search.models.common import SearchResponse
from chat.application.web_search.internal.models.tavily import (
    TavilySearchRequest,
    map_tavily_response,
)
from chat.application.web_search.internal.searcher.base import BaseSearcher
from common.logger import log_event

_TAVILY_TIMEOUT = 10.0


class TavilySearcher(BaseSearcher):
    name = "tavily"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str,
        timeout: float = _TAVILY_TIMEOUT,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        api_key = api_key.strip()

        self._api_key = api_key
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
        source: str = "custom:tavily",
    ) -> SearchResponse:
        request = TavilySearchRequest(
            query=query,
            max_results=max_results,
            with_images=False,
        )

        payload = request.to_payload()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/search"

        try:
            client = await self._get_client()
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code in (401, 403):
                raise SearchProviderError("tavily", "authentication failed")

            if response.status_code == 429:
                raise SearchRateLimitError("tavily", "quota or rate limit exceeded")

            response.raise_for_status()
            raw_response: Any = response.json()

        except (SearchProviderError, SearchRateLimitError):
            raise
        except httpx.TimeoutException as e:
            raise SearchTimeoutError(
                "tavily", query=query, timeout=self._timeout
            ) from e
        except httpx.HTTPError as e:
            raise SearchProviderError(
                "tavily",
                f"HTTP error: query={query!r}, error={type(e).__name__}: {e}",
            ) from e
        except ValueError as e:
            raise SearchProviderError("tavily", f"invalid JSON: query={query!r}") from e

        if not isinstance(raw_response, dict):
            raise SearchProviderError(
                "tavily",
                f"invalid response type: type={type(raw_response).__name__}, query={query!r}",
            )

        response = map_tavily_response(
            raw_response, max_results=max_results
        ).with_source(source)
        log_event(
            "Tavily custom provider 调用完成",
            result_count=len(response.results),
            source=source,
        )
        return response

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()
        log_event("TavilySearcher 关闭", closed=client is not None)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client

        async with self._client_lock:
            if self._client is not None and not self._client.is_closed:
                return self._client

            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            )
            return self._client
