import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from cachetools import TTLCache
from langchain_text_splitters import RecursiveCharacterTextSplitter

from chat.core.config.app_settings import settings


@dataclass(slots=True)
class ContentChunk:
    index: int
    start_offset: int
    end_offset: int


@dataclass(slots=True)
class StoredToolContent:
    content_id: str
    session_id: str
    tool_name: str
    source: str
    content_type: str
    text: str
    chunks: List[ContentChunk] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WindowedContent:
    content_id: str
    tool_name: str
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
    content_cached: bool = True
    cache_error: Optional[str] = None
    warning: Optional[str] = None


def _create_content_chunks(text: str, chunk_size: int) -> List[ContentChunk]:
    if not text:
        return []

    chunk_size = max(1, chunk_size)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=0,
        separators=["\n\n", "\n", " ", ""],
        length_function=len,
        add_start_index=True,
        strip_whitespace=False,
    )

    documents = [doc for doc in splitter.create_documents([text]) if doc.page_content]

    chunks: List[ContentChunk] = []

    for index, doc in enumerate(documents):
        start = doc.metadata["start_index"]
        end = start + len(doc.page_content)
        chunks.append(
            ContentChunk(
                index=index,
                start_offset=start,
                end_offset=end,
            )
        )

    return chunks


def _find_chunk_by_offset(chunks: List[ContentChunk], offset: int) -> Optional[ContentChunk]:
    for chunk in chunks:
        if chunk.end_offset > offset:
            return chunk
    return None


def _assemble_windowed_content(
    *,
    text: str,
    chunks: List[ContentChunk],
    chunk: ContentChunk,
    content_id: str,
    tool_name: str,
    source: str,
    content_type: str,
    original_length: int,
    next_offset: Optional[int] = None,
    content_cached: bool = True,
    cache_error: Optional[str] = None,
    warning: Optional[str] = None,
    error: Optional[str] = None,
) -> WindowedContent:
    chunk_text = text[chunk.start_offset:chunk.end_offset].strip()
    truncated = chunk.index < len(chunks) - 1

    return WindowedContent(
        content_id=content_id,
        tool_name=tool_name,
        source=source,
        content_type=content_type,
        original_length=original_length,
        chunk_index=chunk.index,
        chunk_count=len(chunks),
        offset=chunk.start_offset,
        returned_length=len(chunk_text),
        truncated=truncated,
        next_offset=next_offset,
        text=chunk_text,
        content_cached=content_cached,
        cache_error=cache_error,
        warning=warning,
        error=error,
    )


def create_uncached_window(
    *,
    text: str,
    tool_name: str,
    source: str,
    content_type: str = "text/markdown",
    offset: int = 0,
    limit: int = 4000,
    cache_error: str = "content_too_large",
) -> WindowedContent:
    stripped = text.strip()
    original_length = len(stripped)
    offset = max(0, offset)
    limit = max(1, limit)

    if not stripped:
        return WindowedContent(
            content_id="",
            tool_name=tool_name,
            source=source,
            content_type=content_type,
            original_length=0,
            truncated=False,
            text="",
            error="empty_content",
            content_cached=False,
            cache_error="empty_content",
        )

    chunks = _create_content_chunks(stripped, limit)

    chunk = _find_chunk_by_offset(chunks, offset)

    if chunk is None:
        return WindowedContent(
            content_id="",
            tool_name=tool_name,
            source=source,
            content_type=content_type,
            original_length=original_length,
            truncated=False,
            text="",
            error="offset_out_of_range",
            content_cached=False,
            cache_error=cache_error,
        )

    return _assemble_windowed_content(
        text=stripped,
        chunks=chunks,
        chunk=chunk,
        content_id="",
        tool_name=tool_name,
        source=source,
        content_type=content_type,
        original_length=original_length,
        next_offset=None,
        content_cached=False,
        cache_error=cache_error,
        warning="Full content was too large to cache. Answer conservatively because the complete document is not available from cache.",
    )


def format_windowed_content(window: WindowedContent) -> str:
    next_offset = "" if window.next_offset is None else str(window.next_offset)
    chunk_count = str(window.chunk_count)

    metadata_lines = [
        "[ToolContent Metadata]",
        f"content_id: {window.content_id}",
        f"content_cached: {str(window.content_cached).lower()}",
    ]

    if window.cache_error:
        metadata_lines.append(f"cache_error: {window.cache_error}")

    metadata_lines.extend([
        f"tool_name: {window.tool_name}",
        f"source: {window.source}",
        f"content_type: {window.content_type}",
        f"original_length: {window.original_length}",
        f"chunk_index: {window.chunk_index}",
        f"chunk_count: {chunk_count}",
        f"offset: {window.offset}",
        f"returned_length: {window.returned_length}",
        f"truncated: {str(window.truncated).lower()}",
        f"next_offset: {next_offset}",
    ])

    if window.error:
        metadata_lines.append(f"error: {window.error}")

    if window.warning:
        metadata_lines.append(f"warning: {window.warning}")

    return "\n".join(metadata_lines) + "\n\n[Content]\n" + window.text


