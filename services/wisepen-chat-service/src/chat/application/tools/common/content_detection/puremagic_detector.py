import asyncio
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple

from chat.application.tools.common.content_detection.magika_detector import _map_mime_or_extension, _normalize_extension
from chat.application.tools.common.content_detection.models import ContentDetection, DetectionConfidence


class PureMagicDetector:
    async def detect_path(self, path: Path) -> Optional[ContentDetection]:
        try:
            import puremagic
        except Exception:
            return None

        try:
            result = await asyncio.to_thread(puremagic.magic_file, str(path))
        except Exception:
            return None

        return self._map_result(result)

    async def detect_bytes(self, content: bytes) -> Optional[ContentDetection]:
        try:
            import puremagic
        except Exception:
            return None

        try:
            result = await asyncio.to_thread(puremagic.magic_string, content)
        except Exception:
            return None

        return self._map_result(result)

    def _map_result(self, result: Any) -> Optional[ContentDetection]:
        match = _first_match(result)
        if match is None:
            return None

        mime_type = (_first_attr(match, ("mime_type", "mime_type_string", "mime", "mediatype")) or "application/octet-stream").lower()
        extension = _normalize_extension(_first_attr(match, ("extension", "ext")))
        label = (_first_attr(match, ("name", "confidence", "description")) or mime_type).lower()

        if "zip" in label or mime_type in {"application/zip", "application/x-zip-compressed"} or extension == ".zip":
            return None

        mapped = _map_mime_or_extension(mime_type, extension, label)
        if mapped is None:
            return None

        kind, normalized_mime, normalized_extension = mapped
        return ContentDetection(
            kind=kind,
            mime_type=normalized_mime or mime_type,
            extension=normalized_extension or extension,
            confidence=DetectionConfidence.PURE_MAGIC,
            reason=f"puremagic_{label}",
            detector="puremagic",
        )


def _first_match(result: Any) -> Any:
    if result is None:
        return None
    if isinstance(result, (str, bytes)):
        return result
    if isinstance(result, Iterable):
        for item in result:
            return item
        return None
    return result


def _first_attr(value: Any, names: Tuple[str, ...]) -> Optional[str]:
    if isinstance(value, str):
        if " " in value:
            return None
        return value
    for name in names:
        if isinstance(value, dict) and name in value:
            return _string_or_none(value[name])
        if hasattr(value, name):
            return _string_or_none(getattr(value, name))
    return None


def _string_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
