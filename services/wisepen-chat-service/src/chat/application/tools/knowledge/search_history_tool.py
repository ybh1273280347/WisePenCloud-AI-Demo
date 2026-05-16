from datetime import datetime
from typing import Any, Dict, Optional

from chat.application.tool_content_store import cache_and_format
from chat.application.tools.config import TOOL_RESULT_MAX_CHARS
from chat.domain.interfaces.tool import BaseTool
from chat.domain.repositories import MessageRepository
from common.logger import log_error


class SearchHistoricalMessagesTool(BaseTool):
    """
    历史消息全文检索工具。
    Schema 中不暴露 session_id，该字段由系统通过 context 强注入，防止 LLM 幻觉伪造导致越权访问。
    """

    def __init__(self, message_repo: MessageRepository) -> None:
        self._message_repo = message_repo

    @property
    def name(self) -> str:
        return "search_historical_messages"

    @property
    def description(self) -> str:
        return (
            "Search historical chat messages by keyword and optional time range. "
            "Use this when you need to recall specific facts, events, or details "
            "from earlier in the conversation that may not be in the current context window."
            "NOTE that the search keyword's language should match the user's chat language;otherwise, the search may fail. If no results are found, consider switching the keyword's language. "
            "Long results are returned as ToolContent windows; continue with tool_content_read "
            "using content_id and next_offset when truncated=true."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        # session_id 故意不暴露，由系统通过 context 注入
        return {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "The keyword or phrase to search for in message history. The keyword argument must be in the same language as the user's query.",
                },
                "start_time": {
                    "type": "string",
                    "description": "ISO 8601 start time for filtering messages (optional).",
                },
                "end_time": {
                    "type": "string",
                    "description": "ISO 8601 end time for filtering messages (optional).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return. Defaults to 10.",
                    "default": 10,
                },
            },
            "required": ["keyword"],
        }

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        keyword: str = kwargs["keyword"]

        start_time: Optional[datetime] = None
        end_time: Optional[datetime] = None
        if kwargs.get("start_time"):
            try:
                start_time = datetime.fromisoformat(kwargs["start_time"])
            except ValueError:
                return "[Tool Error] Invalid start_time format. Expected ISO 8601 datetime string."
        if kwargs.get("end_time"):
            try:
                end_time = datetime.fromisoformat(kwargs["end_time"])
            except ValueError:
                return "[Tool Error] Invalid end_time format. Expected ISO 8601 datetime string."

        limit = kwargs.get("limit", 10)

        try:
            results = await self._message_repo.full_text_search(
                keyword=keyword,
                session_id=session_id,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )
        except Exception as e:
            log_error("历史消息全文检索", e, session=session_id, keyword=keyword)
            return f"[Tool Error] Search failed: {e}"

        if not results:
            return f"[Tool Result] No messages found for keyword: '{keyword}'."

        lines = [
            f"[{m.role.value}] ({m.created_at.isoformat()}): {m.content}"
            for m in results
        ]
        raw = "\n".join(lines)

        return cache_and_format(
            session_id=session_id,
            tool_name=self.name,
            source=f"keyword:{keyword}",
            text=raw,
            content_type="text/plain",
            metadata={
                "keyword": keyword,
                "result_count": len(results),
                "start_time": start_time.isoformat() if start_time else None,
                "end_time": end_time.isoformat() if end_time else None,
            },
            limit=TOOL_RESULT_MAX_CHARS,
        )
