import hashlib
import uuid
from typing import Any, Dict, Optional

from common.logger import log_event

from .chunking import create_content_chunks, find_chunk_by_offset
from .models import ContentReceipt, ContentWindow, StoredContent
from .repository import TTLContentRepository


class ContentStore:
    def __init__(
        self,
        *,
        repository: TTLContentRepository,
        default_chunk_size: int,
        max_item_chars: int,
        normalize_text: bool = True,
    ):
        self._repository = repository
        self._default_chunk_size = max(1, default_chunk_size)
        self._max_item_chars = max(1, max_item_chars)
        self._normalize_text = normalize_text

    def put_content(
        self,
        *,
        scope_id: str,
        producer: str,
        source: str,
        text: str,
        content_type: str = "text/markdown",
        metadata: Optional[Dict[str, Any]] = None,
        chunk_size: Optional[int] = None,
    ) -> Optional[str]:
        self._repository.expire()

        if self._normalize_text:
            text = text.strip()

        if not text:
            return None

        if len(text) > self._max_item_chars:
            return None

        content_id = f"cnt_{uuid.uuid4().hex[:16]}"
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        safe_metadata: Dict[str, Any] = dict(metadata) if metadata else {}
        safe_metadata["content_hash"] = content_hash

        effective_chunk_size = (
            chunk_size if chunk_size is not None else self._default_chunk_size
        )
        effective_chunk_size = max(1, effective_chunk_size)

        chunks = create_content_chunks(
            text, effective_chunk_size, content_type=content_type
        )

        stored = StoredContent(
            content_id=content_id,
            scope_id=scope_id,
            producer=producer,
            source=source,
            content_type=content_type,
            text=text,
            chunks=chunks,
            metadata=safe_metadata,
        )

        self._repository.put(stored)
        return content_id

    def get_content(
        self,
        *,
        content_id: str,
        scope_id: str,
    ) -> Optional[StoredContent]:
        self._repository.expire()
        item = self._repository.get(content_id)

        if item is None:
            return None

        if item.scope_id != scope_id:
            return None

        return item

    def put_content_receipt(
        self,
        *,
        scope_id: str,
        producer: str,
        source: str,
        text: str,
        content_type: str = "application/json",
        metadata: Optional[Dict[str, Any]] = None,
        chunk_size: Optional[int] = None,
    ) -> Optional[ContentReceipt]:
        content_id = self.put_content(
            scope_id=scope_id,
            producer=producer,
            source=source,
            text=text,
            content_type=content_type,
            metadata=metadata,
            chunk_size=chunk_size,
        )

        if content_id is None:
            return None

        stored = self.get_content(content_id=content_id, scope_id=scope_id)
        if stored is None:
            return None

        return ContentReceipt(
            content_id=stored.content_id,
            producer=stored.producer,
            source=stored.source,
            content_type=stored.content_type,
            original_length=len(stored.text),
            chunk_count=len(stored.chunks),
            cached=True,
            metadata=dict(stored.metadata),
        )

    def read_window(
        self,
        *,
        content_id: str,
        scope_id: str,
        offset: int = 0,
        limit: Optional[int] = None,
    ) -> Optional[ContentWindow]:
        item = self.get_content(content_id=content_id, scope_id=scope_id)

        if item is None:
            return None

        original_length = len(item.text)
        offset = max(0, offset)
        effective_limit = limit if limit is not None else self._default_chunk_size
        effective_limit = max(1, effective_limit)

        if offset >= original_length:
            return ContentWindow(
                content_id=content_id,
                producer=item.producer,
                source=item.source,
                content_type=item.content_type,
                original_length=original_length,
                chunk_count=len(item.chunks),
                offset=offset,
                returned_length=0,
                truncated=False,
                next_offset=None,
                text="",
                error="offset_out_of_range",
            )

        if effective_limit == self._default_chunk_size:
            chunks = item.chunks
        else:
            chunks = create_content_chunks(
                item.text, effective_limit, content_type=item.content_type
            )

        chunk = find_chunk_by_offset(chunks, offset)

        if chunk is None:
            return ContentWindow(
                content_id=content_id,
                producer=item.producer,
                source=item.source,
                content_type=item.content_type,
                original_length=original_length,
                chunk_count=len(chunks),
                offset=offset,
                returned_length=0,
                truncated=False,
                next_offset=None,
                text="",
                error="chunk_not_found",
            )

        next_offset = chunk.end_offset if chunk.index < len(chunks) - 1 else None
        window_text = item.text[chunk.start_offset : chunk.end_offset]
        returned_length = len(window_text)
        truncated = chunk.index < len(chunks) - 1

        log_event(
            "分段读取进行中",
            content_id=content_id,
            chunk_id=chunk.index,
            chunk_count=len(chunks),
            offset=chunk.start_offset,
            returned_length=returned_length,
            truncated=truncated,
            next_offset=next_offset,
        )

        return ContentWindow(
            content_id=content_id,
            producer=item.producer,
            source=item.source,
            content_type=item.content_type,
            original_length=original_length,
            chunk_index=chunk.index,
            chunk_count=len(chunks),
            offset=chunk.start_offset,
            returned_length=returned_length,
            truncated=truncated,
            next_offset=next_offset,
            text=window_text,
        )

    def put_and_read_window(
        self,
        *,
        scope_id: str,
        producer: str,
        source: str,
        text: str,
        content_type: str = "text/markdown",
        metadata: Optional[Dict[str, Any]] = None,
        offset: int = 0,
        limit: Optional[int] = None,
    ) -> ContentWindow:
        content_id = self.put_content(
            scope_id=scope_id,
            producer=producer,
            source=source,
            text=text,
            content_type=content_type,
            metadata=metadata,
        )

        if content_id:
            window = self.read_window(
                content_id=content_id,
                scope_id=scope_id,
                offset=offset,
                limit=limit,
            )
            if window is not None:
                return window

        # This fallback still relies on chunking. If chunk alignment fails, let the
        # error propagate to the tool execution boundary instead of returning an
        # unsafe window.
        return create_uncached_window(
            text=text,
            producer=producer,
            source=source,
            content_type=content_type,
            offset=offset,
            limit=limit if limit is not None else self._default_chunk_size,
            cache_error="content_too_large",
            normalize_text=self._normalize_text,
        )

    def read_chunk_window(
        self,
        *,
        content_id: str,
        scope_id: str,
        chunk_index: int,
        before_chunks: int = 0,
        after_chunks: int = 0,
    ) -> Optional[ContentWindow]:
        item = self.get_content(content_id=content_id, scope_id=scope_id)

        if item is None:
            return None

        chunks = item.chunks

        if chunk_index < 0 or chunk_index >= len(chunks):
            return ContentWindow(
                content_id=content_id,
                producer=item.producer,
                source=item.source,
                content_type=item.content_type,
                original_length=len(item.text),
                chunk_index=chunk_index,
                chunk_count=len(chunks),
                error="chunk_index_out_of_range",
            )

        before_chunks = max(0, before_chunks)
        after_chunks = max(0, after_chunks)

        start_chunk = max(0, chunk_index - before_chunks)
        end_chunk = min(len(chunks) - 1, chunk_index + after_chunks)

        start_offset = chunks[start_chunk].start_offset
        end_offset = chunks[end_chunk].end_offset

        window_text = item.text[start_offset:end_offset]
        returned_length = len(window_text)
        truncated = end_chunk < len(chunks) - 1
        next_offset = chunks[end_chunk].end_offset if truncated else None

        return ContentWindow(
            content_id=content_id,
            producer=item.producer,
            source=item.source,
            content_type=item.content_type,
            original_length=len(item.text),
            chunk_index=chunk_index,
            chunk_count=len(chunks),
            offset=start_offset,
            returned_length=returned_length,
            truncated=truncated,
            next_offset=next_offset,
            text=window_text,
            metadata={
                "start_chunk_index": start_chunk,
                "end_chunk_index": end_chunk,
            },
        )


