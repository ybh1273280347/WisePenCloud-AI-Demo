from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ContentKind(str, Enum):
    DOCUMENT = "document"
    IMAGE = "image"
    HTML = "html"
    JSON = "json"
    XML = "xml"
    TEXT = "text"
    UNSUPPORTED_ARCHIVE = "unsupported_archive"
    UNSUPPORTED_MEDIA = "unsupported_media"
    UNSUPPORTED_BINARY = "unsupported_binary"


class DetectionConfidence(str, Enum):
    MAGIC = "magic"
    CONTAINER = "container"
    AI = "ai"
    PURE_MAGIC = "pure_magic"
    TEXT = "text"
    HINT = "hint"
    FALLBACK = "fallback"


@dataclass(frozen=True, slots=True)
class DetectionHints:
    filename: Optional[str] = None
    declared_mime_type: Optional[str] = None
    content_type_header: Optional[str] = None
    content_disposition: Optional[str] = None
    source_uri: Optional[str] = None


@dataclass(frozen=True, slots=True)
class ContentDetection:
    kind: ContentKind
    mime_type: str
    extension: Optional[str]
    confidence: DetectionConfidence
    reason: str
    detector: str
