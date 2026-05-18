import re
from pathlib import PurePosixPath
from typing import Optional, Set
from urllib.parse import unquote, urlparse

from chat.application.tools.common.content_detection import (
    ContentDetector,
    ContentKind,
    DetectionHints,
    drop_dangerous_inner_suffix,
    sanitize_download_filename,
)
from chat.application.tools.common.content_detection.models import ContentDetection
from chat.application.tools.services.web_fetch.errors import UnsupportedMediaError
from chat.application.tools.services.web_fetch.models import FetchedDocument
from common.logger import log_event, log_fail

_TEXT_FRIENDLY_MIME_TYPES: Set[str] = {
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-javascript",
}

_TEXT_FRIENDLY_EXTENSIONS: Set[str] = {
    ".txt",
    ".md",
    ".json",
    ".xml",
    ".csv",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".log",
    ".py",
    ".js",
    ".ts",
    ".java",
    ".go",
    ".rs",
    ".cpp",
    ".c",
    ".h",
    ".sql",
}

_DOCUMENT_EXTENSIONS: Set[str] = {
    ".pdf",
    ".docx",
    ".docm",
    ".xls",
    ".xlsx",
    ".xlsm",
    ".pptx",
    ".pptm",
    ".epub",
    ".ods",
}

_DOCUMENT_MIME_TYPES: Set[str] = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-word.document.macroenabled.12",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel.sheet.macroenabled.12",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-powerpoint.presentation.macroenabled.12",
    "application/epub+zip",
    "application/vnd.oasis.opendocument.spreadsheet",
}

_UNSUPPORTED_MEDIA_PREFIXES = (
    "image/",
    "video/",
    "audio/",
    "font/",
    "model/",
)

_OCTET_STREAM_MEDIA_TYPE = "application/octet-stream"

_CONTENT_DISPOSITION_FILENAME_STAR_RE = re.compile(
    r"filename\*\s*=\s*([^;]+)",
    re.IGNORECASE,
)

