import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import httpx
from chat.application.web_search.errors import (
    SearchProviderError,
    SearchProviderTransientError,
    SearchTimeoutError,
)
from chat.application.web_search.models.common import SearchResponse
from chat.application.web_search.internal.models.searxng import (
    SearXNGSearchRequest,
    map_searxng_response,
    merge_search_responses,
)
from chat.application.web_search.internal.searcher.base import BaseSearcher
from common.logger import log_event, log_fail

_SEARXNG_TIMEOUT = 10.0
_SEARXNG_LANGUAGE = ""
_SEARXNG_SAFESEARCH = 1
_SEARXNG_MAX_CONCURRENCY = 1
_SEARXNG_MAX_RETRIES = 2
_SEARXNG_RETRY_BACKOFF_SECONDS = 0.4


@dataclass(frozen=True, slots=True)
class SearXNGHTTPResult:
    data: Dict[str, Any]
    http_status: int
    content_type: str


@dataclass
class SearXNGSearchContext:
    params: Dict[str, Any]
    timeout: float
    max_retries: int
    search_call_id: str = ""
    task_key: str = ""

    def log_attempt(
        self,
        raw: SearXNGHTTPResult,
        data: Dict[str, Any],
        response: SearchResponse,
        *,
        images_only: bool,
        retryable_empty: bool,
        attempt: int,
    ) -> None:
        _log_searxng_call(
            params=self.params,
            timeout=self.timeout,
            http_status=raw.http_status,
            content_type=raw.content_type,
            results=len(response.results),
            images=len(response.images),
            unresponsive_engines=data.get("unresponsive_engines") or (),
            error_type=self._error_type(
                response,
                images_only=images_only,
                retryable_empty=retryable_empty,
            ),
            attempt=attempt,
            max_retries=self.max_retries,
            search_call_id=self.search_call_id,
            task_key=self.task_key,
        )

    def _error_type(
        self,
        response: SearchResponse,
        *,
        images_only: bool,
        retryable_empty: bool,
    ) -> str:
        if retryable_empty:
            return "retryable_empty_result"

        has_payload = bool(response.images) if images_only else bool(response.results)
        return "ok" if has_payload else "empty_result"


@dataclass
class SearXNGCallContext:
    params: Dict[str, Any]
    timeout: float
    search_call_id: str = ""
    task_key: str = ""
    response: Optional[httpx.Response] = None

    @property
    def http_status(self) -> Optional[int]:
        return self.response.status_code if self.response is not None else None

    @property
    def content_type(self) -> str:
        if self.response is None:
            return ""

        return self.response.headers.get("content-type", "")

    @property
    def raw_body_sample(self) -> str:
        if self.response is None:
            return ""

        return self.response.text[:500]

    def log_timeout(self) -> None:
        self.log_failure("timeout")

    def log_http_error(self) -> None:
        self.log_failure("http_error")

    def log_json_parse_error(self) -> None:
        self.log_failure("json_parse_error")

    def log_connection_error(self) -> None:
        self.log_failure("connection_error")

    def log_adapter_error(self) -> None:
        self.log_failure("adapter_error")

    def log_failure(self, error_type: str) -> None:
        _log_searxng_call(
            params=self.params,
            timeout=self.timeout,
            http_status=self.http_status,
            content_type=self.content_type,
            error_type=error_type,
            raw_body_sample=self.raw_body_sample,
            search_call_id=self.search_call_id,
            task_key=self.task_key,
        )


