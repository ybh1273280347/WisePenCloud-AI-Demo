from __future__ import annotations

from typing import Any, Dict, Optional

from chat.application.tools.services.paper_search import (
    PaperSearchDepth,
    PaperSearchFreshness,
    PaperSearchRequest,
    PaperSearchService,
)
from chat.application.tools.services.paper_search.config import (
    PAPER_SEARCH_TOOL_RESULT_MAX_CHARS,
)
from chat.application.tools.services.paper_search.formatting import (
    format_paper_search_response,
    truncate_result,
)
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_error, log_event

_TOOL_DESCRIPTION = (
    "Searches scholarly papers through Paper Search v1.5. This is an Agent Tool: "
    "it only follows explicit freshness, depth, and query_variants parameters and does not infer "
    "latest/stable/deep intent from query text. Exa is the discovery layer; arXiv and DOI metadata "
    "are used for structured hydration when enabled. For non-English user queries, pass concise "
    "English academic keywords and optional explicit variants."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Concise academic search keywords. For non-English user requests, translate the "
                "concept into English before calling."
            ),
        },
        "max_results": {
            "type": "integer",
            "default": 8,
            "minimum": 1,
            "maximum": 8,
        },
        "freshness": {
            "type": "string",
            "enum": ["latest", "balanced", "stable"],
            "default": "balanced",
            "description": (
                "Explicit freshness mode. latest enables arXiv Delta Index and Exa date filtering; "
                "balanced and stable do not infer recency from query text."
            ),
        },
        "depth": {
            "type": "string",
            "enum": ["fast", "deep"],
            "default": "deep",
            "description": "Explicit search depth. deep enables one-hop findSimilar expansion.",
        },
        "query_variants": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
            "description": "Optional Agent-provided query rewrites. The tool does not generate rewrites.",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


class PaperSearchTool(BaseTool):
    def __init__(self, service: Optional[PaperSearchService] = None) -> None:
        self._service = service or PaperSearchService()

    @property
    def name(self) -> str:
        return "paper_search"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        query = kwargs.get("query")
        if not isinstance(query, str) or not query.strip():
            return "[Tool Error] query is required."

        max_results = _coerce_max_results(kwargs.get("max_results", 8))
        if max_results is None:
            return "[Tool Error] max_results must be an integer between 1 and 8."

        freshness = _coerce_enum(
            kwargs.get("freshness", "balanced"),
            PaperSearchFreshness,
        )
        if freshness is None:
            return "[Tool Error] freshness must be one of: latest, balanced, stable."

        depth = _coerce_enum(kwargs.get("depth", "deep"), PaperSearchDepth)
        if depth is None:
            return "[Tool Error] depth must be one of: fast, deep."

        query_variants = kwargs.get("query_variants", [])
        if not isinstance(query_variants, list) or not all(
            isinstance(item, str) for item in query_variants
        ):
            return "[Tool Error] query_variants must be an array of strings."

        try:
            response = await self._service.search(
                PaperSearchRequest(
                    query=query.strip(),
                    max_results=max_results,
                    freshness=freshness,
                    depth=depth,
                    query_variants=query_variants,
                )
            )
        except Exception as e:
            log_error("paper_search", e, query=query)
            return f"[Tool Error] paper_search failed: {e}"

        log_event(
            "paper_search completed",
            query=query,
            result_count=len(response.results),
            searched_sources=response.searched_sources,
            failed_sources=response.failed_sources,
        )
        return truncate_result(
            format_paper_search_response(response),
            max_chars=PAPER_SEARCH_TOOL_RESULT_MAX_CHARS,
        )

    async def close(self) -> None:
        await self._service.close()


def _coerce_max_results(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 1 or parsed > 8:
        return None
    return parsed


def _coerce_enum(value: Any, enum_type: type) -> Any:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        return None
    try:
        return enum_type(value)
    except ValueError:
        return None
