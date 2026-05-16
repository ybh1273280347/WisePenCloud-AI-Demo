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
from chat.application.web_search.models.exa import ExaSearchRequest, map_exa_response
from chat.application.web_search.searcher.base import BaseSearcher
from common.logger import log_event

_EXA_SOURCE = "custom:exa"
_EXA_TIMEOUT_SECONDS = 8.0
_EXA_DEFAULT_NUM_RESULTS = 10
_EXA_MAX_NUM_RESULTS = 20


class ExaSearcher(BaseSearcher):
    name = "exa"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float = _EXA_TIMEOUT_SECONDS,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()

    async def search(
        self,
        query: str,
        *,
        max_results: int = _EXA_DEFAULT_NUM_RESULTS,
        with_images: bool = False,
        source: str = _EXA_SOURCE,
    ) -> SearchResponse:
        request = ExaSearchRequest(
            query=query,
            num_results=max(1, min(max_results, _EXA_MAX_NUM_RESULTS)),
        )
        payload = request.to_payload()
        headers = {
            "x-api-key": self._api_key,
            "Content-Type": "application/json",
        }

        try:
            client = await self._get_client()
            response = await client.post(
                f"{self._base_url}/search", headers=headers, json=payload
            )

            if response.status_code in (401, 403):
                raise SearchProviderError("exa", "authentication failed")

            if response.status_code == 429:
                raise SearchRateLimitError("exa", "quota or rate limit exceeded")

            response.raise_for_status()
            data = response.json()

        except (SearchProviderError, SearchRateLimitError):
            raise
        except httpx.TimeoutException as e:
            raise SearchTimeoutError(
                "exa", query=query, timeout=self._timeout_seconds
            ) from e
        except httpx.HTTPError as e:
            raise SearchProviderError("exa", f"HTTP error: query={query!r}") from e
        except ValueError as e:
            raise SearchProviderError("exa", f"invalid JSON: query={query!r}") from e

        if not isinstance(data, Mapping):
            raise SearchProviderError(
                "exa",
                f"invalid response type: type={type(data).__name__}, query={query!r}",
            )

        search_response = map_exa_response(data, query=query, source=source)
        log_event(
            "Exa custom provider 调用完成",
            result_count=len(search_response.results),
            source=source,
        )

        return search_response

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()
        log_event("ExaSearcher 关闭", closed=client is not None)

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
