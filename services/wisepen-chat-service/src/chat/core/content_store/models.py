from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class ContentChunk:
    index: int
    start_offset: int
    end_offset: int
    token_count: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StoredContent:
    content_id: str
    scope_id: str
    producer: str
    source: str
    content_type: str
    text: str
    chunks: List[ContentChunk] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def session_id(self) -> str:
        return self.scope_id

    @property
    def tool_name(self) -> str:
        return self.producer


@dataclass(slots=True)
class ContentWindow:
    content_id: str
    producer: str
    source: str
    content_type: str
    original_length: int
    chunk_index: int = 0
    chunk_count: int = 1
    offset: int = 0
    returned_length: int = 0
    truncated: bool = False
    next_offset: Optional[int] = None
    text: str = ""
    error: Optional[str] = None
    cached: bool = True
    cache_error: Optional[str] = None
    warning: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def tool_name(self) -> str:
        return self.producer

    @property
    def content_cached(self) -> bool:
        return self.cached


@dataclass(slots=True)
class ContentReceipt:
    content_id: str
    producer: str
    source: str
    content_type: str
    original_length: int
    chunk_count: int
    cached: bool = True
    cache_error: Optional[str] = None
    error: Optional[str] = None
    warning: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def tool_name(self) -> str:
        return self.producer

    @property
    def content_cached(self) -> bool:
        return self.cached


StoredToolContent = StoredContent
WindowedContent = ContentWindow
