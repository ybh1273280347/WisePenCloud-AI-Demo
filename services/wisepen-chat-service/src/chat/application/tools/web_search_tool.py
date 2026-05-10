import asyncio
from typing import Any, Dict, List, Optional, Set

from chat.application.tool_content_store import (
    cache_and_window,
    format_windowed_content,
)
from chat.application.web_fetch import FetchCoordinator
from chat.application.web_search import (
    MAX_BROAD_SEARCH_QUERIES,
    ImageResult,
    SearchCoordinator,
    SearchResponse,
    has_response_content,
)
from chat.application.web_search.search_coordinator import _normalize_queries
from chat.application.web_search.utils import (
    add_note,
    count_unique_domains,
    extract_domain,
    has_site_operator,
    normalize_bool,
    normalize_int,
)
from chat.core.config.app_settings import settings
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail


_TRUNCATION_MARKER = "\n\n...(Search result truncated due to length)"

_TOOL_DESCRIPTION = (
    "Searches the web using a staged fallback chain: "
    "fresh cache, SearXNG, DuckDuckGo buffer, stale cache, then Tavily as paid fallback.\n\n"
    "Use query for a focused precise search. Use queries for 2-4 focused searches when broad "
    "coverage is needed. Use fetch_top_pages=true when snippets are insufficient and the caller "
    "needs page content from the top merged results.\n\n"
    "Use freshness_required=true for time-sensitive information such as latest news, current "
    "facts, recent releases, prices, weather, scores, schedules, or current office holders.\n\n"
    "Use with_images=true for pictures, photos, visual references, locations, people, animals, "
    "products, UI screenshots, or other visual information."
)
_TOOL_DESCRIPTION += (
    "\n\nQuery generation guidance:\n"
    "- For simple lookup, use query.\n"
    "- For complex questions, comparisons, debugging, or research tasks, use queries.\n"
    "- Generate 2-4 concise focused search-engine-style queries.\n"
    "- Keep each query short. Do not pass long natural-language paragraphs.\n\n"
    "Recommended query patterns for technical questions:\n"
    "1) original focused query\n"
    "2) official documentation query, often with site:docs...\n"
    "3) exact error message keywords\n"
    "4) GitHub issue or StackOverflow style query when debugging\n\n"
    "Recommended query patterns for comparison or selection:\n"
    "1) A vs B focused comparison\n"
    "2) A official documentation\n"
    "3) B official documentation\n"
    "4) real-world usage A OR B with current year if freshness matters\n"
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Single web search query.",
        },
        "queries": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Multiple focused search queries for broad or complex research. "
                "Prefer 2-4 concise queries. If provided, this takes precedence over query."
            ),
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum results per query. Default 5. Maximum 10.",
            "default": 5,
            "minimum": 1,
            "maximum": 10,
        },
        "final_max_results": {
            "type": "integer",
            "description": (
                "Maximum merged results returned after multi-query search. "
                "Default 12. Maximum 20."
            ),
            "default": 12,
            "minimum": 1,
            "maximum": 20,
        },
        "with_images": {
            "type": "boolean",
            "description": (
                "Whether to include relevant image results. Use when the user asks for "
                "pictures, photos, visual references, locations, people, animals, products, "
                "UI screenshots, or other visual information."
            ),
            "default": False,
        },
        "freshness_required": {
            "type": "boolean",
            "description": (
                "Whether the search must avoid stale cached results. Set to true for "
                "time-sensitive queries such as latest news, current facts, prices, weather, "
                "scores, schedules, recent releases, or current office holders."
            ),
            "default": False,
        },
        "fetch_top_pages": {
            "type": "boolean",
            "description": (
                "Whether to fetch and extract content from the top 1-3 merged result pages "
                "after search. Use when snippets are insufficient and the user needs a "
                "detailed source-backed answer."
            ),
            "default": False,
        },
        "fetch_top_pages_limit": {
            "type": "integer",
            "description": "Number of top merged result pages to fetch. Default 2. Maximum 3.",
            "default": 2,
            "minimum": 1,
            "maximum": 3,
        },
        "fetched_page_max_chars": {
            "type": "integer",
            "description": (
                "Maximum characters returned from each fetched page. "
                "Default 3000. Maximum 6000."
            ),
            "default": 3000,
            "minimum": 500,
            "maximum": 6000,
        },
        "fetch_page_timeout_seconds": {
            "type": "number",
            "description": "Timeout for each fetched top page. Default 15 seconds. Maximum 30 seconds.",
            "default": 15.0,
            "minimum": 3.0,
            "maximum": 30.0,
        },
        "allow_paid_fallback": {
            "type": "boolean",
            "description": (
                "Whether multi-query search may use Tavily paid fallback when merged results "
                "are sparse. Default false to control cost. Single-query search keeps its "
                "standard fallback behavior."
            ),
            "default": False,
        },
        "dedupe_domains": {
            "type": "boolean",
            "description": (
                "Whether to limit repeated results from the same domain. Default true for "
                "broader coverage. Set false for site-specific searches such as "
                "site:docs.python.org."
            ),
            "default": True,
        },
        "max_per_domain": {
            "type": "integer",
            "description": (
                "Maximum results kept per domain when dedupe_domains is true. "
                "Default 2. Maximum 5."
            ),
            "default": 2,
            "minimum": 1,
            "maximum": 5,
        },
        "include_domains": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Only keep results from these domains after search. "
                "Use for official-site or site-specific research."
            ),
        },
        "exclude_domains": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Exclude results from these domains after search.",
        },
        "time_range": {
            "type": "string",
            "enum": ["day", "week", "month", "year"],
            "description": (
                "Optional freshness window. Best-effort parameter. When month/year is used, "
                "queries without time words may get the current year appended."
            ),
        },
    },
    "required": [],
}