class SearXNGSearcher(BaseSearcher):
    name = "searxng"

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = _SEARXNG_TIMEOUT,
        safesearch: Optional[int] = _SEARXNG_SAFESEARCH,
        web_category: str = "general",
        image_category: str = "images",
        max_concurrency: int = _SEARXNG_MAX_CONCURRENCY,
        max_retries: int = _SEARXNG_MAX_RETRIES,
        retry_backoff_seconds: float = _SEARXNG_RETRY_BACKOFF_SECONDS,
    ) -> None:
        base_url = base_url.rstrip("/")

        self._base_url = base_url
        self._timeout = timeout
        self._safesearch = safesearch
        self._web_category = web_category
        self._image_category = image_category
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
        engines: Optional[Tuple[str, ...]] = None,
        language: Optional[str] = None,
        search_call_id: str = "",
        task_key: str = "",
    ) -> SearchResponse:
        if not with_images:
            return await self._search_by_category(
                query=query,
                max_results=max_results,
                category=self._web_category,
                images_only=False,
                engines=engines,
                language=language,
                search_call_id=search_call_id,
                task_key=task_key,
            )

        web_result, image_result = await asyncio.gather(
            self._search_by_category(
                query=query,
                max_results=max_results,
                category=self._web_category,
                images_only=False,
                engines=engines,
                language=language,
                search_call_id=search_call_id,
                task_key=f"{task_key}|cat=web",
            ),
            self._search_by_category(
                query=query,
                max_results=max_results,
                category=self._image_category,
                images_only=True,
                engines=engines,
                language=language,
                search_call_id=search_call_id,
                task_key=f"{task_key}|cat=img",
            ),
            return_exceptions=True,
        )

        if isinstance(web_result, Exception):
            raise web_result

        if isinstance(image_result, Exception):
            log_fail(
                "SearXNG 图片搜索",
                repr(image_result),
                query=query,
                max_results=max_results,
            )
            return web_result

        return merge_search_responses(web_result, image_result)

    async def _search_by_category(
        self,
        *,
        query: str,
        max_results: int,
        category: str,
        images_only: bool,
        engines: Optional[Tuple[str, ...]] = None,
        language: Optional[str] = None,
        search_call_id: str = "",
        task_key: str = "",
    ) -> SearchResponse:
        request = SearXNGSearchRequest(
            query=query,
            category=category,
            engines=list(engines) if engines else None,
            language=language,
            safesearch=self._safesearch,
        )

        return await self._search_with_retries(
            query=query,
            max_results=max_results,
            params=request.to_params(),
            images_only=images_only,
            search_call_id=search_call_id,
            task_key=task_key,
        )

    async def _search_with_retries(
        self,
        *,
        query: str,
        max_results: int,
        params: Dict[str, Any],
        images_only: bool,
        search_call_id: str = "",
        task_key: str = "",
    ) -> SearchResponse:
        last_data: Optional[Dict[str, Any]] = None
        ctx = SearXNGSearchContext(
            params=params,
            timeout=self._timeout,
            max_retries=self._max_retries,
            search_call_id=search_call_id,
            task_key=task_key,
        )

        for attempt in range(self._max_retries + 1):
            raw = await self._get_json(
                params, search_call_id=search_call_id, task_key=task_key
            )
            data = raw.data

            response = map_searxng_response(
                data,
                query=query,
                max_results=max_results,
                images_only=images_only,
            )
            retryable_empty = _should_retry_empty_response(
                data,
                response,
                images_only=images_only,
            )

            ctx.log_attempt(
                raw,
                data,
                response,
                images_only=images_only,
                retryable_empty=retryable_empty,
                attempt=attempt,
            )

            if not retryable_empty:
                return response

            last_data = data
            if attempt < self._max_retries:
                await asyncio.sleep(self._retry_backoff_seconds * (attempt + 1))
                continue

        raise SearchProviderTransientError(
            "SearXNG retryable empty result: "
            f"query={query!r}, category={params.get('categories')!r}, "
            f"engines={params.get('engines')!r}, language={params.get('language')!r}, "
            f"unresponsive_engines={(last_data or {}).get('unresponsive_engines')!r}"
        )

    async def _get_json(
        self, params: Dict[str, Any], search_call_id: str = "", task_key: str = ""
    ) -> SearXNGHTTPResult:
        url = f"{self._base_url}/search"
        ctx = SearXNGCallContext(
            params=params,
            timeout=self._timeout,
            search_call_id=search_call_id,
            task_key=task_key,
        )

        try:
            timeout = httpx.Timeout(
                timeout=self._timeout,
                connect=min(3.0, self._timeout),
                read=self._timeout,
                write=min(3.0, self._timeout),
                pool=min(3.0, self._timeout),
            )
            async with self._semaphore:
                client = await self._get_client(timeout)
                response = await client.get(url, params=params)

                ctx.response = response
                if response.is_redirect:
                    ctx.log_http_error()
                    raise SearchProviderError(
                        "searxng",
                        "redirect is not allowed: "
                        f"status={response.status_code}, "
                        f"query={params.get('q')!r}, "
                        f"category={params.get('categories')!r}, "
                        f"engines={params.get('engines')!r}, "
                        f"language={params.get('language')!r}",
                    )

                response.raise_for_status()
                data = response.json()

        except SearchProviderError:
            raise

        except httpx.HTTPStatusError as e:
            ctx.response = e.response
            ctx.log_http_error()
            raise SearchProviderError(
                "searxng",
                "HTTP error: "
                f"status={e.response.status_code}, "
                f"query={params.get('q')!r}, "
                f"category={params.get('categories')!r}, "
                f"engines={params.get('engines')!r}, "
                f"language={params.get('language')!r}",
            ) from e

        except httpx.TimeoutException as e:
            ctx.log_timeout()
            raise SearchTimeoutError(
                provider="searxng",
                query=str(params.get("q", "")),
                timeout=self._timeout,
            ) from e

        except httpx.RequestError as e:
            ctx.log_connection_error()
            raise SearchProviderError(
                "searxng",
                f"request error: query={params.get('q')!r}",
            ) from e

        except ValueError as e:
            ctx.log_json_parse_error()
            raise SearchProviderError(
                "searxng",
                f"invalid JSON: query={params.get('q')!r}, http_status={ctx.http_status}",
            ) from e

        if not isinstance(data, dict):
            ctx.log_adapter_error()
            raise SearchProviderError(
                "searxng",
                f"invalid response type: type={type(data).__name__}, query={params.get('q')!r}",
            )

        if ctx.response is None:
            raise SearchProviderError(
                "searxng",
                f"empty HTTP response: query={params.get('q')!r}",
            )

        return SearXNGHTTPResult(
            data=data,
            http_status=ctx.response.status_code,
            content_type=ctx.response.headers.get("content-type", ""),
        )

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()
        log_event("SearXNGSearcher 关闭", closed=client is not None)

    async def _get_client(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client

        async with self._client_lock:
            if self._client is not None and not self._client.is_closed:
                return self._client

            self._client = httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
            )
            return self._client


