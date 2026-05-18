from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional


class AttachmentKind(str, Enum):
    IMAGE = "image"
    DIRECT_TEXT = "direct_text"
    BINARY_DOCUMENT = "binary_document"
    UNSUPPORTED_BINARY = "unsupported_binary"


class AttachmentStatus(str, Enum):
    READ = "read"
    UNSUPPORTED = "unsupported"
    ERROR = "error"
    DEFERRED = "deferred"
    DOCUMENT_PARSE_REQUIRED = "document_parse_required"
    OCR_COMPLETED = "ocr_completed"
    OCR_FAILED = "ocr_failed"


@dataclass(frozen=True, slots=True)
class AttachmentReadRequest:
    session_id: str
    user_id: str
    attachment_refs: List[str]
    purpose: Optional[str] = None


@dataclass(frozen=True, slots=True)
class ResolvedAttachment:
    attachment_ref: str
    file_name: str
    mime_type: Optional[str]
    size_bytes: int
    local_path: Path
    enable_image: bool = False


@dataclass(frozen=True, slots=True)
class AttachmentReadItem:
    attachment_ref: str
    file_name: str
    mime_type: str
    size_bytes: int
    kind: str
    status: str
    content_block: Optional[str] = None
    preview: Optional[str] = None
    ocr_content_block: Optional[str] = None
    ocr_preview: Optional[str] = None
    image_ref: Optional[str] = None
    image_available_for_vision: bool = False
    file_ref: Optional[str] = None
    error: Optional[str] = None


@dataclass(frozen=True, slots=True)
class AttachmentReadResult:
    items: List[AttachmentReadItem]
