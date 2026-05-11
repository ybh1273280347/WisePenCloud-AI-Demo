import asyncio
from typing import Any, Dict, List, Optional, Set, Tuple

from chat.application.tool_content_store import (
    cache_and_format,
    cache_and_window,
    format_windowed_content,
)
from chat.application.web_fetch import FetchCoordinator
from chat.application.web_search import (
    ImageResult,
    SearchCoordinator,
    SearchResponse,
    SearchResult,
)
from chat.application.web_search.utils import (
    add_note,
    count_unique_domains,
    extract_domain,
    has_site_operator,
    normalize_queries,
)
from chat.application.web_search.utils.urls import normalize_url_for_dedup
from chat.core.config.app_settings import settings
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail, log_event


_SEARCH_QUERY_LIMIT = 4

_RESULTS_PER_QUERY = 8
_FINAL_RESULT_LIMIT = 20

_DEDUPE_DOMAINS = True
_MAX_PER_DOMAIN = 2

_PAGE_CONTENT_LIMIT = 8
_PAGE_CONTENT_CONCURRENCY = 5
_PAGE_CONTENT_TIMEOUT_SECONDS = 15.0
_PAGE_CONTENT_WINDOW_CHARS = 3000

_TOOL_DESCRIPTION = (
    "Searches the web with concurrent multi-query search. "
    "The tool always accepts queries as a list. Use one query for simple lookup and "
    "2-4 focused queries for complex research, comparisons, debugging, or source-backed answers.\n\n"
    "For one research task, prefer one web_search call with 2-4 queries instead of "
    "multiple web_search calls.\n\n"
    "Modes:\n"
    "1) normal: concurrent web search, returning titles, URLs, snippets, and optional images.\n"
    "2) deep: concurrent web search plus page content snippets from top-ranked result URLs.\n\n"
    "Use normal mode when search result snippets are enough. "
    "Use deep mode when the answer needs stronger evidence from page content.\n\n"
    "Query generation guidance:\n"
    "- Keep each query short and search-engine-style.\n"
    "- For technical questions, include one official-documentation query when possible, such as site:docs.python.org.\n"
    "- For debugging, include exact error keywords and library/framework names.\n"
    "- For comparisons, use focused A vs B queries plus official documentation queries.\n\n"
    "Cost policy: Tavily paid fallback is disabled for this tool."
    " Long evidence packs are returned as ToolContent windows; when truncated=true, "
    "continue reading with tool_content_read using next_offset."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 4,
            "description": (
                "One to four focused search-engine-style queries. "
                "Use one query for simple lookup. Use 2-4 queries for complex questions, "
                "technical research, comparisons, debugging, or source-backed answers."
            ),
        },
        "mode": {
            "type": "string",
            "enum": ["normal", "deep"],
            "description": (
                "Search mode. normal returns search result titles, URLs, snippets, "
                "and optional images. deep also reads page content snippets from up to "
                "8 top-ranked result URLs."
            ),
            "default": "normal",
        },
        "with_images": {
            "type": "boolean",
            "description": (
                "Whether to include relevant image results. "
                "Use for pictures, photos, locations, people, animals, products, "
                "UI screenshots, or visual references."
            ),
            "default": False,
        },
    },
    "required": ["queries"],
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

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        notes: List[str] = []

        queries = normalize_queries(
            kwargs.get("queries", []),
            limit=_SEARCH_QUERY_LIMIT,
            notes=notes,
        )
        if not queries:
            return "[Tool Error] Missing required queries parameter."

        mode = kwargs.get("mode", "normal")
        with_images = kwargs.get("with_images", False)

        search_override: Optional[Dict[str, Any]] = context.get("search_override")
        if search_override:
            # 会话级搜索覆盖：只提升搜索力度，不降级 AI 自主判断的结果
            # force_deep_search=True 时强制 deep；False/None 时不降级
            # force_image_search=True 时强制启用图片搜索；False/None 时不关闭
            if search_override.get("force_deep_search") is True:
                mode = "deep"
                add_note(notes, "Deep search forced by session override.")
            if search_override.get("force_image_search") is True:
                with_images = True
                add_note(notes, "Image search forced by session override.")

        dedupe_domains = _DEDUPE_DOMAINS
        if has_site_operator(queries):
            dedupe_domains = False
            add_note(notes, "Domain dedupe disabled because a site: operator was detected.")

        try:
            response = await self._coordinator.search_many(
                queries=queries,
                max_results_per_query=_RESULTS_PER_QUERY,
                final_max_results=_FINAL_RESULT_LIMIT,
                with_images=with_images,
                dedupe_domains=dedupe_domains,
                max_per_domain=_MAX_PER_DOMAIN,
                notes=notes,
            )
        except Exception as e:
            log_fail("网页搜索工具执行失败", e, session_id=session_id, queries=queries, mode=mode, with_images=with_images)
            return "[Tool Error] Unexpected error while searching the web."

        if response is None:
            return "[Tool Result] Failed to search the web."

        page_contents: List[str] = []

        if mode == "deep":
            page_contents = await self._read_page_contents(
                response,
                session_id=session_id,
                limit=_PAGE_CONTENT_LIMIT,
                notes=notes,
            )

            if not page_contents:
                add_note(notes, "Deep search was requested but no page content was read.")

        formatted = _format_response(
            response,
            mode=mode,
            queries=queries,
            notes=notes,
            page_contents=page_contents,
        )
        return _window_long_response(
            session_id=session_id,
            mode=mode,
            queries=queries,
            text=formatted,
        )

    async def _read_page_contents(
        self,
        response: SearchResponse,
        *,
        session_id: str,
        limit: int,
        notes: Optional[List[str]] = None,
    ) -> List[str]:
        candidates: List[Tuple[int, SearchResult]] = []
        seen: Set[str] = set()

        for result_index, result in enumerate(response.results, 1):
            url = result.url.strip()
            if not url:
                continue

            normalized_url = normalize_url_for_dedup(url)
            if not normalized_url or normalized_url in seen:
                continue

            seen.add(normalized_url)
            candidates.append((result_index, result))

            if len(candidates) >= limit:
                break

        if not candidates:
            add_note(notes, "No readable result URLs were available for page content reading.")
            return []

        semaphore = asyncio.Semaphore(_PAGE_CONTENT_CONCURRENCY)

        async def read_one(
            result_index: int,
            result: SearchResult,
        ) -> Optional[Tuple[int, str]]:
            async with semaphore:
                return await self._read_one_page_content(
                    result_index=result_index,
                    result=result,
                    response=response,
                    session_id=session_id,
                    notes=notes,
                )

        tasks = [read_one(result_index, result) for result_index, result in candidates]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        contents: List[Tuple[int, str]] = []

        for item in raw_results:
            if isinstance(item, Exception):
                log_fail("页面内容读取任务失败", item)
                continue

            if item is None:
                continue

            contents.append(item)

        contents.sort(key=lambda item: item[0])

        return [content for _, content in contents]

    async def _read_one_page_content(
        self,
        *,
        result_index: int,
        result: SearchResult,
        response: SearchResponse,
        session_id: str,
        notes: Optional[List[str]],
    ) -> Optional[Tuple[int, str]]:
        url = result.url.strip()

        try:
            content = await asyncio.wait_for(
                self._fetcher.fetch(url),
                timeout=_PAGE_CONTENT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as e:
            log_fail("页面内容读取超时", e, url=url, timeout=_PAGE_CONTENT_TIMEOUT_SECONDS)
            add_note(
                notes,
                f"Page content for result #{result_index} was skipped because it timed out.",
            )
            return None
        except Exception as e:
            log_fail("页面内容读取失败", e, url=url)
            add_note(
                notes,
                f"Page content for result #{result_index} failed and was skipped.",
            )
            return None

        if content is None:
            add_note(
                notes,
                f"Page content for result #{result_index} returned no content and was skipped.",
            )
            return None

        if not isinstance(content, str):
            log_event("页面内容读取跳过：返回内容非 Markdown 文本", url=url, content_type=type(content).__name__)
            add_note(
                notes,
                f"Page content for result #{result_index} was skipped because it returned non-text content.",
            )
            return None

        content = content.strip()
        if not content:
            add_note(
                notes,
                f"Page content for result #{result_index} was empty and skipped.",
            )
            return None

        try:
            window = cache_and_window(
                session_id=session_id,
                tool_name=self.name,
                source=url,
                text=content,
                content_type="text/markdown",
                metadata={
                    "query": response.query,
                    "fetched_from": "web_search.deep",
                },
                offset=0,
                limit=_PAGE_CONTENT_WINDOW_CHARS,
            )

            formatted_content = format_windowed_content(window)

        except Exception as e:
            log_fail("页面内容窗口化处理失败", e, url=url, session_id=session_id)
            add_note(
                notes,
                f"Page content for result #{result_index} failed during content processing and was skipped.",
            )
            return None

        text = (
            f"--- Page content for result #{result_index} ---\n"
            f"Title: {result.title.strip() or '(no title)'}\n"
            f"URL: {url}\n"
            "Content:\n"
            f"{formatted_content}"
        )

        return result_index, text


def _format_response(
    response: SearchResponse,
    *,
    mode: str,
    queries: List[str],
    notes: Optional[List[str]] = None,
    page_contents: Optional[List[str]] = None,
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

    if page_contents:
        lines.append("\nPage contents:")
        for content in page_contents:
            lines.append("")
            lines.append(content)

    if response.results:
        lines.append("\nResults:")

    for index, result in enumerate(response.results, 1):
        title = result.title.strip() or result.url or "(no title)"
        url = result.url.strip()
        snippet = result.snippet.strip()
        domain = extract_domain(url)

        lines.append(f"\n{index}. Title: {title}")
        if domain:
            lines.append(f"   Domain: {domain}")
        if url:
            lines.append(f"   URL: {url}")
        if snippet:
            lines.append(f"   Snippet: {snippet}")
        if result.images:
            lines.append("   Images:")
            for image in result.images[:2]:
                lines.append(_format_image_line(image, indent="      "))

    if response.images:
        lines.append("\nQuery-level images:")
        for image in response.images[:5]:
            lines.append(_format_image_line(image, indent="   "))

    return "\n".join(lines).strip()


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


def _window_long_response(
    *,
    session_id: str,
    mode: str,
    queries: List[str],
    text: str,
) -> str:
    result = text.strip()

    if len(result) <= settings.TOOL_RESULT_MAX_CHARS:
        return result

    return cache_and_format(
        session_id=session_id,
        tool_name="web_search",
        source="; ".join(queries),
        text=result,
        content_type="text/plain",
        metadata={
            "content_kind": "web_search_evidence_pack",
            "mode": mode,
            "queries": queries,
        },
        limit=settings.TOOL_RESULT_MAX_CHARS,
    )


def _normalize_tool_result(result: str) -> str:
    """向后兼容的别名：长响应应由 execute() 进行窗口化处理。"""
    result = result.strip()
    return result