class WebSearchTool(BaseTool):
    def __init__(self, coordinator: SearchCoordinator, fetcher: FetchCoordinator):
        self._coordinator = coordinator
        self._fetcher = fetcher

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        notes: List[str] = []
        queries = _normalize_queries(
            _get_queries(kwargs),
            limit=MAX_BROAD_SEARCH_QUERIES,
            notes=notes,
            time_range=kwargs.get("time_range"),
        )
        if not queries:
            return "[Tool Error] Missing required query or queries parameter."

        max_results = normalize_int(
            kwargs.get("max_results", 5),
            default=5,
            minimum=1,
            maximum=10,
        )
        final_max_results = normalize_int(
            kwargs.get("final_max_results", 12),
            default=12,
            minimum=1,
            maximum=20,
        )
        fetch_top_pages_limit = normalize_int(
            kwargs.get("fetch_top_pages_limit", 2),
            default=2,
            minimum=1,
            maximum=3,
        )
        fetched_page_max_chars = normalize_int(
            kwargs.get("fetched_page_max_chars", 3000),
            default=3000,
            minimum=500,
            maximum=6000,
        )
        fetch_page_timeout_seconds = _normalize_float(
            kwargs.get("fetch_page_timeout_seconds", 15.0),
            default=15.0,
            minimum=3.0,
            maximum=30.0,
        )
        max_per_domain = normalize_int(
            kwargs.get("max_per_domain", 2),
            default=2,
            minimum=1,
            maximum=5,
        )

        with_images = normalize_bool(kwargs.get("with_images", False))
        freshness_required = normalize_bool(kwargs.get("freshness_required", False))
        fetch_top_pages = normalize_bool(kwargs.get("fetch_top_pages", False))
        allow_paid_fallback = normalize_bool(kwargs.get("allow_paid_fallback", False))
        dedupe_domains = (
            normalize_bool(kwargs.get("dedupe_domains"))
            if "dedupe_domains" in kwargs
            else None
        )
        if len(queries) > 1 and dedupe_domains is None and has_site_operator(queries):
            dedupe_domains = False
            add_note(notes, "Domain dedupe disabled because a site: operator was detected.")
        include_domains = _get_domain_list(kwargs.get("include_domains"))
        exclude_domains = _get_domain_list(kwargs.get("exclude_domains"))

        mode = "deep" if fetch_top_pages else "broad" if len(queries) > 1 else "precise"

        try:
            if len(queries) == 1:
                response = await self._coordinator.search(
                    query=queries[0],
                    max_results=max_results,
                    with_images=with_images,
                    freshness_required=freshness_required,
                    allow_paid_fallback=True,
                )
            else:
                response = await self._coordinator.search_many(
                    queries=queries,
                    max_results_per_query=max_results,
                    final_max_results=final_max_results,
                    with_images=with_images,
                    freshness_required=freshness_required,
                    allow_paid_fallback=allow_paid_fallback,
                    dedupe_domains=dedupe_domains,
                    max_per_domain=max_per_domain,
                    notes=notes,
                    include_domains=include_domains,
                    exclude_domains=exclude_domains,
                    time_range=kwargs.get("time_range"),
                )
        except Exception as e:
            log_fail(
                "联网搜索工具",
                e,
                session_id=session_id,
                queries=queries,
                mode=mode,
                max_results=max_results,
                with_images=with_images,
                freshness_required=freshness_required,
            )
            return "[Tool Error] Unexpected error while searching the web."

        if response is None:
            return "[Tool Result] Failed to search the web (all search methods exhausted)."

        if not has_response_content(response):
            return _format_response(response, mode=mode, queries=queries, notes=notes)

        extra_contents: List[str] = []

        if fetch_top_pages:
            extra_contents = await self._fetch_top_pages(
                response,
                session_id=session_id,
                limit=fetch_top_pages_limit,
                max_chars_per_page=fetched_page_max_chars,
                timeout_seconds=fetch_page_timeout_seconds,
                notes=notes,
            )

        return _format_response(
            response,
            mode=mode,
            queries=queries,
            notes=notes,
            extra_contents=extra_contents,
        )

    async def _fetch_top_pages(
        self,
        response: SearchResponse,
        *,
        session_id: str,
        limit: int = 2,
        max_chars_per_page: int = 3000,
        timeout_seconds: float = 15.0,
        notes: Optional[List[str]] = None,
    ) -> List[str]:
        limit = normalize_int(limit, default=2, minimum=1, maximum=3)
        max_chars_per_page = normalize_int(
            max_chars_per_page,
            default=3000,
            minimum=500,
            maximum=6000,
        )
        timeout_seconds = _normalize_timeout(
            timeout_seconds,
            default=15.0,
            maximum=30.0,
        )

        contents: List[str] = []
        seen: Set[str] = set()

        for result_index, result in enumerate(response.results, 1):
            url = result.url.strip()
            if not url or url in seen:
                continue
            seen.add(url)

            if len(contents) >= limit:
                break

            try:
                content = await asyncio.wait_for(
                    self._fetcher.fetch(url),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError as e:
                log_fail(
                    "搜索结果页面抓取超时",
                    e,
                    url=url,
                    timeout=timeout_seconds,
                )
                add_note(
                    notes,
                    f"Fetched page for result #{result_index} was skipped because it timed out.",
                )
                continue
            except Exception as e:
                log_fail("搜索结果页面抓取失败", e, url=url)
                add_note(
                    notes,
                    f"Fetched page for result #{result_index} failed and was skipped.",
                )
                continue

            if content is None:
                add_note(
                    notes,
                    f"Fetched page for result #{result_index} returned no content and was skipped.",
                )
                continue

            if not isinstance(content, str):
                log_fail(
                    "搜索结果页面抓取跳过",
                    "Fetched content is not text Markdown, skip page body",
                    url=url,
                    content_type=type(content).__name__,
                )
                add_note(
                    notes,
                    f"Fetched page for result #{result_index} was skipped because it returned non-text content.",
                )
                continue

            content = content.strip()
            if not content:
                add_note(
                    notes,
                    f"Fetched page for result #{result_index} was empty and skipped.",
                )
                continue

            window = cache_and_window(
                session_id=session_id,
                tool_name=self.name,
                source=url,
                text=content,
                content_type="text/markdown",
                metadata={
                    "query": response.query,
                    "fetched_from": "web_search.fetch_top_pages",
                },
                offset=0,
                limit=max_chars_per_page,
            )

            contents.append(
                f"--- Fetched page for result #{result_index} ---\n"
                f"Title: {result.title.strip() or '(no title)'}\n"
                f"URL: {url}\n"
                "Content:\n"
                f"{format_windowed_content(window)}"
            )

        return contents


def _get_queries(kwargs: Dict[str, Any]) -> List[str]:
    raw_queries = kwargs.get("queries")
    if isinstance(raw_queries, list):
        queries = [
            item
            for item in raw_queries
            if isinstance(item, str) and item.strip()
        ]
        if queries:
            return queries

    query = kwargs.get("query")
    if isinstance(query, str) and query.strip():
        return [query]

    return []


def _get_domain_list(value: Any) -> Optional[List[str]]:
    if not isinstance(value, list):
        return None

    domains = [
        item.strip()
        for item in value
        if isinstance(item, str) and item.strip()
    ]
    return domains or None


def _format_response(
    response: SearchResponse,
    *,
    mode: str,
    queries: List[str],
    notes: Optional[List[str]] = None,
    extra_contents: Optional[List[str]] = None,
) -> str:
    unique_domains = count_unique_domains(tuple(response.results))
    output_notes = list(notes or [])
    if response.source and "stale_cache" in response.source:
        add_note(
            output_notes,
            "Some results came from stale cache and may be outdated.",
        )
    output_notes = _deduplicate_notes(output_notes)

    lines = ["[Tool Result] Web search evidence pack"]
    lines.append(f"Mode: {mode}")

    if queries:
        lines.append("Queries:")
        for query in queries:
            lines.append(f"- {query}")

    if response.source:
        lines.append(f"Source: {response.source}")

    lines.append(
        f"Summary: {len(response.results)} results, "
        f"{len(response.images)} query-level images, "
        f"{unique_domains} unique domains."
    )

    if output_notes:
        lines.append("Notes:")
        for note in output_notes:
            lines.append(f"- {note}")

    if response.answer:
        lines.append(f"\nAnswer:\n{response.answer}")

    if response.results:
        lines.append("\nResults:")

    for index, result in enumerate(response.results, 1):
        title = result.title.strip() or result.url or "(no title)"
        url = result.url.strip()
        snippet = result.snippet.strip()
        domain = extract_domain(url)

        lines.append(f"\n{index}. Title: {title}")
        lines.append(f"   Domain: {domain}")
        lines.append(f"   URL: {url}")
        lines.append(f"   Snippet: {snippet}")
        if result.images:
            lines.append("   Images:")
            for image in result.images[:2]:
                lines.append(_format_image_line(image, indent="      "))

    if response.images:
        lines.append("\nQuery-level images:")
        for image in response.images[:5]:
            lines.append(_format_image_line(image, indent="   "))

    if extra_contents:
        lines.append("\nFetched top pages:")
        for content in extra_contents:
            lines.append("")
            lines.append(content)

    return _normalize_tool_result("\n".join(lines))


def _format_image_line(image: ImageResult, *, indent: str) -> str:
    url = image.url.strip()
    desc = image.desc.strip() if image.desc else ""
    line = f"{indent}- {url}"

    details: List[str] = []
    if desc:
        details.append(desc)
    if image.resolution:
        details.append(f"resolution={image.resolution}")
    if image.source_url:
        details.append(f"source={image.source_url}")

    if details:
        line += f" ({'; '.join(details)})"

    return line


def _deduplicate_notes(notes: List[str]) -> List[str]:
    seen: Set[str] = set()
    deduped: List[str] = []

    for note in notes:
        normalized = " ".join(note.strip().split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)

    return deduped


def _normalize_timeout(
    value: Any,
    *,
    default: float,
    maximum: float,
) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        timeout = default

    if timeout <= 0:
        timeout = default

    return min(timeout, maximum)


def _normalize_float(
    value: Any,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default

    return max(minimum, min(number, maximum))


def _normalize_tool_result(result: str) -> str:
    result = result.strip()

    if len(result) > settings.TOOL_RESULT_MAX_CHARS:
        limit = settings.TOOL_RESULT_MAX_CHARS
        keep_len = max(0, limit - len(_TRUNCATION_MARKER))
        result = result[:keep_len].rstrip() + _TRUNCATION_MARKER

    return result
