from __future__ import annotations

import asyncio
from typing import Mapping, Optional

import httpx
from chat.application.web_search.errors import (
    SearchProviderError,
    SearchRateLimitError,
    SearchTimeoutError,
)
from chat.application.web_search.models.brave import (
    BraveSearchRequest,
    map_brave_response,
)
from chat.application.web_search.models.common import SearchResponse
from chat.application.web_search.searcher.base import BaseSearcher
from common.logger import log_event

_BRAVE_SOURCE = "custom:brave"
_BRAVE_ENDPOINT = "/res/v1/web/search"
_BRAVE_TIMEOUT_SECONDS = 8.0
_BRAVE_DEFAULT_COUNT = 10
_BRAVE_MAX_COUNT = 20


class BraveSearcher(BaseSearcher):
    name = "brave"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float = _BRAVE_TIMEOUT_SECONDS,
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
        max_results: int = _BRAVE_DEFAULT_COUNT,
        with_images: bool = False,
        language: Optional[str] = None,
        source: str = _BRAVE_SOURCE,
    ) -> SearchResponse:
        request = BraveSearchRequest(
            query=query,
            count=max(1, min(max_results, _BRAVE_MAX_COUNT)),
            language=language,
        )
        params = request.to_params()

        headers = {
            "X-Subscription-Token": self._api_key,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }
        url = f"{self._base_url}{_BRAVE_ENDPOINT}"

        try:
            client = await self._get_client()
            response = await client.get(url, headers=headers, params=params)

            if response.status_code in (401, 403):
                raise SearchProviderError("brave", "authentication failed")

            if response.status_code == 429:
                raise SearchRateLimitError("brave", "quota or rate limit exceeded")

            response.raise_for_status()
            data = response.json()

        except (SearchProviderError, SearchRateLimitError):
            raise
        except httpx.TimeoutException as e:
            raise SearchTimeoutError(
                "brave", query=query, timeout=self._timeout_seconds
            ) from e
        except httpx.HTTPError as e:
            raise SearchProviderError("brave", f"HTTP error: query={query!r}") from e
        except ValueError as e:
            raise SearchProviderError("brave", f"invalid JSON: query={query!r}") from e

        if not isinstance(data, Mapping):
            raise SearchProviderError(
                "brave",
                f"invalid response type: type={type(data).__name__}, query={query!r}",
            )

        search_response = map_brave_response(data, query=query, source=source)
        log_event(
            "Brave custom provider 调用完成",
            result_count=len(search_response.results),
            source=source,
        )

        return search_response

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()
        log_event("BraveSearcher 关闭", closed=client is not None)

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
