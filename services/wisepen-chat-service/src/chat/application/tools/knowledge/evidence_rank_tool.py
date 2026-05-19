import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Dict, Optional

from chat.application.tools.services.evidence_ranking import (
    format_evidence_result,
    rank_evidence,
)
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_event, log_fail, log_ok

_EVIDENCE_RANK_EXECUTOR = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="evidence-rank",
)


TOOL_DESCRIPTION = (
    "Scores and ranks all chunks from cached tool content by relevance to the user's question "
    "using BM25 text matching, then returns the top-ranked evidence snippets.\n\n"
    "When a tool returns ToolContent Metadata with content_cached=true and content_id values, "
    "the full content has been split into many chunks and stored. You cannot know which chunks "
    "are relevant by reading them sequentially. evidence_rank solves this by scoring every chunk "
    "against the user's question and returning only the most relevant ones, ranked by score.\n\n"
    "You MUST call evidence_rank before answering when cached content_ids are available. "
    "Do not answer directly from the first truncated window — it may not contain the key evidence. "
    "Do not use tool_content_read to scan chunks one by one hoping to find relevant content — "
    "use evidence_rank first to locate the best evidence, then use tool_content_read only when "
    "you need more context around a specific ranked passage.\n\n"
    "Do not use evidence_rank to discover URLs, fetch pages, parse file_ref values, or continue "
    "reading from a known offset. Use tool_content_read for sequential continuation.\n\n"
    "This tool returns ranked evidence snippets with excerpts, not a final answer. Use the "
    "evidence snippets to answer the user with citations or source attribution."
)

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "description": (
                "The user's question or information need used to rank evidence snippets. "
                "Use the user's current question as-is when possible. If you must rewrite it, "
                "follow any explicit response-language request first, otherwise use the current "
                "message language, and use the preferred locale only when the message language "
                "is ambiguous."
            ),
        },
        "content_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "description": (
                "content_id values from previous ToolContent Metadata, usually cnt_* identifiers. "
                "Do not pass file_ref values."
            ),
        },
        "max_evidence": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "description": "Maximum number of ranked evidence snippets to return.",
            "default": 8,
        },
    },
    "required": ["query", "content_ids"],
    "additionalProperties": False,
}


class EvidenceRankTool(BaseTool):
    @property
    def name(self) -> str:
        return "evidence_rank"

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

        query = kwargs.get("query")
        if type(query) is not str:
            return "[Tool Error] query must be a string."
        if not query:
            return "[Tool Error] query must be a non-empty string."
        if query.strip() != query:
            return (
                "[Tool Error] query must not contain leading or trailing whitespace."
            )

        content_ids = kwargs.get("content_ids")
        if not isinstance(content_ids, list):
            return "[Tool Error] content_ids must be a list of strings."
        if not content_ids:
            return "[Tool Error] content_ids must contain at least one item."
        for cid in content_ids:
            if type(cid) is not str:
                return "[Tool Error] content_ids items must be strings."
            if not cid:
                return "[Tool Error] content_ids items must be non-empty strings."
            if cid.strip() != cid:
                return (
                    "[Tool Error] content_ids items must not contain leading or "
                    "trailing whitespace."
                )
            if cid.startswith("file_ref"):
                return "[Tool Error] content_ids must be cnt_* values, not file_ref values."

        max_evidence = kwargs.get("max_evidence", 8)
        if type(max_evidence) is not int:
            return "[Tool Error] max_evidence must be an integer."
        if max_evidence < 1 or max_evidence > 20:
            return "[Tool Error] max_evidence must be between 1 and 20."

        log_event(
            "evidence_rank 开始执行",
            session_id=session_id,
            query=query[:80],
            content_ids=len(content_ids),
            max_evidence=max_evidence,
        )

        t0 = time.monotonic()
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                _EVIDENCE_RANK_EXECUTOR,
                partial(
                    rank_evidence,
                    query=query,
                    content_ids=content_ids,
                    session_id=session_id,
                    max_evidence=max_evidence,
                ),
            )
        except Exception as e:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            log_fail(
                "evidence_rank",
                e,
                session_id=session_id,
                query=query[:80],
                content_ids=len(content_ids),
                elapsed_ms=elapsed_ms,
            )
            return "[Tool Error] Unexpected error while ranking evidence."

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        log_ok(
            "evidence_rank",
            session_id=session_id,
            evidence_count=len(result.evidence),
            chunks_scanned=result.total_chunks_scanned,
            elapsed_ms=elapsed_ms,
        )

        return format_evidence_result(result)

    async def close(self) -> None:
        _EVIDENCE_RANK_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        log_event("EvidenceRankTool 关闭")
