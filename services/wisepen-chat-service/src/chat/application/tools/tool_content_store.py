from typing import Any, Dict, Optional

from chat.application.infra.content_store import ContentStore
from chat.application.infra.content_store.formatting import (
    format_tool_content_receipt,
    format_tool_content_window,
)
from chat.application.infra.content_store.models import (
    ContentReceipt,
    ContentWindow,
    StoredContent,
)
from chat.application.infra.content_store.repository import TTLContentRepository
from chat.core.config.app_settings import settings as app_settings

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
        """初始化 ToolContentStore。

        使用 ContentStore 和 TTLContentRepository 构建工具内容存储实例。

        Args:
            ttl_seconds: 缓存项的存活时间（秒），默认 30 分钟。
            max_total_chars: 缓存仓库允许存储的最大总字符数。
            default_chunk_size: 默认单分块最大字符跨度。
            max_item_chars: 单条内容允许存入的最大字符数。
        """
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
            else app_settings.TOOL_RESULT_MAX_CHARS,
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
        """缓存工具输出的文本内容。

        将工具输出的文本内容存储到缓存中，并返回对应的内容 ID。

        Args:
            session_id: 会话 ID，用于隔离不同会话的内容。
            tool_name: 产生内容的工具名称。
            source: 内容来源标识。
            text: 要缓存的内容文本。
            content_type: 内容的 MIME 类型，默认为 text/markdown。
            metadata: 可选的键值对元数据。

        Returns:
            缓存成功时返回内容 ID，失败时返回 None。
        """
        return self._store.put_content(
            scope_id=session_id,
            producer=tool_name,
            source=source,
            text=text,
            content_type=content_type,
            metadata=metadata,
        )

    def get(self, *, content_id: str, session_id: str) -> Optional[StoredContent]:
        """从缓存中获取已存储的工具内容。

        根据内容 ID 和会话 ID 从缓存中检索已存储的内容。

        Args:
            content_id: 内容 ID。
            session_id: 会话 ID，用于验证内容所属的会话。

        Returns:
            找到内容时返回 StoredContent 对象，未找到或已过期时返回 None。
        """
        return self._store.get_content(
            content_id=content_id,
            scope_id=session_id,
        )

    def put_receipt(
        self,
        *,
        session_id: str,
        tool_name: str,
        source: str,
        text: str,
        content_type: str = "application/json",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[ContentReceipt]:
        """缓存工具输出并返回结构化回执。

        将工具的输出内容缓存，并返回包含缓存详情（如内容 ID、长度、分块数等）的回执对象。

        Args:
            session_id: 会话 ID。
            tool_name: 产生内容的工具名称。
            source: 内容来源标识。
            text: 要缓存的内容文本。
            content_type: 内容的 MIME 类型，默认为 application/json。
            metadata: 可选的键值对元数据。

        Returns:
            缓存成功时返回 ContentReceipt 回执对象，失败时返回 None。
        """
        return self._store.put_content_receipt(
            scope_id=session_id,
            producer=tool_name,
            source=source,
            text=text,
            content_type=content_type,
            metadata=metadata,
        )

    def read_window(
        self,
        *,
        content_id: str,
        session_id: str,
        offset: int,
        limit: int,
    ) -> Optional[ContentWindow]:
        """从缓存中读取指定偏移量和长度的内容窗口。

        Args:
            content_id: 内容 ID。
            session_id: 会话 ID。
            offset: 读取起始位置的字符偏移量。
            limit: 返回内容的最大字符数。

        Returns:
            成功时返回 ContentWindow 窗口对象，内容不存在或已过期时返回 None。
        """
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
        """缓存工具输出并立即读取指定的内容窗口。

        先缓存工具输出的文本内容，然后根据偏移量和限制长度读取对应的内容窗口。
        如果内容因过大等原因未能缓存，会返回一个包含截断内容的未缓存窗口。

        Args:
            session_id: 会话 ID。
            tool_name: 产生内容的工具名称。
            source: 内容来源标识。
            text: 要缓存的内容文本。
            content_type: 内容的 MIME 类型，默认为 text/markdown。
            metadata: 可选的键值对元数据。
            offset: 读取起始位置的字符偏移量，默认为 0。
            limit: 返回内容的最大字符数，不指定则使用默认分块大小。

        Returns:
            包含请求内容片段的 ContentWindow 对象。
        """
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
        """以指定分块为中心读取内容窗口。

        根据分块索引定位内容，并可选地包含前后相邻的分块，返回合并后的内容窗口。

        Args:
            content_id: 内容 ID。
            session_id: 会话 ID。
            chunk_index: 目标分块的索引。
            before_chunks: 目标分块之前额外包含的分块数，默认为 0。
            after_chunks: 目标分块之后额外包含的分块数，默认为 0。

        Returns:
            成功时返回合并后的 ContentWindow 对象，内容不存在时返回 None。
        """
        return self._store.read_chunk_window(
            content_id=content_id,
            scope_id=session_id,
            chunk_index=chunk_index,
            before_chunks=before_chunks,
            after_chunks=after_chunks,
        )


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
    """缓存工具输出并返回可读的内容窗口。

    先将工具输出的文本内容缓存，然后按指定的偏移量和限制长度返回内容窗口。

    Args:
        session_id: 会话 ID。
        tool_name: 产生内容的工具名称。
        source: 内容来源标识。
        text: 要缓存的内容文本。
        content_type: 内容的 MIME 类型，默认为 text/markdown。
        metadata: 可选的键值对元数据。
        offset: 读取起始位置的字符偏移量，默认为 0。
        limit: 返回内容的最大字符数。

    Returns:
        包含请求内容片段的 ContentWindow 对象。
    """
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
    """缓存工具输出并返回格式化后的内容窗口字符串。

    将工具输出的文本内容缓存，按指定偏移量和限制长度读取内容窗口，然后格式化为可读的字符串。

    Args:
        session_id: 会话 ID。
        tool_name: 产生内容的工具名称。
        source: 内容来源标识。
        text: 要缓存的内容文本。
        content_type: 内容的 MIME 类型，默认为 text/markdown。
        metadata: 可选的键值对元数据。
        offset: 读取起始位置的字符偏移量，默认为 0。
        limit: 返回内容的最大字符数。

    Returns:
        格式化后的内容窗口字符串。
    """
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


def cache_artifact_and_format_receipt(
    *,
    session_id: str,
    tool_name: str,
    source: str,
    text: str,
    content_type: str = "application/json",
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """缓存工具产物并返回格式化后的回执字符串。

    将工具产出的结构化数据（如 JSON）缓存，并返回包含内容 ID、长度、分块数等详情的格式化回执。

    Args:
        session_id: 会话 ID。
        tool_name: 产生内容的工具名称。
        source: 内容来源标识。
        text: 要缓存的内容文本。
        content_type: 内容的 MIME 类型，默认为 application/json。
        metadata: 可选的键值对元数据。

    Returns:
        格式化后的回执字符串。缓存失败时返回错误提示信息。
    """
    receipt = tool_content_store.put_receipt(
        session_id=session_id,
        tool_name=tool_name,
        source=source,
        text=text,
        content_type=content_type,
        metadata=metadata,
    )

    if receipt is None:
        return "[Tool Error] Failed to cache tool artifact."

    return format_tool_content_receipt(receipt)


def read_tool_content_window(
    *,
    session_id: str,
    content_id: str,
    offset: int = 0,
    limit: Optional[int] = None,
) -> str:
    """读取已缓存的工具内容并返回格式化后的窗口字符串。

    根据内容 ID 从缓存中读取指定偏移量和长度的内容片段，并格式化为可读的字符串。
    如果内容不存在或已过期，返回错误提示信息。

    Args:
        session_id: 会话 ID。
        content_id: 内容 ID。
        offset: 读取起始位置的字符偏移量，默认为 0。
        limit: 返回内容的最大字符数，默认为系统配置的工具结果最大字符数。

    Returns:
        格式化后的内容窗口字符串。内容不可用时返回错误提示信息。
    """
    if limit is None:
        limit = TOOL_RESULT_MAX_CHARS

    window = tool_content_store.read_window(
        content_id=content_id,
        session_id=session_id,
        offset=offset,
        limit=limit,
    )

    if window is None:
        return "[Tool Result] Cached tool content not found, expired, or inaccessible."

    return format_tool_content_window(window)


def read_tool_content_chunk_window(
    *,
    session_id: str,
    content_id: str,
    chunk_index: int,
    before_chunks: int = 1,
    after_chunks: int = 1,
) -> str:
    """以指定分块为中心读取已缓存的工具内容并返回格式化后的窗口字符串。

    根据分块索引定位内容，并包含前后相邻的分块，将合并后的内容格式化为可读的字符串。

    Args:
        session_id: 会话 ID。
        content_id: 内容 ID。
        chunk_index: 目标分块的索引。
        before_chunks: 目标分块之前额外包含的分块数，默认为 1。
        after_chunks: 目标分块之后额外包含的分块数，默认为 1。

    Returns:
        格式化后的内容窗口字符串。内容不可用时返回错误提示信息。
    """
    window = tool_content_store.read_chunk_window(
        content_id=content_id,
        session_id=session_id,
        chunk_index=chunk_index,
        before_chunks=before_chunks,
        after_chunks=after_chunks,
    )

    if window is None:
        return "[Tool Result] Cached tool content not found, expired, or inaccessible."

    return format_tool_content_window(window)

tool_content_store = ToolContentStore()