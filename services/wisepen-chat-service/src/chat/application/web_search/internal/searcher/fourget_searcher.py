from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import httpx

from chat.application.web_search.errors import (
    SearchProviderError,
    SearchProviderTransientError,
    SearchTimeoutError,
)
from chat.application.web_search.internal.models.fourget import (
    FOURGET_ALLOWED_WEB_SCRAPERS,
    FourGetSearchRequest,
    map_fourget_response,
)
from chat.application.web_search.internal.searcher.base import BaseSearcher
from chat.application.web_search.models.common import SearchResponse
from common.logger import log_event, log_fail

_FOURGET_ENDPOINT = "/api/v1/web"
_FOURGET_DEFAULT_TIMEOUT = 8.0
_FOURGET_DEFAULT_WEB_SCRAPER = "ddg"
_FOURGET_DEFAULT_MAX_CONCURRENCY = 5
_FOURGET_DEFAULT_MAX_RETRIES = 1
_FOURGET_DEFAULT_RETRY_BACKOFF_SECONDS = 0.4
_FOURGET_REASON_TOKENS = (
    "pass",
    "captcha",
    "unauthorized",
    "expired",
    "blocked",
    "rate",
    "unsupported",
)


@dataclass(frozen=True, slots=True)
class FourGetHTTPResult:
    data: Dict[str, Any]
    http_status: int
    content_type: str


@dataclass
class FourGetCallContext:
    params: Dict[str, str]
    timeout: float
    search_call_id: str = ""
    task_key: str = ""
    language: Optional[str] = None
    with_images: bool = False
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

    def log_failure(self, error_type: str, *, reason: str = "") -> None:
        _log_fourget_call(
            params=self.params,
            timeout=self.timeout,
            http_status=self.http_status,
            content_type=self.content_type,
            error_type=error_type,
            reason=reason,
            raw_body_sample=self.raw_body_sample,
            search_call_id=self.search_call_id,
            task_key=self.task_key,
            language=self.language,
            with_images=self.with_images,
        )


