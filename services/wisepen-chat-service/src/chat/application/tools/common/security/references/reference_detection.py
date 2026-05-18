import re
from typing import Optional

from .models import InternalReferenceKind

_CONTENT_ID_PATTERN = re.compile(r"^cnt_[A-Za-z0-9_-]+$")
_ATTACHMENT_REF_PATTERN = re.compile(r"^att_[A-Za-z0-9_-]+$")
_IMAGE_REF_PATTERN = re.compile(r"^(img|image)_[A-Za-z0-9_-]+$")

_DOWNLOAD_REF_PREFIXES = (
    "download_ref:",
    "download:",
    "generated:",
)

_FILE_REF_PREFIXES = (
    "file_ref:",
    "handoff:",
)

_URL_PREFIXES = (
    "http://",
    "https://",
)


def _normalize(value: str) -> str:
    return value.strip()


def looks_like_content_id(value: str) -> bool:
    text = _normalize(value)
    return bool(_CONTENT_ID_PATTERN.match(text))


def looks_like_file_ref(value: str) -> bool:
    text = _normalize(value)
    lowered = text.lower()

    if lowered.startswith(_URL_PREFIXES):
        return False

    return any(lowered.startswith(prefix) for prefix in _FILE_REF_PREFIXES)


def looks_like_download_ref(value: str) -> bool:
    text = _normalize(value)
    lowered = text.lower()
    return any(lowered.startswith(prefix) for prefix in _DOWNLOAD_REF_PREFIXES)


def looks_like_attachment_ref(value: str) -> bool:
    text = _normalize(value)
    return bool(_ATTACHMENT_REF_PATTERN.match(text))


def looks_like_image_ref(value: str) -> bool:
    text = _normalize(value)
    return bool(_IMAGE_REF_PATTERN.match(text))


def detect_reference_kind(value: str) -> Optional[InternalReferenceKind]:
    text = _normalize(value)

    if not text:
        return None

    lowered = text.lower()
    if lowered.startswith(_URL_PREFIXES):
        return None

    if looks_like_content_id(text):
        return InternalReferenceKind.CONTENT_ID

    if looks_like_download_ref(text):
        return InternalReferenceKind.DOWNLOAD_REF

    if looks_like_attachment_ref(text):
        return InternalReferenceKind.ATTACHMENT_REF

    if looks_like_image_ref(text):
        return InternalReferenceKind.IMAGE_REF

    if looks_like_file_ref(text):
        return InternalReferenceKind.FILE_REF

    return None


def reject_non_url_reference(value: str) -> Optional[str]:
    kind = detect_reference_kind(value)
    return kind.value if kind is not None else None
