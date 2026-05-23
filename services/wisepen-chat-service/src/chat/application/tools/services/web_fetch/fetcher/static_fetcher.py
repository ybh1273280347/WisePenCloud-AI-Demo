import asyncio
from typing import List, Optional
from urllib.parse import urljoin

import httpx
from chat.application.tools.common.content_detection import ContentDetector
from chat.application.tools.common.security.network import (
    UrlSecurityError,
    validate_public_http_url,
)
from chat.application.tools.services.web_fetch.base import BaseFetcher
from chat.application.tools.services.web_fetch.content_processor import ContentProcessor
from chat.application.tools.services.web_fetch.errors import UnsupportedMediaError
from chat.application.tools.services.web_fetch.fetcher.content_detection_adapter import (
    build_web_fetch_detection_hints,
    build_web_fetch_result,
    is_declared_unsupported_media,
    should_read_web_fetch_body,
)
from chat.application.tools.services.web_fetch.models import FetchedDocument
from common.logger import log_error, log_event, log_fail

_MAX_REDIRECTS = 5


def _log_static_fetch_fail(detail, *, url: str) -> None:
    log_fail("静态抓取", f"detail={detail}", url=url)


async def _read_limited(
    response: httpx.Response,
    *,
    url: str,
    max_response_bytes: int,
) -> Optional[bytes]:
    content_length = response.headers.get("content-length")

    if content_length:
        try:
            expected_size = int(content_length)
        except ValueError:
            expected_size = 0

        if expected_size > max_response_bytes:
            _log_static_fetch_fail(
                f"响应体过大({expected_size}字节)，上限{max_response_bytes}字节",
                url=url,
            )
            return None

    chunks: List[bytes] = []
    total_size = 0

    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
        total_size += len(chunk)

        if total_size > max_response_bytes:
            _log_static_fetch_fail(
                f"响应体超过上限({total_size}字节)，上限{max_response_bytes}字节",
                url=url,
            )
            return None

        chunks.append(chunk)

    return b"".join(chunks)


class StaticFetcher(BaseFetcher):
    """轻量级静态 HTTP 抓取器"""

    name = "static"

    def __init__(
        self,
        timeout: float = 10.0,
        max_retries: int = 3,
        max_response_bytes: int = 50 * 1024 * 1024,
        content_detector: Optional[ContentDetector] = None,
        processor: Optional[ContentProcessor] = None,
    ):
        self._timeout = timeout
        self._max_retries = max_retries
        self._max_response_bytes = max_response_bytes
        self._content_detector = content_detector or ContentDetector()
        self._processor = processor or ContentProcessor()
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()

    async def fetch(self, url: str) -> Optional[str | FetchedDocument]:
        redirect_count = 0
        current_url = url

        try:
            client = await self._get_client()
            while True:
                async with client.stream("GET", current_url) as response:
                    if response.status_code >= 300 and response.status_code < 400:
                        if redirect_count >= _MAX_REDIRECTS:
                            _log_static_fetch_fail("重定向次数过多", url=current_url)
                            return None

                        location = response.headers.get("location")
                        if not location:
                            _log_static_fetch_fail(
                                "redirect 缺少 Location header", url=current_url
                            )
                            return None

                        current_url = urljoin(str(response.url), location)
                        current_url = await asyncio.to_thread(
                            validate_public_http_url, current_url
                        )

                        redirect_count += 1
                        continue

                    response.raise_for_status()

                    content_type_header = response.headers.get("content-type", "")
                    content_disposition = response.headers.get(
                        "content-disposition", ""
                    )

                    hints = build_web_fetch_detection_hints(
                        url=current_url,
                        content_type_header=content_type_header,
                        content_disposition=content_disposition,
                    )

                    if not should_read_web_fetch_body(hints=hints):
                        if is_declared_unsupported_media(hints=hints):
                            log_event(
                                "web_fetch_unsupported_media_detected",
                                content_type=hints.declared_mime_type,
                                action="stop_fallback",
                                url=current_url,
                            )
                            raise UnsupportedMediaError(
                                url=current_url,
                                media_type=hints.declared_mime_type or "unknown",
                            )
                        _log_static_fetch_fail(
                            f"不支持的响应类型: {hints.declared_mime_type or 'unknown'}",
                            url=current_url,
                        )
                        return None

                    content = await _read_limited(
                        response,
                        url=current_url,
                        max_response_bytes=self._max_response_bytes,
                    )
                    if content is None:
                        return None

                    result = await build_web_fetch_result(
                        url=current_url,
                        content=content,
                        hints=hints,
                        detector=self._content_detector,
                    )
                    if isinstance(result, FetchedDocument):
                        return result

                    processed = await asyncio.to_thread(self._processor.process, result)
                    if not processed:
                        _log_static_fetch_fail("内容处理失败", url=current_url)
                        return None

                    return processed

        except UrlSecurityError:
            raise

        except UnsupportedMediaError:
            raise

        except httpx.TimeoutException:
            _log_static_fetch_fail(f"请求超时 {self._timeout}s", url=current_url)
            return None

        except httpx.ConnectError:
            _log_static_fetch_fail("连接失败", url=current_url)
            return None

        except httpx.HTTPStatusError as e:
            _log_static_fetch_fail(f"HTTP {e.response.status_code}", url=current_url)
            return None

        except httpx.RequestError as e:
            _log_static_fetch_fail(f"请求异常: {e.__class__.__name__}", url=current_url)
            return None

        except Exception as e:
            log_error("静态抓取", e, url=current_url)
            return None

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()
        log_event("StaticFetcher 关闭", closed=client is not None)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client

        async with self._client_lock:
            if self._client is not None and not self._client.is_closed:
                return self._client

            transport = httpx.AsyncHTTPTransport(retries=self._max_retries)
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers=self._headers,
                transport=transport,
                follow_redirects=False,
            )
            return self._client
