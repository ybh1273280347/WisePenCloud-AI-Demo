from __future__ import annotations

from typing import Any, Dict, Optional

from chat.application.tools.services.paper_search import PaperSearchRequest, PaperSearchService
from chat.application.tools.services.paper_search.config import PAPER_SEARCH_TOOL_RESULT_MAX_CHARS
from chat.application.tools.services.paper_search.formatting import (
    format_paper_search_response,
    truncate_result,
)
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_error, log_event

_TOOL_DESCRIPTION = (
    "Searches free and open scholarly sources for academic papers using Crossref, arXiv, DataCite, "
    "and optionally Unpaywall. Use this tool for paper discovery, DOI-backed publication metadata, "
    "preprints, datasets, research objects, and open-access paper links.\n\n"
    "This tool intentionally avoids paid commercial databases and freemium quota-based APIs. "
    "It does not use OpenAlex API or Semantic Scholar API by default. "
    "Do not rely on arXiv alone. arXiv is a preprint source and has strict request limits. "
    "For non-English user queries, use concise English academic keywords when calling this tool. "
    "The tool may return partial results with source warnings if one source is rate-limited or unavailable."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Concise English academic search keywords. "
                "For non-English user requests, translate the concept into English before calling. "
                'Example: use "deep learning" instead of "深度学习".'
            ),
        },
        "max_results": {
            "type": "integer",
            "default": 8,
            "minimum": 1,
            "maximum": 10,
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
            return "[Tool Error] max_results must be an integer between 1 and 10."

        try:
            response = await self._service.search(
                PaperSearchRequest(query=query.strip(), max_results=max_results)
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
    if parsed < 1 or parsed > 10:
        return None
    return parsed