def _should_retry_empty_response(
    data: Dict[str, Any],
    response: SearchResponse,
    *,
    images_only: bool,
) -> bool:
    has_payload = bool(response.images) if images_only else bool(response.results)
    if has_payload:
        return False

    unresponsive_engines = data.get("unresponsive_engines")
    return isinstance(unresponsive_engines, list) and bool(unresponsive_engines)


def _log_searxng_call(
    *,
    params: Dict[str, Any],
    timeout: float,
    http_status: Optional[int],
    content_type: str,
    results: Optional[int] = None,
    images: Optional[int] = None,
    unresponsive_engines: Any = (),
    error_type: Optional[str] = None,
    raw_body_sample: str = "",
    attempt: Optional[int] = None,
    max_retries: Optional[int] = None,
    search_call_id: str = "",
    task_key: str = "",
) -> None:
    fields = {
        "query": params.get("q"),
        "category": params.get("categories"),
        "language": params.get("language"),
        "engines": params.get("engines"),
        "timeout": timeout,
        "http_status": http_status,
        "content_type": content_type,
        "results": results,
        "images": images,
        "unresponsive_engines": unresponsive_engines,
        "error_type": error_type or "ok",
        "attempt": attempt,
        "max_retries": max_retries,
        "search_call_id": search_call_id,
        "task_key": task_key,
    }

    if error_type and error_type != "ok":
        log_fail(
            "SearXNG provider 调用",
            f"error_type={error_type}",
            **fields,
            raw_body_sample=raw_body_sample,
        )
        return

    log_event("SearXNG provider 调用成功", **fields)