def create_uncached_window(
    *,
    text: str,
    producer: str,
    source: str,
    content_type: str = "text/markdown",
    offset: int = 0,
    limit: int = 4000,
    cache_error: str = "content_too_large",
    normalize_text: bool = True,
) -> ContentWindow:
    if normalize_text:
        text = text.strip()

    original_length = len(text)

    if not text:
        return ContentWindow(
            content_id="",
            producer=producer,
            source=source,
            content_type=content_type,
            original_length=0,
            truncated=False,
            text="",
            error="empty_content",
            cached=False,
            cache_error="empty_content",
        )

    chunks = create_content_chunks(text, limit, content_type=content_type)

    chunk = find_chunk_by_offset(chunks, offset)

    if chunk is None:
        return ContentWindow(
            content_id="",
            producer=producer,
            source=source,
            content_type=content_type,
            original_length=original_length,
            chunk_count=len(chunks),
            offset=offset,
            truncated=False,
            text="",
            error="offset_out_of_range",
            cached=False,
            cache_error=cache_error,
        )

    window_text = text[chunk.start_offset : chunk.end_offset]
    returned_length = len(window_text)
    truncated = chunk.index < len(chunks) - 1

    return ContentWindow(
        content_id="",
        producer=producer,
        source=source,
        content_type=content_type,
        original_length=original_length,
        chunk_index=chunk.index,
        chunk_count=len(chunks),
        offset=chunk.start_offset,
        returned_length=returned_length,
        truncated=truncated,
        next_offset=None,
        text=window_text,
        cached=False,
        cache_error=cache_error,
        warning="Full content was too large to cache. Answer conservatively because the complete document is not available from cache.",
    )
