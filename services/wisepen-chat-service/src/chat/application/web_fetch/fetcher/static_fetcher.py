import asyncio
import re
from pathlib import PurePosixPath
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import unquote, urljoin, urlparse

import httpx

from chat.application.web_fetch.models import FetchedDocument
from chat.application.web_fetch.utils import DOCUMENT_EXTENSIONS, UrlSecurityError, validate_public_http_url
from common.logger import log_error, log_fail, log_ok

_MAX_REDIRECTS = 5

_DOCUMENT_EXTENSION_BY_MIME_TYPE: Dict[str, str] = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/epub+zip": ".epub",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
}

_TEXT_FRIENDLY_MIME_TYPES: Set[str] = {
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-javascript",
}

_TEXT_FRIENDLY_EXTENSIONS: Tuple[str, ...] = (
    ".txt",
    ".md",
    ".json",
    ".xml",
    ".csv",
)

_CONTENT_DISPOSITION_FILENAME_STAR_RE = re.compile(
    r"filename\*\s*=\s*([^;]+)",
    re.IGNORECASE,
)

_CONTENT_DISPOSITION_FILENAME_RE = re.compile(
    r"filename\s*=\s*(?P<filename>\"[^\"]+\"|'[^']+'|[^;]+)",
    re.IGNORECASE,
)

_CONTENT_TYPE_CHARSET_RE = re.compile(
    r"charset\s*=\s*['\"]?([^;'\"]+)",
    re.IGNORECASE,
)

_META_CHARSET_RE = re.compile(
    rb"<meta[^>]+charset\s*=\s*['\"]?([^'\"\s/>;]+)",
    re.IGNORECASE,
)

_META_TAG_RE = re.compile(
    rb"<meta\b[^>]*>",
    re.IGNORECASE,
)


def _log_static_fetch_fail(detail, *, url: str) -> None:
    log_fail("静态抓取", detail, url=url)


def _get_media_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").lower().split(";")[0].strip()


def _may_read_body(media_type: str, path: str) -> bool:
    lower_path = path.lower()

    if not media_type:
        return True

    if media_type == "application/octet-stream":
        return lower_path.endswith(DOCUMENT_EXTENSIONS) or lower_path.endswith(_TEXT_FRIENDLY_EXTENSIONS)

    if _is_text_like(media_type, lower_path):
        return True

    if _is_document_like(media_type, lower_path):
        return True

    return False


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
            _log_static_fetch_fail(f"响应体过大({expected_size}字节)，上限{max_response_bytes}字节", url=url)
            return None

    chunks: List[bytes] = []
    total_size = 0

    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
        total_size += len(chunk)

        if total_size > max_response_bytes:
            _log_static_fetch_fail(f"响应体超过上限({total_size}字节)，上限{max_response_bytes}字节", url=url)
            return None

        chunks.append(chunk)

    return b"".join(chunks)


def _route_response(
    *,
    media_type: str,
    content_type_header: str,
    content_disposition: str,
    path: str,
    url: str,
    content: bytes,
) -> Optional[str | FetchedDocument]:
    if _is_text_response(media_type, path, content):
        text = _decode_text_response(
            content,
            content_type_header=content_type_header,
        ).strip()

        if not text:
            _log_static_fetch_fail("文本响应为空", url=url)
            return None

        log_ok("静态抓取", content_type=media_type or "unknown", size=len(content), url=url)
        return text

    if _is_document_like(media_type, path):
        if not content:
            _log_static_fetch_fail("文档响应为空", url=url)
            return None

        log_ok("静态抓取", content_type=media_type or "unknown", size=len(content), url=url)
        return FetchedDocument(
            url=url,
            media_type=media_type or "application/octet-stream",
            filename=_document_filename(
                path=path,
                media_type=media_type,
                content_disposition=content_disposition,
            ),
            content=content,
        )

    _log_static_fetch_fail(f"不支持的 Content-Type: {media_type or 'unknown'}", url=url)
    return None


def _is_text_like(media_type: str, path: str) -> bool:
    lower_path = path.lower()

    if media_type.startswith("text/"):
        return True

    if media_type in _TEXT_FRIENDLY_MIME_TYPES:
        return True

    if media_type.endswith("+json") or media_type.endswith("+xml"):
        return True

    if lower_path.endswith(_TEXT_FRIENDLY_EXTENSIONS):
        return True

    return False


def _is_document_like(media_type: str, path: str) -> bool:
    lower_path = path.lower()
    return media_type in _DOCUMENT_EXTENSION_BY_MIME_TYPE or lower_path.endswith(DOCUMENT_EXTENSIONS)


def _document_filename(
    *,
    path: str,
    media_type: str,
    content_disposition: str,
) -> str:
    fallback_suffix = _document_suffix(
        path=path,
        media_type=media_type,
    )

    header_filename = _filename_from_content_disposition(content_disposition)
    if header_filename:
        name = PurePosixPath(
            header_filename.replace("\\", "/")
        ).name.strip()

        if name:
            return _with_supported_suffix(
                name,
                fallback_suffix,
            )

    name = PurePosixPath(unquote(path)).name.strip()
    if name:
        return _with_supported_suffix(
            name,
            fallback_suffix,
        )

    return f"download{fallback_suffix}"


