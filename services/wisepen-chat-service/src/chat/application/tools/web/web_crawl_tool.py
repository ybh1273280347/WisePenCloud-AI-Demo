from __future__ import annotations

from typing import Any, Dict, Optional

from chat.application.web_crawl import CrawlRequest, WebCrawlService
from chat.application.web_crawl.errors import CrawlConfigurationError, CrawlInputError
from chat.application.web_crawl.formatting import format_crawl_result
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_error, log_event

_TOOL_DESCRIPTION = (
    "Crawls a small, bounded set of linked web pages starting from seed URLs. "
    "Use this after the user asks for information that is likely spread across a site or a few strongly related linked pages.\n\n"
    "web_crawl is not a new fetcher. It reuses web_fetch internally, applies URL security checks, robots policy, "
    "politeness, Markdown link extraction, and relevance-based frontier ranking.\n\n"
    "Use seed_urls for http:// or https:// URLs only. Do not pass content_id, file_ref, attachment_ref, or download_ref. "
    "Set objective to the specific evidence goal, not a generic phrase. Keep max_depth small; max_depth is capped at 2 "
    "and max_pages is capped at 20.\n\n"
    "HTML pages return ToolContent windows. Direct document links return file_ref handoffs for document_parse. "
    "Skipped and failed URLs are reported explicitly; do not infer content from skipped URLs."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "seed_urls": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 5,
            "uniqueItems": True,
            "description": "Initial http:// or https:// URLs to crawl from.",
        },
        "objective": {
            "type": "string",
            "minLength": 8,
            "description": "Specific evidence objective used to rank discovered links.",
        },
        "max_depth": {
            "type": "integer",
            "minimum": 1,
            "maximum": 2,
            "default": 1,
        },
        "max_pages": {
            "type": "integer",
            "minimum": 2,
            "maximum": 20,
            "default": 8,
        },
    },
    "required": ["seed_urls", "objective"],
    "additionalProperties": False,
}


class WebCrawlTool(BaseTool):
    def __init__(self, service: WebCrawlService):
        self._service = service

    @property
    def name(self) -> str:
        return "web_crawl"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        session_id = context.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            return "[Tool Error] session_id is required for web_crawl."

        seed_urls = kwargs.get("seed_urls")
        if not isinstance(seed_urls, list) or not seed_urls:
            return "[Tool Error] seed_urls must be a non-empty array."

        if not all(isinstance(item, str) and item.strip() for item in seed_urls):
            return "[Tool Error] seed_urls must contain non-empty URL strings."

        objective = kwargs.get("objective")
        if not isinstance(objective, str) or len(objective.strip()) < 8:
            return "[Tool Error] objective must be a specific non-empty string."

        max_depth = _coerce_int(kwargs.get("max_depth", 1))
        if max_depth is None or max_depth < 1 or max_depth > 2:
            return "[Tool Error] max_depth must be an integer between 1 and 2."

        max_pages = _coerce_int(kwargs.get("max_pages", 8))
        if max_pages is None or max_pages < 2 or max_pages > 20:
            return "[Tool Error] max_pages must be an integer between 2 and 20."

        try:
            log_event(
                "web_crawl fetched",
                seed_count=len(seed_urls),
                max_depth=max_depth,
                max_pages=max_pages,
            )
            result = await self._service.crawl(
                CrawlRequest(
                    session_id=session_id.strip(),
                    seed_urls=[item.strip() for item in seed_urls],
                    objective=objective.strip(),
                    max_depth=max_depth,
                    max_pages=max_pages,
                )
            )
            return format_crawl_result(result)
        except (CrawlInputError, CrawlConfigurationError) as e:
            return f"[Tool Error] {e}"
        except Exception as e:
            log_error("web_crawl", e)
            return "[Tool Error] web_crawl failed unexpectedly."


def _coerce_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
