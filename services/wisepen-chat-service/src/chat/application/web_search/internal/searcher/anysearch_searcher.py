from __future__ import annotations

import asyncio
from typing import Mapping, Optional

import httpx

from chat.application.web_search.errors import (
    SearchProviderError,
    SearchRateLimitError,
    SearchTimeoutError,
)
from chat.application.web_search.internal.models.anysearch import (
    AnySearchRequest,
    map_anysearch_response,
)
from chat.application.web_search.internal.searcher.base import BaseSearcher
from chat.application.web_search.models.common import SearchResponse
from common.logger import log_event

_ANYSEARCH_SOURCE = "custom:anysearch"
_ANYSEARCH_ENDPOINT = "/v1/search"
_ANYSEARCH_BASE_URL = "https://api.anysearch.com"
_ANYSEARCH_TIMEOUT_SECONDS = 8.0
_ANYSEARCH_DEFAULT_MAX_RESULTS = 10
_ANYSEARCH_MAX_RESULTS = 100


class AnySearchSearcher(BaseSearcher):
    name = "anysearch"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: str = _ANYSEARCH_BASE_URL,
        timeout_seconds: float = _ANYSEARCH_TIMEOUT_SECONDS,
        zone: Optional[str] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._api_key = api_key.strip() if isinstance(api_key, str) else None
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._zone = zone
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()

    async def search(
        self,
        query: str,
        *,
        max_results: int = _ANYSEARCH_DEFAULT_MAX_RESULTS,
        with_images: bool = False,
        language: Optional[str] = None,
        source: str = _ANYSEARCH_SOURCE,
    ) -> SearchResponse:
        request = AnySearchRequest(
            query=query,
            max_results=max(1, min(max_results, _ANYSEARCH_MAX_RESULTS)),
            language=language,
            zone=self._zone,
        )
        payload = request.to_payload()
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        url = f"{self._base_url}{_ANYSEARCH_ENDPOINT}"

        try:
            client = await self._get_client()
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code in (401, 403):
                raise SearchProviderError("anysearch", "authentication failed")
            if response.status_code == 402:
                raise SearchProviderError("anysearch", "quota exhausted")
            if response.status_code == 429:
                raise SearchRateLimitError(
                    "anysearch",
                    "quota or rate limit exceeded",
                )

            response.raise_for_status()
            data = response.json()

        except (SearchProviderError, SearchRateLimitError):
            raise
        except httpx.TimeoutException as e:
            raise SearchTimeoutError(
                "anysearch",
                query=query,
                timeout=self._timeout_seconds,
            ) from e
        except httpx.HTTPError as e:
            raise SearchProviderError(
                "anysearch",
                f"HTTP error: query={query!r}",
            ) from e
        except ValueError as e:
            raise SearchProviderError(
                "anysearch",
                f"invalid JSON: query={query!r}",
            ) from e

        if not isinstance(data, Mapping):
            raise SearchProviderError(
                "anysearch",
                f"invalid response type: type={type(data).__name__}, query={query!r}",
            )

        search_response = map_anysearch_response(data, query=query, source=source)
        log_event(
            "AnySearch custom provider 调用完成",
            result_count=len(search_response.results),
            source=source,
            with_images=with_images,
        )

        return search_response

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()
        log_event("AnySearchSearcher 关闭", closed=client is not None)

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