def _document_suffix(*, path: str, media_type: str) -> str:
    suffix = PurePosixPath(unquote(path)).suffix.lower()
    if suffix in DOCUMENT_EXTENSIONS:
        return suffix

    return _DOCUMENT_EXTENSION_BY_MIME_TYPE.get(media_type, ".bin")


def _filename_from_content_disposition(value: str) -> Optional[str]:
    filename_star = _filename_from_rfc5987(value)
    if filename_star:
        return filename_star

    match = _CONTENT_DISPOSITION_FILENAME_RE.search(value)
    if not match:
        return None

    return match.group("filename").strip().strip("\"'").strip() or None


def _filename_from_rfc5987(value: str) -> Optional[str]:
    match = _CONTENT_DISPOSITION_FILENAME_STAR_RE.search(value)
    if not match:
        return None

    raw = match.group(1).strip().strip("\"'")

    try:
        charset, _, encoded = raw.split("'", 2)
    except ValueError:
        return None

    try:
        return unquote(
            encoded,
            encoding=charset or "utf-8",
        ).strip() or None
    except LookupError:
        return unquote(
            encoded,
            encoding="utf-8",
            errors="replace",
        ).strip() or None


def _with_supported_suffix(name: str, fallback_suffix: str) -> str:
    path_name = PurePosixPath(name)
    suffix = path_name.suffix.lower()

    if suffix in DOCUMENT_EXTENSIONS:
        return path_name.name

    stem = path_name.stem or path_name.name
    return f"{stem}{fallback_suffix}"


def _decode_text_response(
    content: bytes,
    *,
    content_type_header: str,
) -> str:
    encoding = _charset_from_content_type(content_type_header)
    if encoding:
        decoded = _try_decode_with_encoding(content, encoding)
        if decoded is not None:
            return decoded

    encoding = _charset_from_html_meta(content)
    if encoding:
        decoded = _try_decode_with_encoding(content, encoding)
        if decoded is not None:
            return decoded

    return content.decode("utf-8", errors="replace")


def _try_decode_with_encoding(
    content: bytes,
    encoding: str,
) -> Optional[str]:
    try:
        return content.decode(encoding, errors="replace")
    except LookupError:
        return None


def _charset_from_content_type(value: str) -> Optional[str]:
    match = _CONTENT_TYPE_CHARSET_RE.search(value)
    if not match:
        return None

    return match.group(1).strip()


def _charset_from_html_meta(content: bytes) -> Optional[str]:
    head = content[:4096]

    match = _META_CHARSET_RE.search(head)
    if match:
        return match.group(1).decode(
            "ascii",
            errors="replace",
        ).strip()

    for meta_match in _META_TAG_RE.finditer(head):
        tag = meta_match.group(0)
        lower_tag = tag.lower()

        if b"http-equiv" not in lower_tag:
            continue

        if b"content-type" not in lower_tag:
            continue

        tag_text = tag.decode(
            "ascii",
            errors="replace",
        )

        charset_match = _CONTENT_TYPE_CHARSET_RE.search(tag_text)
        if charset_match:
            return charset_match.group(1).strip()

    return None


def _is_text_response(media_type: str, path: str, content: bytes) -> bool:
    if _is_text_like(media_type, path):
        return True

    head = content[:512].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<html" in head[:128]:
        return True

    return False


class StaticFetcher:
    """轻量级静态 HTTP 抓取器"""

    def __init__(
        self,
        timeout: float = 10.0,
        max_retries: int = 3,
        max_response_bytes: int = 50 * 1024 * 1024,
    ):
        self._timeout = timeout
        self._max_retries = max_retries
        self._max_response_bytes = max_response_bytes
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    async def fetch(self, url: str) -> Optional[str | FetchedDocument]:
        redirect_count = 0
        current_url = url

        try:
            transport = httpx.AsyncHTTPTransport(retries=self._max_retries)

            async with httpx.AsyncClient(
                timeout=self._timeout,
                headers=self._headers,
                transport=transport,
                follow_redirects=False,
            ) as client:
                while True:
                    async with client.stream("GET", current_url) as response:
                        if response.status_code >= 300 and response.status_code < 400:
                            if redirect_count >= _MAX_REDIRECTS:
                                _log_static_fetch_fail("重定向次数过多", url=current_url)
                                return None

                            location = response.headers.get("location")
                            if not location:
                                _log_static_fetch_fail("redirect 缺少 Location header", url=current_url)
                                return None

                            current_url = urljoin(str(response.url), location)
                            await asyncio.to_thread(validate_public_http_url, current_url)

                            redirect_count += 1
                            continue

                        response.raise_for_status()

                        content_type_header = response.headers.get("content-type", "")
                        content_disposition = response.headers.get("content-disposition", "")
                        media_type = _get_media_type(response)
                        path = urlparse(current_url).path

                        if not _may_read_body(media_type, path):
                            _log_static_fetch_fail(f"不支持的 Content-Type: {media_type or 'unknown'}", url=current_url)
                            return None

                        content = await _read_limited(response, url=current_url, max_response_bytes=self._max_response_bytes)
                        if content is None:
                            return None

                        return _route_response(
                            media_type=media_type,
                            content_type_header=content_type_header,
                            content_disposition=content_disposition,
                            path=path,
                            url=current_url,
                            content=content,
                        )

        except UrlSecurityError:
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
