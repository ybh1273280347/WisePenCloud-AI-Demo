from typing import Any, Dict, Optional

from chat.domain.interfaces.tool import BaseTool
from chat.application.tool_content_store import read_tool_content_window


TOOL_DESCRIPTION = (
    "Read a window of previously cached tool content by content_id. "
    "Use this only when another tool returns ToolContent Metadata with content_cached=true, "
    "truncated=true, a non-empty content_id, and next_offset. "
    "Do not use this tool when content_cached=false or content_id is empty. "
    "For full-document reading or summary, continue calling this tool with offset=next_offset until truncated=false."
)

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "content_id": {
            "type": "string",
            "description": "The non-empty content_id returned in ToolContent Metadata.",
        },
        "offset": {
            "type": "integer",
            "description": "Character offset to continue reading from.",
            "default": 0,
        },
        "limit": {
            "type": "integer",
            "description": (
                "Maximum number of characters to return. "
                "Defaults to TOOL_RESULT_MAX_CHARS and is capped by TOOL_RESULT_MAX_CHARS."
            ),
        },
    },
    "required": ["content_id"],
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

        return read_tool_content_window(
            session_id=session_id,
            content_id=kwargs["content_id"],
            offset=kwargs.get("offset", 0),
            limit=kwargs.get("limit"),
        )
