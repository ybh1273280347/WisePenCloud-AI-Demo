import json
from pathlib import PurePosixPath
from typing import Optional

from chat.application.content_detection.models import (
    ContentDetection,
    ContentKind,
    DetectionConfidence,
    DetectionHints,
)

ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png", "png_magic"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg", "jpeg_magic"),
    (b"GIF87a", "image/gif", ".gif", "gif_magic"),
    (b"GIF89a", "image/gif", ".gif", "gif_magic"),
    (b"BM", "image/bmp", ".bmp", "bmp_magic"),
    (b"II*\x00", "image/tiff", ".tiff", "tiff_magic"),
    (b"MM\x00*", "image/tiff", ".tiff", "tiff_magic"),
)

_OLE_DOCUMENT_HINTS = {
    ".xls": ("application/vnd.ms-excel", ".xls", "ole_xls"),
    ".doc": ("application/msword", ".doc", "ole_doc"),
    ".ppt": ("application/vnd.ms-powerpoint", ".ppt", "ole_ppt"),
}

_OLE_MIME_HINTS = {
    "application/vnd.ms-excel": ("application/vnd.ms-excel", ".xls", "ole_xls"),
    "application/msword": ("application/msword", ".doc", "ole_doc"),
    "application/vnd.ms-powerpoint": (
        "application/vnd.ms-powerpoint",
        ".ppt",
        "ole_ppt",
    ),
}

_TEXT_MIME_TYPES = {
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-javascript",
    "text/xml",
}


def detect_builtin_magic(
    header: bytes,
    content: bytes,
    hints: DetectionHints,
) -> Optional[ContentDetection]:
    if not content:
        return None

    if header.startswith(b"%PDF-"):
        return ContentDetection(
            kind=ContentKind.DOCUMENT,
            mime_type="application/pdf",
            extension=".pdf",
            confidence=DetectionConfidence.MAGIC,
            reason="pdf_magic",
            detector="builtin_magic",
        )

    image = detect_image_magic(header)
    if image:
        return image

    if header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return detect_ole_magic(hints)

    if is_zip_content(header):
        return None

    if is_html_content(header):
        return ContentDetection(
            ContentKind.HTML,
            "text/html",
            ".html",
            DetectionConfidence.MAGIC,
            "html_payload",
            "builtin_magic",
        )

    if is_json_content(header, hints):
        return ContentDetection(
            ContentKind.JSON,
            "application/json",
            ".json",
            DetectionConfidence.MAGIC,
            "json_payload",
            "builtin_magic",
        )

    if is_xml_content(header, hints):
        return ContentDetection(
            ContentKind.XML,
            "application/xml",
            ".xml",
            DetectionConfidence.MAGIC,
            "xml_payload",
            "builtin_magic",
        )

    return None


def detect_image_magic(header: bytes) -> Optional[ContentDetection]:
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return ContentDetection(
            ContentKind.IMAGE,
            "image/webp",
            ".webp",
            DetectionConfidence.MAGIC,
            "webp_magic",
            "builtin_magic",
        )

    for signature, mime_type, extension, reason in _IMAGE_MAGIC:
        if header.startswith(signature):
            return ContentDetection(
                ContentKind.IMAGE,
                mime_type,
                extension,
                DetectionConfidence.MAGIC,
                reason,
                "builtin_magic",
            )

    return None


def detect_ole_magic(hints: DetectionHints) -> ContentDetection:
    ext = hint_extension(hints)
    if ext in _OLE_DOCUMENT_HINTS:
        mime_type, extension, reason = _OLE_DOCUMENT_HINTS[ext]
        return ContentDetection(
            ContentKind.DOCUMENT,
            mime_type,
            extension,
            DetectionConfidence.MAGIC,
            reason,
            "builtin_magic",
        )

    mime_type = normalized_hint_mime(hints)
    if mime_type in _OLE_MIME_HINTS:
        detected_mime, extension, reason = _OLE_MIME_HINTS[mime_type]
        return ContentDetection(
            ContentKind.DOCUMENT,
            detected_mime,
            extension,
            DetectionConfidence.MAGIC,
            reason,
            "builtin_magic",
        )

    return ContentDetection(
        kind=ContentKind.UNSUPPORTED_BINARY,
        mime_type="application/octet-stream",
        extension=None,
        confidence=DetectionConfidence.MAGIC,
        reason="unsupported_ole",
        detector="builtin_magic",
    )


def is_zip_content(header: bytes) -> bool:
    return header.startswith(ZIP_SIGNATURES)


def is_html_content(content: bytes) -> bool:
    head = content[:512].lstrip().lower()
    return (
        head.startswith(b"<!doctype html")
        or head.startswith(b"<html")
        or b"<html" in head[:128]
    )


def is_json_content(content: bytes, hints: DetectionHints) -> bool:
    head = content[:4096].lstrip()
    if head[:1] not in (b"{", b"["):
        return False

    mime_type = normalized_hint_mime(hints)
    if mime_type == "application/json" or mime_type.endswith("+json"):
        return True

    try:
        json.loads(head.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return True


def is_xml_content(content: bytes, hints: DetectionHints) -> bool:
    head = content[:512].lstrip().lower()
    mime_type = normalized_hint_mime(hints)
    if mime_type in {"application/xml", "text/xml"} or mime_type.endswith("+xml"):
        return head.startswith(b"<?xml") or head.startswith(b"<")
    return head.startswith(b"<?xml")


def normalized_hint_mime(hints: DetectionHints) -> str:
    value = hints.declared_mime_type or hints.content_type_header or ""
    return value.split(";", 1)[0].strip().lower()


def hint_extension(hints: DetectionHints) -> str:
    filename = hints.filename
    if not filename and hints.source_uri:
        filename = PurePosixPath(hints.source_uri.replace("\\", "/")).name
    return PurePosixPath(filename or "").suffix.lower()


def is_text_mime_hint(hints: DetectionHints) -> bool:
    mime_type = normalized_hint_mime(hints)
    return (
        mime_type.startswith("text/")
        or mime_type in _TEXT_MIME_TYPES
        or mime_type.endswith("+json")
        or mime_type.endswith("+xml")
    )