_CONTENT_DISPOSITION_FILENAME_RE = re.compile(
    r"filename\s*=\s*(?P<filename>\"[^\"]+\"|'[^']+'|[^;]+)",
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

_CONTENT_TYPE_CHARSET_RE = re.compile(
    r"charset\s*=\s*['\"]?([^;'\"]+)",
    re.IGNORECASE,
)


def build_web_fetch_detection_hints(
    *,
    url: str,
    content_type_header: str,
    content_disposition: str,
) -> DetectionHints:
    declared_mime_type = _media_type_from_content_type(content_type_header)
    disposition_filename = filename_from_content_disposition(content_disposition)
    url_basename = _url_basename(url)

    return DetectionHints(
        filename=disposition_filename or url_basename or None,
        declared_mime_type=declared_mime_type,
        content_type_header=content_type_header,
        content_disposition=content_disposition,
        source_uri=url,
    )


def should_read_web_fetch_body(
    *,
    hints: DetectionHints,
) -> bool:
    media_type = (hints.declared_mime_type or "").lower()
    extension = _hint_extension(hints)

    if is_declared_unsupported_media(hints=hints):
        return False

    if media_type.startswith("text/"):
        return True

    if media_type in _TEXT_FRIENDLY_MIME_TYPES:
        return True

    if media_type.endswith("+json") or media_type.endswith("+xml"):
        return True

    if media_type in _DOCUMENT_MIME_TYPES:
        return True

    if media_type == _OCTET_STREAM_MEDIA_TYPE or not media_type:
        return (
            extension in _DOCUMENT_EXTENSIONS or extension in _TEXT_FRIENDLY_EXTENSIONS
        )

    return False


def is_declared_unsupported_media(
    *,
    hints: DetectionHints,
) -> bool:
    media_type = (hints.declared_mime_type or "").lower()
    return media_type.startswith(_UNSUPPORTED_MEDIA_PREFIXES)


async def build_web_fetch_result(
    *,
    url: str,
    content: bytes,
    hints: DetectionHints,
    detector: ContentDetector,
) -> Optional[str | FetchedDocument]:
    detection = await detector.detect_bytes(content, hints=hints)

    if detection.kind == ContentKind.DOCUMENT:
        return FetchedDocument(
            url=url,
            media_type=detection.mime_type
            or hints.declared_mime_type
            or "application/octet-stream",
            filename=build_document_filename(
                url=url,
                hints=hints,
                detection=detection,
            ),
            content=content,
        )

    if detection.kind in {
        ContentKind.HTML,
        ContentKind.JSON,
        ContentKind.XML,
        ContentKind.TEXT,
    }:
        text = decode_text_response(
            content,
            content_type_header=hints.content_type_header or "",
        ).strip()

        if not text:
            log_fail("静态抓取", "empty text", url=url)
            return None

        return text

    if detection.kind in {ContentKind.IMAGE, ContentKind.UNSUPPORTED_MEDIA}:
        media_type = detection.mime_type or hints.declared_mime_type or "unknown"
        log_event(
            "web_fetch_unsupported_media_detected",
            content_type=media_type,
            action="stop_fallback",
            url=url,
        )
        raise UnsupportedMediaError(
            url=url,
            media_type=media_type,
        )

    if detection.kind in {
        ContentKind.UNSUPPORTED_ARCHIVE,
        ContentKind.UNSUPPORTED_BINARY,
    }:
        log_event(
            "静态抓取失败，允许上层降级",
            detail=f"不支持的响应类型: {detection.reason}",
            url=url,
        )
        return None

    return None


def build_document_filename(
    *,
    url: str,
    hints: DetectionHints,
    detection: ContentDetection,
) -> str:
    base = filename_from_content_disposition(hints.content_disposition or "")

    if not base:
        base = _url_basename(url)

    if not base:
        base = "download"

    base = sanitize_download_filename(base)
    stem = PurePosixPath(base).stem or "download"
    detected_ext = detection.extension or _hint_extension(hints)

    return drop_dangerous_inner_suffix(f"{stem}{detected_ext}")


def decode_text_response(
    content: bytes,
    *,
    content_type_header: str,
) -> str:
    encoding = charset_from_content_type(content_type_header)
    if encoding:
        decoded = try_decode_with_encoding(content, encoding)
        if decoded is not None:
            return decoded

    encoding = charset_from_html_meta(content)
    if encoding:
        decoded = try_decode_with_encoding(content, encoding)
        if decoded is not None:
            return decoded

    return content.decode("utf-8", errors="replace")


def filename_from_content_disposition(value: str) -> Optional[str]:
    filename_star = filename_from_rfc5987(value)
    if filename_star:
        return filename_star

    match = _CONTENT_DISPOSITION_FILENAME_RE.search(value)
    if not match:
        return None

    return match.group("filename").strip().strip("\"'").strip() or None


def filename_from_rfc5987(value: str) -> Optional[str]:
    match = _CONTENT_DISPOSITION_FILENAME_STAR_RE.search(value)
    if not match:
        return None

    raw = match.group(1).strip().strip("\"'")

    try:
        charset, _, encoded = raw.split("'", 2)
    except ValueError:
        return None

    try:
        return (
            unquote(
                encoded,
                encoding=charset or "utf-8",
            ).strip()
            or None
        )
    except LookupError:
        return (
            unquote(
                encoded,
                encoding="utf-8",
                errors="replace",
            ).strip()
            or None
        )


def charset_from_content_type(value: str) -> Optional[str]:
    match = _CONTENT_TYPE_CHARSET_RE.search(value)
    if not match:
        return None

    return match.group(1).strip()


def charset_from_html_meta(content: bytes) -> Optional[str]:
    head = content[:4096]

    match = _META_CHARSET_RE.search(head)
    if match:
        return match.group(1).decode("ascii", errors="replace").strip()

    for meta_match in _META_TAG_RE.finditer(head):
        tag = meta_match.group(0)
        lower_tag = tag.lower()

        if b"http-equiv" not in lower_tag:
            continue

        if b"content-type" not in lower_tag:
            continue

        tag_text = tag.decode("ascii", errors="replace")

        charset_match = _CONTENT_TYPE_CHARSET_RE.search(tag_text)
        if charset_match:
            return charset_match.group(1).strip()

    return None


def try_decode_with_encoding(
    content: bytes,
    encoding: str,
) -> Optional[str]:
    try:
        return content.decode(encoding, errors="replace")
    except LookupError:
        return None


def _media_type_from_content_type(value: str) -> str:
    return value.lower().split(";", 1)[0].strip()


def _url_basename(url: str) -> str:
    path = urlparse(url).path
    return PurePosixPath(unquote(path).replace("\\", "/")).name


def _hint_extension(hints: DetectionHints) -> str:
    filename = hints.filename
    if not filename and hints.source_uri:
        filename = _url_basename(hints.source_uri)
    return PurePosixPath(filename or "").suffix.lower()
