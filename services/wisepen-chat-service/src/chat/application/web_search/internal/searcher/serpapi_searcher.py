from __future__ import annotations

import asyncio
from typing import Mapping, Optional

import httpx
from chat.application.web_search.errors import (
    SearchProviderError,
    SearchRateLimitError,
    SearchTimeoutError,
)
from chat.application.web_search.models.common import SearchResponse
from chat.application.web_search.internal.models.serpapi import (
    SerpApiSearchRequest,
    map_serpapi_response,
)
from chat.application.web_search.internal.searcher.base import BaseSearcher
from common.logger import log_event

_SERPAPI_SOURCE = "custom:serpapi"
_SERPAPI_TIMEOUT_SECONDS = 8.0
_SERPAPI_DEFAULT_NUM = 10
_SERPAPI_MAX_NUM = 20


class SerpApiSearcher(BaseSearcher):
    name = "serpapi"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float = _SERPAPI_TIMEOUT_SECONDS,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()

    async def search(
        self,
        query: str,
        *,
        max_results: int = _SERPAPI_DEFAULT_NUM,
        with_images: bool = False,
        language: Optional[str] = None,
        source: str = _SERPAPI_SOURCE,
    ) -> SearchResponse:
        request = SerpApiSearchRequest(
            query=query,
            api_key=self._api_key,
            num=max(1, min(max_results, _SERPAPI_MAX_NUM)),
            language=language,
        )
        params = request.to_params()

        try:
            client = await self._get_client()
            response = await client.get(f"{self._base_url}/search", params=params)

            if response.status_code in (401, 403):
                raise SearchProviderError("serpapi", "authentication failed")

            if response.status_code == 429:
                raise SearchRateLimitError("serpapi", "quota or rate limit exceeded")

            response.raise_for_status()
            data = response.json()

        except (SearchProviderError, SearchRateLimitError):
            raise
        except httpx.TimeoutException as e:
            raise SearchTimeoutError(
                "serpapi", query=query, timeout=self._timeout_seconds
            ) from e
        except httpx.HTTPError as e:
            raise SearchProviderError("serpapi", f"HTTP error: query={query!r}") from e
        except ValueError as e:
            raise SearchProviderError(
                "serpapi", f"invalid JSON: query={query!r}"
            ) from e

        if not isinstance(data, Mapping):
            raise SearchProviderError(
                "serpapi",
                f"invalid response type: type={type(data).__name__}, query={query!r}",
            )

        search_response = map_serpapi_response(data, query=query, source=source)
        log_event(
            "SerpAPI custom provider 调用完成",
            result_count=len(search_response.results),
            source=source,
        )

        return search_response

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()
        log_event("SerpApiSearcher 关闭", closed=client is not None)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client

        async with self._client_lock:
            if self._client is not None and not self._client.is_closed:
                return self._client

            self._client = httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            )
            return self._client
