import asyncio
from pathlib import Path
from typing import Any, Optional, Tuple

from chat.application.tools.common.content_detection.models import (
    ContentDetection,
    ContentKind,
    DetectionConfidence,
)

_HIGH_CONFIDENCE_THRESHOLD = 0.75


class MagikaDetector:
    def __init__(self) -> None:
        self._magika: Any = None
        self._load_failed = False

    async def detect_path(self, path: Path) -> Optional[ContentDetection]:
        magika = self._get_magika()
        if magika is None:
            return None

        try:
            result = await asyncio.to_thread(magika.identify_path, path)
        except Exception:
            return None

        return self._map_result(result)

    async def detect_bytes(self, content: bytes) -> Optional[ContentDetection]:
        magika = self._get_magika()
        if magika is None:
            return None

        try:
            result = await asyncio.to_thread(magika.identify_bytes, content)
        except Exception:
            return None

        return self._map_result(result)

    def _get_magika(self) -> Any:
        if self._load_failed:
            return None

        if self._magika is not None:
            return self._magika

        try:
            from magika import Magika
        except Exception:
            self._load_failed = True
            return None

        try:
            self._magika = Magika()
        except Exception:
            self._load_failed = True
            return None

        return self._magika

    def _map_result(self, result: Any) -> Optional[ContentDetection]:
        label, mime_type, extension, score = _extract_magika_fields(result)
        if not label and not mime_type:
            return None

        if score is not None and score < _HIGH_CONFIDENCE_THRESHOLD:
            return None

        mime_type = (mime_type or "application/octet-stream").lower()
        extension = _normalize_extension(extension)
        label = (label or "").lower()

        if (
            "zip" in label
            or mime_type in {"application/zip", "application/x-zip-compressed"}
            or extension == ".zip"
        ):
            return None

        mapped = _map_mime_or_extension(mime_type, extension, label)
        if mapped is None:
            return None

        kind, normalized_mime, normalized_extension = mapped
        return ContentDetection(
            kind=kind,
            mime_type=normalized_mime or mime_type,
            extension=normalized_extension or extension,
            confidence=DetectionConfidence.AI,
            reason=f"magika_{label or mime_type}",
            detector="magika",
        )


def _extract_magika_fields(
    result: Any,
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[float]]:
    output = getattr(result, "output", result)
    label = _first_attr(output, ("label", "ct_label", "description"))
    mime_type = _first_attr(output, ("mime_type", "mime", "content_type"))
    extension = _first_attr(output, ("extension", "ext", "extensions"))
    score = _first_attr(output, ("score", "confidence"))

    if score is None:
        score = _first_attr(result, ("score", "confidence"))

    try:
        normalized_score = float(score) if score is not None else None
    except (TypeError, ValueError):
        normalized_score = None

    return (
        _string_or_none(label),
        _string_or_none(mime_type),
        _string_or_none(extension),
        normalized_score,
    )


def _first_attr(value: Any, names: Tuple[str, ...]) -> Any:
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _string_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        value = value[0]
    text = str(value).strip()
    return text or None


def _normalize_extension(extension: Optional[str]) -> Optional[str]:
    if not extension:
        return None
    return extension if extension.startswith(".") else f".{extension}"


def _map_mime_or_extension(
    mime_type: str,
    extension: Optional[str],
    label: str,
) -> Optional[Tuple[ContentKind, str, Optional[str]]]:
    if mime_type == "application/pdf" or extension == ".pdf":
        return ContentKind.DOCUMENT, "application/pdf", ".pdf"
    if mime_type.startswith("image/"):
        return ContentKind.IMAGE, mime_type, extension
    if mime_type == "text/html" or extension in {".html", ".htm"}:
        return ContentKind.HTML, "text/html", ".html"
    if (
        mime_type == "application/json"
        or mime_type.endswith("+json")
        or extension == ".json"
    ):
        return ContentKind.JSON, "application/json", ".json"
    if (
        mime_type in {"application/xml", "text/xml"}
        or mime_type.endswith("+xml")
        or extension == ".xml"
    ):
        return ContentKind.XML, "application/xml", ".xml"
    if mime_type.startswith("text/") or "text" in label:
        return ContentKind.TEXT, "text/plain", ".txt"

    document_extensions = {
        ".doc",
        ".docx",
        ".docm",
        ".xls",
        ".xlsx",
        ".xlsm",
        ".ppt",
        ".pptx",
        ".pptm",
        ".epub",
        ".ods",
    }
    if extension in document_extensions:
        return ContentKind.DOCUMENT, mime_type, extension

    media_prefixes = ("audio/", "video/")
    if mime_type.startswith(media_prefixes):
        return ContentKind.UNSUPPORTED_MEDIA, mime_type, extension

    return None
