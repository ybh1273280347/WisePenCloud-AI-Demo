from __future__ import annotations

import asyncio
from typing import Mapping, Optional

import httpx
from chat.application.web_search.models.common import SearchResponse
from chat.application.web_search.internal.models.serper import (
    SerperSearchRequest,
    map_serper_response,
)
from chat.application.web_search.internal.searcher.base import BaseSearcher
from common.logger import log_event

_SERPER_SOURCE = "serper:google"
_SERPER_SEARCH_ENDPOINT = "/search"
_SERPER_TIMEOUT_SECONDS = 8.0
_SERPER_DEFAULT_NUM = 10
_SERPER_MAX_NUM = 20


class SerperSearchError(Exception):
    pass


class SerperAuthError(SerperSearchError):
    pass


class SerperRateLimitError(SerperSearchError):
    pass


class SerperSearcher(BaseSearcher):
    name = "serper"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float = _SERPER_TIMEOUT_SECONDS,
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
        max_results: int = _SERPER_DEFAULT_NUM,
        with_images: bool = False,
        language: Optional[str] = None,
        source: str = _SERPER_SOURCE,
    ) -> SearchResponse:
        num = max(1, min(max_results, _SERPER_MAX_NUM))
        request = SerperSearchRequest(
            query=query,
            num=num,
            language=language,
        )
        payload = request.to_payload()

        url = f"{self._base_url}{_SERPER_SEARCH_ENDPOINT}"
        headers = {
            "X-API-KEY": self._api_key,
            "Content-Type": "application/json",
        }

        try:
            timeout = httpx.Timeout(
                timeout=self._timeout_seconds,
                connect=min(3.0, self._timeout_seconds),
                read=self._timeout_seconds,
                write=min(3.0, self._timeout_seconds),
                pool=min(3.0, self._timeout_seconds),
            )
            client = await self._get_client(timeout)
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code in (401, 403):
                raise SerperAuthError("Serper authentication failed")

            if response.status_code == 429:
                raise SerperRateLimitError("Serper rate limit exceeded")

            response.raise_for_status()
            data = response.json()

        except SerperSearchError:
            raise
        except httpx.TimeoutException as e:
            raise SerperSearchError("Serper request timed out") from e
        except httpx.HTTPError as e:
            raise SerperSearchError(f"Serper HTTP error: {e}") from e
        except ValueError as e:
            raise SerperSearchError("Serper returned invalid JSON") from e

        if not isinstance(data, Mapping):
            raise SerperSearchError(
                f"Serper returned invalid response type: {type(data).__name__}"
            )

        search_response = map_serper_response(data, query=query, source=source)
        log_event(
            "Serper provider 调用完成",
            query=query,
            result_count=len(search_response.results),
            language=language,
            source=source,
        )

        return search_response

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()
        log_event("SerperSearcher 关闭", closed=client is not None)

    async def _get_client(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client

        async with self._client_lock:
            if self._client is not None and not self._client.is_closed:
                return self._client

            self._client = httpx.AsyncClient(
                timeout=timeout,
                transport=self._transport,
            )
            return self._client
