from typing import Any, Dict, Optional

from chat.application.tools.common.tool_content_store import (
    read_tool_content_chunk_window,
    read_tool_content_window,
)
from chat.application.tools.config import TOOL_RESULT_MAX_CHARS
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_event

TOOL_DESCRIPTION = (
    "Reads a window of previously cached tool content by content_id. "
    "Use this for one known ToolContent window, either by sequential offset or by a ranked chunk_index.\n\n"
    "Use offset mode with content_id plus offset/limit to continue from next_offset.\n"
    "Use chunk_index mode with content_id plus chunk_index/before_chunks/after_chunks to inspect "
    "one ranked passage and its surrounding chunks.\n"
    "Use either offset mode or chunk_index mode, never both.\n"
    "Do not use tool_content_read for blind scanning when evidence_rank can rank the cached content.\n\n"
    "content_id values usually look like cnt_* and are not document file_refs.\n"
    "Never pass file_ref values to tool_content_read."
)

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "content_id": {
            "type": "string",
            "minLength": 1,
            "description": (
                "cnt_* ToolContent identifier. Do not pass file_ref values."
            ),
        },
        "offset": {
            "type": "integer",
            "minimum": 0,
            "description": (
                "Character offset for offset mode, usually next_offset from a previous window."
            ),
            "default": 0,
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "description": (
                "Maximum characters to return in offset mode."
            ),
        },
        "chunk_index": {
            "type": "integer",
            "minimum": 0,
            "description": (
                "Chunk index returned by evidence_rank for chunk_index mode."
            ),
        },
        "before_chunks": {
            "type": "integer",
            "minimum": 0,
            "maximum": 3,
            "default": 1,
            "description": "Number of chunks before chunk_index to include.",
        },
        "after_chunks": {
            "type": "integer",
            "minimum": 0,
            "maximum": 3,
            "default": 1,
            "description": "Number of chunks after chunk_index to include.",
        },
    },
    "required": ["content_id"],
    "additionalProperties": False,
}


class ToolContentReadTool(BaseTool):
    """长工具输出续读的 runtime adapter。

    核心读取逻辑位于 read_tool_content_window。
    它不是普通业务工具，而是让模型访问已缓存工具内容窗口的通用基础设施入口。
    """

    @property
    def name(self) -> str:
        return "tool_content_read"

    @property
    def description(self) -> str:
        return TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        content_id = kwargs.get("content_id")
        if type(content_id) is not str:
            return "[Tool Error] content_id must be a string."
        if not content_id:
            return "[Tool Error] content_id must be a non-empty string."
        if content_id.strip() != content_id:
            return (
                "[Tool Error] content_id must not contain leading or trailing "
                "whitespace."
            )
        if content_id.startswith("file_ref"):
            return (
                "[Tool Error] content_id must be a cnt_* value, not a file_ref "
                "value."
            )

        has_chunk_index = "chunk_index" in kwargs
        has_offset_mode_arg = "offset" in kwargs or "limit" in kwargs
        has_chunk_context_arg = (
            "before_chunks" in kwargs or "after_chunks" in kwargs
        )

        if has_chunk_index and has_offset_mode_arg:
            return (
                "[Tool Error] Use either chunk_index mode or offset mode, not both."
            )
        if has_chunk_context_arg and not has_chunk_index:
            return (
                "[Tool Error] before_chunks/after_chunks require chunk_index mode."
            )

        if has_chunk_index:
            chunk_index = kwargs.get("chunk_index")
            if type(chunk_index) is not int:
                return "[Tool Error] chunk_index must be an integer."
            if chunk_index < 0:
                return "[Tool Error] chunk_index must be greater than or equal to 0."

            before_chunks = kwargs.get("before_chunks", 1)
            if type(before_chunks) is not int:
                return "[Tool Error] before_chunks must be an integer."
            if before_chunks < 0 or before_chunks > 3:
                return "[Tool Error] before_chunks must be between 0 and 3."

            after_chunks = kwargs.get("after_chunks", 1)
            if type(after_chunks) is not int:
                return "[Tool Error] after_chunks must be an integer."
            if after_chunks < 0 or after_chunks > 3:
                return "[Tool Error] after_chunks must be between 0 and 3."

            log_event(
                "tool_content_read 调用",
                content_id=content_id,
                chunk_index=chunk_index,
                before_chunks=before_chunks,
                after_chunks=after_chunks,
            )

            return read_tool_content_chunk_window(
                session_id=session_id,
                content_id=content_id,
                chunk_index=chunk_index,
                before_chunks=before_chunks,
                after_chunks=after_chunks,
            )

        offset = kwargs.get("offset", 0)
        if type(offset) is not int:
            return "[Tool Error] offset must be an integer."
        if offset < 0:
            return "[Tool Error] offset must be greater than or equal to 0."

        limit = kwargs.get("limit")
        if limit is not None:
            if type(limit) is not int:
                return "[Tool Error] limit must be an integer."
            if limit < 1:
                return "[Tool Error] limit must be greater than or equal to 1."
            if limit > TOOL_RESULT_MAX_CHARS:
                return (
                    "[Tool Error] limit must be less than or equal to "
                    f"{TOOL_RESULT_MAX_CHARS}."
                )

        log_event(
            "tool_content_read 调用",
            content_id=content_id,
            offset=offset,
            limit=limit,
        )

        return read_tool_content_window(
            session_id=session_id,
            content_id=content_id,
            offset=offset,
            limit=limit,
        )
