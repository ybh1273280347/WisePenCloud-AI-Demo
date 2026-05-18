from typing import Any, Dict, Optional

from chat.application.tools.common.tool_content_store import read_tool_content_window
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_event

TOOL_DESCRIPTION = (
    "Reads a window of previously cached tool content by content_id. "
    "Use this after calling evidence_rank, when you need more context around a specific "
    "ranked passage. evidence_rank scores all chunks by relevance to find the best evidence; "
    "tool_content_read then lets you read surrounding content for any ranked passage.\n\n"
    "Call tool_content_read with content_id and offset=next_offset to continue reading "
    "sequential windows until truncated=false.\n\n"
    "content_id values usually look like cnt_* and are not document file_refs. Do not pass "
    "content_id values to document_parse, and do not pass file_ref values to tool_content_read."
)

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "content_id": {
            "type": "string",
            "minLength": 1,
            "description": (
                "The non-empty content_id returned in ToolContent Metadata, usually a cnt_* identifier. "
                "Do not use file_ref values."
            ),
        },
        "offset": {
            "type": "integer",
            "minimum": 0,
            "description": (
                "Character offset to read from. Use next_offset from the previous ToolContent window "
                "when continuing."
            ),
            "default": 0,
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "description": (
                "Maximum number of characters to return. Defaults to TOOL_RESULT_MAX_CHARS and is "
                "capped by TOOL_RESULT_MAX_CHARS."
            ),
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

        content_id = kwargs["content_id"]
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("limit")

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
