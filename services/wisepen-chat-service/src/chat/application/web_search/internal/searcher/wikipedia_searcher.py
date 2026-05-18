import asyncio
import re
from collections.abc import Mapping
from typing import Any, Optional
from urllib.parse import quote

import httpx

from chat.application.web_search.errors import SearchProviderError, SearchTimeoutError
from chat.application.web_search.internal.models.helpers import is_valid_result
from chat.application.web_search.internal.planning.planner import detect_query_language
from chat.application.web_search.internal.searcher.base import BaseSearcher
from chat.application.web_search.models.common import SearchResponse, SearchResult
from chat.core.config.app_settings import settings
from common.logger import log_event

_WIKIPEDIA_TIMEOUT = 5.0
_WIKIPEDIA_CONCURRENCY = 4
_WIKIPEDIA_MAX_RESULTS = 3
_WIKIPEDIA_USER_AGENT = "WisePenCloud-AI web_search/1.0 (contact@example.com)"


def _wikipedia_base_url(language: str) -> str:
    return settings.WIKIPEDIA_BASE_URL_TEMPLATE.format(language=language).rstrip("/")


class WikipediaClient(BaseSearcher):
    name = "wikipedia"

    def __init__(
        self,
        *,
        timeout: float = _WIKIPEDIA_TIMEOUT,
        max_results: int = _WIKIPEDIA_MAX_RESULTS,
        user_agent: str = _WIKIPEDIA_USER_AGENT,
        concurrency: int = _WIKIPEDIA_CONCURRENCY,
    ) -> None:
        self._timeout = timeout
        self._max_results = max_results
        self._user_agent = user_agent
        self._semaphore = asyncio.Semaphore(concurrency)
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()

    async def search(
        self,
        query: str,
        *,
        max_results: int = _WIKIPEDIA_MAX_RESULTS,
        with_images: bool = False,
    ) -> SearchResponse:
        language = detect_query_language(query)
        limit = min(max_results, self._max_results)
        endpoint = f"{_wikipedia_base_url(language)}/w/rest.php/v1/search/page"

        headers = {
            "User-Agent": self._user_agent,
            "Api-User-Agent": self._user_agent,
            "Accept": "application/json",
        }
        params = {
            "q": query,
            "limit": str(limit),
        }

        async with self._semaphore:
            try:
                client = await self._get_client()
                response = await client.get(
                    endpoint,
                    params=params,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()

            except httpx.TimeoutException as e:
                raise SearchTimeoutError(
                    provider="wikipedia",
                    query=query,
                    timeout=self._timeout,
                ) from e

            except httpx.HTTPStatusError as e:
                raise SearchProviderError(
                    "wikipedia",
                    f"HTTP error: query={query!r}, language={language}, "
                    f"status={e.response.status_code}, body={e.response.text[:500]!r}",
                ) from e

            except httpx.RequestError as e:
                raise SearchProviderError(
                    "wikipedia",
                    f"request error: query={query!r}, language={language}, "
                    f"error={type(e).__name__}: {e}",
                ) from e

            except ValueError as e:
                raise SearchProviderError(
                    "wikipedia",
                    f"invalid JSON: query={query!r}, language={language}, body={response.text[:500]!r}",
                ) from e

        return map_wikipedia_response(
            query=query,
            language=language,
            data=data,
        )

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()
        log_event("WikipediaClient 关闭", closed=client is not None)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client

        async with self._client_lock:
            if self._client is not None and not self._client.is_closed:
                return self._client

            self._client = httpx.AsyncClient(timeout=self._timeout)
            return self._client


def map_wikipedia_response(
    *,
    query: str,
    language: str,
    data: Mapping[str, Any],
) -> SearchResponse:
    results = tuple(
        result
        for item in data["pages"]
        for result in (_map_wikipedia_page(language, item),)
        if is_valid_result(result)
    )

    return SearchResponse(
        query=query,
        results=results,
        source=f"wikipedia:{language}",
    )


def _map_wikipedia_page(
    language: str,
    item: Mapping[str, Any],
) -> SearchResult:
    title = str(item.get("title") or item.get("key") or "")
    excerpt = _strip_html(str(item.get("excerpt") or ""))
    description = str(item.get("description") or "")
    snippet = excerpt or description

    key = str(item.get("key") or title)
    encoded_key = quote(key.replace(" ", "_"), safe="/:_-()")
    url = f"{_wikipedia_base_url(language)}/wiki/{encoded_key}"

    return SearchResult(
        title=title,
        url=url,
        snippet=snippet,
    )


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).strip()