class ToolContentStore:

    def __init__(
        self,
        ttl_seconds: Optional[int] = None,
        max_total_chars: Optional[int] = None,
        default_chunk_size: Optional[int] = None,
    ):
        self._ttl_seconds = ttl_seconds if ttl_seconds is not None else settings.TOOL_CONTENT_STORE_TTL_SECONDS
        self._max_total_chars = max_total_chars if max_total_chars is not None else settings.TOOL_CONTENT_STORE_MAX_TOTAL_CHARS
        self._default_chunk_size = default_chunk_size if default_chunk_size is not None else settings.TOOL_RESULT_MAX_CHARS

        self._items: TTLCache[str, StoredToolContent] = TTLCache(
            maxsize=self._max_total_chars,
            ttl=self._ttl_seconds,
            getsizeof=lambda item: len(item.text),
        )

    def put(
        self,
        *,
        session_id: str,
        tool_name: str,
        source: str,
        text: str,
        content_type: str = "text/markdown",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        self._items.expire()

        text = text.strip()

        if not text:
            return None

        if len(text) > self._max_total_chars:
            return None

        content_id = f"{tool_name}:{uuid.uuid4().hex[:16]}"

        chunks = _create_content_chunks(text, self._default_chunk_size)

        self._items[content_id] = StoredToolContent(
            content_id=content_id,
            session_id=session_id,
            tool_name=tool_name,
            source=source,
            content_type=content_type,
            text=text,
            chunks=chunks,
            metadata=metadata or {},
        )

        return content_id

    def get(self, *, content_id: str, session_id: str) -> Optional[StoredToolContent]:
        self._items.expire()
        item = self._items.get(content_id)

        if item is None:
            return None

        if item.session_id != session_id:
            return None

        return item

    def read_window(
        self,
        *,
        content_id: str,
        session_id: str,
        offset: int,
        limit: int,
    ) -> Optional[WindowedContent]:
        item = self.get(content_id=content_id, session_id=session_id)

        if item is None:
            return None

        original_length = len(item.text)
        offset = max(0, offset)
        chunk_size = max(1, limit)

        if offset >= original_length:
            return WindowedContent(
                content_id=content_id,
                tool_name=item.tool_name,
                source=item.source,
                content_type=item.content_type,
                original_length=original_length,
                offset=offset,
                returned_length=0,
                truncated=False,
                next_offset=None,
                text="",
                error="offset_out_of_range",
            )

        if chunk_size == self._default_chunk_size:
            chunks = item.chunks
        else:
            chunks = _create_content_chunks(item.text, chunk_size)

        chunk = _find_chunk_by_offset(chunks, offset)

        if chunk is None:
            return WindowedContent(
                content_id=content_id,
                tool_name=item.tool_name,
                source=item.source,
                content_type=item.content_type,
                original_length=original_length,
                offset=offset,
                returned_length=0,
                truncated=False,
                next_offset=None,
                text="",
                error="chunk_not_found",
            )

        next_offset = chunk.end_offset if chunk.index < len(chunks) - 1 else None

        return _assemble_windowed_content(
            text=item.text,
            chunks=chunks,
            chunk=chunk,
            content_id=content_id,
            tool_name=item.tool_name,
            source=item.source,
            content_type=item.content_type,
            original_length=original_length,
            next_offset=next_offset,
        )


tool_content_store = ToolContentStore()


def cache_and_window(
    *,
    session_id: str,
    tool_name: str,
    source: str,
    text: str,
    content_type: str = "text/markdown",
    metadata: Optional[Dict[str, Any]] = None,
    offset: int = 0,
    limit: int = 4000,
) -> WindowedContent:
    content_id = tool_content_store.put(
        session_id=session_id,
        tool_name=tool_name,
        source=source,
        text=text,
        content_type=content_type,
        metadata=metadata,
    )

    if content_id:
        window = tool_content_store.read_window(
            content_id=content_id,
            session_id=session_id,
            offset=offset,
            limit=limit,
        )
        if window is not None:
            return window

    return create_uncached_window(
        text=text,
        tool_name=tool_name,
        source=source,
        content_type=content_type,
        offset=offset,
        limit=limit,
        cache_error="content_too_large",
    )


def cache_and_format(
    *,
    session_id: str,
    tool_name: str,
    source: str,
    text: str,
    content_type: str = "text/markdown",
    metadata: Optional[Dict[str, Any]] = None,
    offset: int = 0,
    limit: int = 4000,
) -> str:
    window = cache_and_window(
        session_id=session_id,
        tool_name=tool_name,
        source=source,
        text=text,
        content_type=content_type,
        metadata=metadata,
        offset=offset,
        limit=limit,
    )
    return format_windowed_content(window)


def read_tool_content_window(
    *,
    session_id: str,
    content_id: str,
    offset: int = 0,
    limit: Optional[int] = None,
) -> str:
    content_id = content_id.strip()

    if not content_id:
        return "[Tool Error] Missing required content_id parameter"

    if limit is None:
        limit = settings.TOOL_RESULT_MAX_CHARS

    offset = max(0, offset)
    limit = min(max(1, limit), settings.TOOL_RESULT_MAX_CHARS)

    window = tool_content_store.read_window(
        content_id=content_id,
        session_id=session_id,
        offset=offset,
        limit=limit,
    )

    if window is None:
        return "[Tool Result] Cached tool content not found, expired, or inaccessible."

    return format_windowed_content(window)
