from typing import Any, Dict, Optional

from chat.application.tools.config import TOOL_RESULT_MAX_CHARS
from chat.core.content_store import (
    ContentStore,
    ContentWindow,
    TTLContentRepository,
)
from chat.core.content_store.formatters import format_tool_content_window
from chat.core.content_store.models import (
    StoredContent,
)
from common.logger import log_event

_TOOL_CONTENT_STORE_TTL_SECONDS = 30 * 60
_TOOL_CONTENT_STORE_MAX_TOTAL_CHARS = 20_000_000
_TOOL_CONTENT_STORE_MAX_ITEM_CHARS = 20_000_000


class ToolContentStore:
    def __init__(
        self,
        ttl_seconds: Optional[int] = None,
        max_total_chars: Optional[int] = None,
        default_chunk_size: Optional[int] = None,
        max_item_chars: Optional[int] = None,
    ):
        self._store = ContentStore(
            repository=TTLContentRepository(
                ttl_seconds=ttl_seconds
                if ttl_seconds is not None
                else _TOOL_CONTENT_STORE_TTL_SECONDS,
                max_total_chars=max_total_chars
                if max_total_chars is not None
                else _TOOL_CONTENT_STORE_MAX_TOTAL_CHARS,
            ),
            default_chunk_size=default_chunk_size
            if default_chunk_size is not None
            else TOOL_RESULT_MAX_CHARS,
            max_item_chars=max_item_chars
            if max_item_chars is not None
            else _TOOL_CONTENT_STORE_MAX_ITEM_CHARS,
            normalize_text=True,
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
        return self._store.put_content(
            scope_id=session_id,
            producer=tool_name,
            source=source,
            text=text,
            content_type=content_type,
            metadata=metadata,
        )

    def get(self, *, content_id: str, session_id: str) -> Optional[StoredContent]:
        return self._store.get_content(
            content_id=content_id,
            scope_id=session_id,
        )

    def read_window(
        self,
        *,
        content_id: str,
        session_id: str,
        offset: int,
        limit: int,
    ) -> Optional[ContentWindow]:
        return self._store.read_window(
            content_id=content_id,
            scope_id=session_id,
            offset=offset,
            limit=limit,
        )

    def put_and_read_window(
        self,
        *,
        session_id: str,
        tool_name: str,
        source: str,
        text: str,
        content_type: str = "text/markdown",
        metadata: Optional[Dict[str, Any]] = None,
        offset: int = 0,
        limit: Optional[int] = None,
    ) -> ContentWindow:
        return self._store.put_and_read_window(
            scope_id=session_id,
            producer=tool_name,
            source=source,
            text=text,
            content_type=content_type,
            metadata=metadata,
            offset=offset,
            limit=limit,
        )

    def read_chunk_window(
        self,
        *,
        content_id: str,
        session_id: str,
        chunk_index: int,
        before_chunks: int = 0,
        after_chunks: int = 0,
    ) -> Optional[ContentWindow]:
        return self._store.read_chunk_window(
            content_id=content_id,
            scope_id=session_id,
            chunk_index=chunk_index,
            before_chunks=before_chunks,
            after_chunks=after_chunks,
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
    limit: Optional[int] = None,
) -> ContentWindow:
    return tool_content_store.put_and_read_window(
        session_id=session_id,
        tool_name=tool_name,
        source=source,
        text=text,
        content_type=content_type,
        metadata=metadata,
        offset=offset,
        limit=limit,
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
    limit: Optional[int] = None,
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
    return format_tool_content_window(window)


def read_tool_content_window(
    *,
    session_id: str,
    content_id: str,
    offset: int = 0,
    limit: Optional[int] = None,
) -> str:
    if limit is None:
        limit = TOOL_RESULT_MAX_CHARS

    offset = max(0, offset)
    limit = min(max(1, limit), TOOL_RESULT_MAX_CHARS)

    log_event(
        "分段读取请求",
        content_id=content_id,
        offset=offset,
        limit=limit,
    )

    window = tool_content_store.read_window(
        content_id=content_id,
        session_id=session_id,
        offset=offset,
        limit=limit,
    )

    if window is None:
        return "[Tool Result] Cached tool content not found, expired, or inaccessible."

    return format_tool_content_window(window)


format_windowed_content = format_tool_content_window