class FourGetSearcher(BaseSearcher):
    name = "fourget"

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = _FOURGET_DEFAULT_TIMEOUT,
        web_scraper: str = _FOURGET_DEFAULT_WEB_SCRAPER,
        max_concurrency: int = _FOURGET_DEFAULT_MAX_CONCURRENCY,
        max_retries: int = _FOURGET_DEFAULT_MAX_RETRIES,
        retry_backoff_seconds: float = _FOURGET_DEFAULT_RETRY_BACKOFF_SECONDS,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        _validate_base_url(base_url)
        _validate_positive_number(timeout, "timeout")
        _validate_web_scraper(web_scraper)
        _validate_non_bool_int_at_least(max_concurrency, "max_concurrency", 1)
        _validate_non_bool_int_at_least(max_retries, "max_retries", 0)
        _validate_non_negative_number(retry_backoff_seconds, "retry_backoff_seconds")

        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._web_scraper = web_scraper
        self._max_concurrency = max_concurrency
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._transport = transport
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
        if engines:
            raise SearchProviderError(
                "fourget",
                "fourget does not support per-request engines override",
            )

        request = FourGetSearchRequest(query=query, scraper=self._web_scraper)
        params = request.to_params()

        if with_images:
            log_event(
                "FourGet image search unsupported; using web search",
                query=query,
                scraper=self._web_scraper,
                search_call_id=search_call_id,
                task_key=task_key,
            )

        last_response: Optional[SearchResponse] = None
        for attempt in range(self._max_retries + 1):
            raw = await self._get_json(
                params,
                search_call_id=search_call_id,
                task_key=task_key,
                language=language,
                with_images=with_images,
            )
            response = map_fourget_response(
                raw.data,
                query=query,
                scraper=self._web_scraper,
                max_results=max_results,
            )
            last_response = response

            retryable_empty = not response.results
            _log_fourget_call(
                params=params,
                timeout=self._timeout,
                http_status=raw.http_status,
                content_type=raw.content_type,
                results=len(response.results),
                error_type="retryable_empty_result" if retryable_empty else "ok",
                attempt=attempt,
                max_retries=self._max_retries,
                search_call_id=search_call_id,
                task_key=task_key,
                language=language,
                with_images=with_images,
            )

            if response.results:
                return response

            if attempt < self._max_retries:
                await asyncio.sleep(self._retry_backoff_seconds * (attempt + 1))

        raise SearchProviderTransientError(
            "4get empty_result after retries: "
            f"query={query!r}, scraper={self._web_scraper!r}, "
            f"results={len(last_response.results) if last_response else 0}"
        )

    async def _get_json(
        self,
        params: Dict[str, str],
        *,
        search_call_id: str = "",
        task_key: str = "",
        language: Optional[str] = None,
        with_images: bool = False,
    ) -> FourGetHTTPResult:
        url = f"{self._base_url}{_FOURGET_ENDPOINT}"
        ctx = FourGetCallContext(
            params=params,
            timeout=self._timeout,
            search_call_id=search_call_id,
            task_key=task_key,
            language=language,
            with_images=with_images,
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
                response = await client.get(url, params=params, timeout=timeout)

                ctx.response = response
                if response.is_redirect:
                    ctx.log_failure("redirect")
                    raise SearchProviderError(
                        "fourget",
                        "redirect: "
                        f"status={response.status_code}, query={params.get('s')!r}, "
                        f"scraper={params.get('scraper')!r}",
                    )

                data = response.json()

        except SearchProviderError:
            raise

        except httpx.TimeoutException as e:
            ctx.log_failure("timeout")
            raise SearchTimeoutError(
                provider="fourget",
                query=params.get("s", ""),
                timeout=self._timeout,
            ) from e

        except httpx.RequestError as e:
            ctx.log_failure("connection_error")
            raise SearchProviderError(
                "fourget",
                f"connection_error: query={params.get('s')!r}, scraper={params.get('scraper')!r}",
            ) from e

        except ValueError as e:
            ctx.log_failure("json_parse_error")
            raise SearchProviderError(
                "fourget",
                f"json_parse_error: query={params.get('s')!r}, http_status={ctx.http_status}",
            ) from e

        if not isinstance(data, dict):
            ctx.log_failure("invalid_response_type")
            raise SearchProviderError(
                "fourget",
                "invalid_response_type: "
                f"type={type(data).__name__}, query={params.get('s')!r}",
            )

        status = data.get("status")
        if status != "ok":
            status_message = status if isinstance(status, str) else repr(status)
            reason = _status_reason(status_message)
            error_type = (
                f"provider_status_error:{reason}"
                if reason
                else "provider_status_error"
            )
            ctx.log_failure(error_type, reason=status_message)
            raise SearchProviderError(
                "fourget",
                "provider_status_error: "
                f"status={status_message}, query={params.get('s')!r}, "
                f"scraper={params.get('scraper')!r}",
            )

        if ctx.response is None:
            raise SearchProviderError(
                "fourget",
                f"invalid_response_type: missing HTTP response, query={params.get('s')!r}",
            )

        try:
            ctx.response.raise_for_status()
        except httpx.HTTPStatusError as e:
            ctx.log_failure("http_error")
            raise SearchProviderError(
                "fourget",
                "http_error: "
                f"status={e.response.status_code}, query={params.get('s')!r}, "
                f"scraper={params.get('scraper')!r}",
            ) from e

        web = data.get("web")
        if not isinstance(web, list):
            ctx.log_failure("invalid_response_type", reason="web field is not a list")
            raise SearchProviderError(
                "fourget",
                "invalid_response_type: "
                f"web_type={type(web).__name__}, query={params.get('s')!r}",
            )

        return FourGetHTTPResult(
            data=data,
            http_status=ctx.response.status_code,
            content_type=ctx.response.headers.get("content-type", ""),
        )

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()
        log_event("FourGetSearcher 关闭", closed=client is not None)

    async def _get_client(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client

        async with self._client_lock:
            if self._client is not None and not self._client.is_closed:
                return self._client

            self._client = httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                transport=self._transport,
            )
            return self._client


def _validate_base_url(value: object) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError("base_url must be a non-empty string")

    if not (value.startswith("http://") or value.startswith("https://")):
        raise ValueError("base_url must start with http:// or https://")


def _validate_web_scraper(value: object) -> None:
    if type(value) is not str or value not in FOURGET_ALLOWED_WEB_SCRAPERS:
        raise ValueError("web_scraper must be one of: ddg, yandex")


def _validate_positive_number(value: object, name: str) -> None:
    if type(value) not in {int, float} or value <= 0:
        raise ValueError(f"{name} must be an int or float greater than 0")


def _validate_non_negative_number(value: object, name: str) -> None:
    if type(value) not in {int, float} or value < 0:
        raise ValueError(f"{name} must be an int or float greater than or equal to 0")


def _validate_non_bool_int_at_least(value: object, name: str, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an int greater than or equal to {minimum}")


def _status_reason(status: str) -> str:
    normalized = status.lower()
    for token in _FOURGET_REASON_TOKENS:
        if token in normalized:
            return token
    return ""


def _log_fourget_call(
    *,
    params: Dict[str, str],
    timeout: float,
    http_status: Optional[int],
    content_type: str,
    results: Optional[int] = None,
    error_type: str = "ok",
    reason: str = "",
    raw_body_sample: str = "",
    attempt: Optional[int] = None,
    max_retries: Optional[int] = None,
    search_call_id: str = "",
    task_key: str = "",
    language: Optional[str] = None,
    with_images: bool = False,
) -> None:
    fields = {
        "query": params.get("s"),
        "scraper": params.get("scraper"),
        "language": language,
        "with_images": with_images,
        "timeout": timeout,
        "http_status": http_status,
        "content_type": content_type,
        "results": results,
        "error_type": error_type,
        "reason": reason,
        "attempt": attempt,
        "max_retries": max_retries,
        "search_call_id": search_call_id,
        "task_key": task_key,
    }

    if error_type != "ok":
        log_fail(
            "FourGet provider 调用",
            f"error_type={error_type}",
            **fields,
            raw_body_sample=raw_body_sample,
        )
        return

    log_event("FourGet provider 调用成功", **fields)
