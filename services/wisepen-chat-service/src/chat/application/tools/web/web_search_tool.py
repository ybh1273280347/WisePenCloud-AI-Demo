import inspect
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from chat.application.algorithms.url import canonicalize_url
from chat.application.tools.common.tool_content_store import (
    cache_and_format,
)
from chat.application.tools.config import TOOL_RESULT_MAX_CHARS
from chat.application.runtime_context import get_runtime_context
from chat.application.web_search import (
    CustomSearchProviderUnavailableError,
    EmptySearchResultError,
    ImageResult,
    SearchCoordinator,
    SearchManyRequest,
    SearchResponse,
    SearchResult,
    WikipediaGroundingResult,
)
from chat.application.web_search.provider_policy import (
    parse_custom_provider_credentials,
)
from chat.application.web_search.search_provider_config.constants import MODE_CUSTOM
from chat.application.web_search.utils.domains import (
    count_unique_domains,
    extract_domain,
)
from chat.application.web_search.utils.notes import add_note
from chat.application.web_search.utils.queries import normalize_queries
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_event, log_fail, log_ok

_SEARCH_QUERY_MIN_COUNT = 2
_SEARCH_QUERY_LIMIT = 4

_FAST_CANDIDATE_PAGE_LIMIT = 0
_NORMAL_CANDIDATE_PAGE_LIMIT = 5
_DEEP_CANDIDATE_PAGE_LIMIT = 12

_FAST_SOURCE_DISPLAY_LIMIT = 10
_NORMAL_SOURCE_DISPLAY_LIMIT = 15
_DEEP_SOURCE_DISPLAY_LIMIT = 10

_TOOL_DESCRIPTION = (
    "Searches the web with concurrent multi-query recall and returns candidate evidence: "
    "titles, URLs, snippets, optional images, background grounding, and candidate pages. "
    "It does not fetch or read page bodies.\n\n"
    "Query rules: issue at most one web_search call per user request. Put all search variants "
    "into that single call's queries array. Always pass 2-4 short search-engine-style queries. "
    "Every call MUST include at least one pure English query. A pure English query is written "
    "with ASCII English words, numbers, and punctuation only, and preserves key technical "
    "terms in English. Do not call web_search "
    "with only one query and do not pass more than four queries. The other queries may use "
    "the user's language, English, or any language directly useful for the task; do not force "
    "a specific companion-query language unless it is relevant. queries[0] is primary; queries[1] is "
    "secondary; queries[2] is extra.\n\n"
    "Mode rules: choose the most specific mode; do not use normal as the default.\n"
    "- fast: quick facts, definitions, official sites, images/photos, lightweight overviews, "
    "or questions answerable from snippets/images. NEVER call web_fetch after fast.\n"
    "- normal: medium-complexity searches that need a small candidate URL list but do not meet "
    "fast or deep conditions.\n"
    "- deep: technical research, engineering decisions, academic/paper research, community best "
    "practices, official documentation comparison, multi-source verification, recent rules/prices/"
    "laws/news, medical/legal/financial accuracy, or broad bilingual recall.\n\n"
    "Answer language rule: after using this tool, follow the conversation language policy: "
    "use the user's explicit language request first, otherwise prefer the language of the "
    "user's current message, and use the user's preferred locale only when the message language "
    "is ambiguous. Search query language and source language do not by themselves determine "
    "the final answer language.\n\n"
    "Result-use rule: synthesize the results into an answer; do not return the raw evidence pack, "
    "candidate list, snippets, or source list as the final answer. When image results are relevant, "
    "include image URLs and source page URLs when available.\n\n"
    "web_fetch rule: web_search candidate pages have NOT been fetched. In normal/deep mode, call "
    "web_fetch when snippets are insufficient, when page-body evidence is needed, or when the task "
    "requires primary-source verification, direct quotes, technical details, conflict resolution, "
    "or high-confidence citations. If web_fetch is needed, call it once with all selected URLs: "
    "urls=[url1, url2, ...]. Do not call web_fetch once per URL.\n\n"
    "Long evidence packs may be returned as ToolContent windows with content_id. "
    "The full content is split into many chunks; the first window may not contain the key evidence. "
    "After web_search returns cached content_ids, you MUST call evidence_rank with the user's "
    "question and the content_ids to score all chunks by relevance and find the most relevant "
    "passages before answering. Do not answer directly from the first truncated window alone. "
    "If you need more context around a ranked passage, use tool_content_read with content_id "
    "and offset. Use source markers like [1], [2] when citing evidence."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 4,
            "description": (
                "Two to four focused search-engine-style queries. "
                "Every web_search call MUST include at least one pure English query. "
                "A pure English query is written with ASCII English words, numbers, and punctuation only, "
                "and preserves key technical terms in English. "
                "queries[0] is primary; queries[1] is secondary; queries[2] is extra. "
                "Use the user's language, English, or any relevant locale-specific query variants when useful; "
                "do not force a specific companion-query language unless it is relevant."
            ),
        },
        "wikipedia_keywords": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 3,
            "description": (
                "Optional short entity/concept keywords for Wikipedia grounding. "
                "Use entities, concepts, organizations, products, laws, or technical terms, not full questions. "
                "Examples: SearXNG, Redis, Reciprocal rank fusion, Digital Markets Act. "
                "fast mode ignores this field; normal uses at most 1; deep uses at most 3."
            ),
        },
        "mode": {
            "type": "string",
            "enum": ["fast", "normal", "deep"],
            "description": (
                "Search mode; set explicitly and do not use normal as the default. "
                "Use fast for quick facts, simple lookup, definitions, official sites, images/photos, "
                "or lightweight overview when snippets/images are enough and web_fetch must not follow. "
                "Use deep for technical research, engineering decisions, academic/paper research, "
                "community best practices, official documentation comparison, multi-source verification, "
                "recent rules/prices/laws/news, medical/legal/financial accuracy, or broad bilingual recall. "
                "Use normal only for medium-complexity searches that need a small candidate URL list "
                "but do not meet fast or deep conditions."
            ),
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
        "language": {
            "type": "string",
            "enum": ["en", "zh-CN"],
            "description": (
                "Deprecated legacy field. Search language is now inferred per query variant by the backend planner. "
                "Prefer omitting this field."
            ),
        },
    },
    "required": ["queries", "mode"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class _OutputBudget:
    candidate_page_limit: int
    source_display_limit: int


def _get_output_budget(mode: str) -> _OutputBudget:
    if mode == "fast":
        return _OutputBudget(
            candidate_page_limit=_FAST_CANDIDATE_PAGE_LIMIT,
            source_display_limit=_FAST_SOURCE_DISPLAY_LIMIT,
        )

    if mode == "deep":
        return _OutputBudget(
            candidate_page_limit=_DEEP_CANDIDATE_PAGE_LIMIT,
            source_display_limit=_DEEP_SOURCE_DISPLAY_LIMIT,
        )

    return _OutputBudget(
        candidate_page_limit=_NORMAL_CANDIDATE_PAGE_LIMIT,
        source_display_limit=_NORMAL_SOURCE_DISPLAY_LIMIT,
    )


def _is_pure_english_query(query: str) -> bool:
    normalized = query.strip()
    if not normalized:
        return False
    if any(not char.isascii() for char in normalized):
        return False
    return any(char.isalpha() for char in normalized)


def _has_pure_english_query(queries: List[str]) -> bool:
    return any(_is_pure_english_query(query) for query in queries)


def _search_language_hint(locale: str) -> Optional[str]:
    if locale in {"zh-CN", "zh-TW", "zh-HK"}:
        return "zh"
    if locale in {"en-US", "en-GB"}:
        return "en"
    return None


class WebSearchTool(BaseTool):
    def __init__(self, coordinator: SearchCoordinator):
        self._coordinator = coordinator

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

        raw_queries = kwargs.get("queries", [])

        if not isinstance(raw_queries, list):
            return "[Tool Error] queries must be a list of strings."

        if len(raw_queries) < _SEARCH_QUERY_MIN_COUNT:
            return (
                "[Tool Error] web_search requires 2-4 search queries. "
                "You MUST call web_search again immediately with at least two queries, "
                "including one pure English query. Do not proceed without searching."
            )

        if len(raw_queries) > _SEARCH_QUERY_LIMIT:
            return (
                "[Tool Error] web_search accepts at most 4 search queries. "
                "You MUST call web_search again immediately with 2-4 focused queries. "
                "Do not proceed without searching."
            )

        queries, _ = normalize_queries(
            raw_queries,
            limit=_SEARCH_QUERY_LIMIT,
            notes=notes,
        )
        if not queries:
            return "[Tool Error] Missing required queries parameter."

        if len(queries) < _SEARCH_QUERY_MIN_COUNT:
            return (
                "[Tool Error] web_search requires at least two distinct search queries after normalization. "
                "You MUST call web_search again immediately with different queries. "
                "Do not proceed without searching."
            )

        if not _has_pure_english_query(queries):
            return (
                "[Tool Error] web_search requires at least one pure English query. "
                "A pure English query must be written with ASCII English words, numbers, and punctuation only, "
                "and should preserve key technical terms in English. "
                "Keep any non-English query only when it is useful for the user's request, and add one pure English query. "
                "You MUST call web_search again immediately. Do not proceed without searching."
            )

        mode = kwargs.get("mode", "normal")
        output = _get_output_budget(mode)
        with_images = kwargs.get("with_images", False)
        runtime_context = get_runtime_context(context)
        language = kwargs.get("language")
        if language is None and runtime_context is not None:
            language = _search_language_hint(runtime_context.locale)
        wikipedia_keywords = kwargs.get("wikipedia_keywords")
        search_config = runtime_context.search_config if runtime_context else None
        provider_mode = search_config.mode if search_config is not None else "default"
        custom_provider_credentials = parse_custom_provider_credentials(
            search_config.custom_providers if search_config is not None else None
        )
        if (
            provider_mode == MODE_CUSTOM
            and search_config is not None
            and search_config.error_public_code
        ):
            status = search_config.error_status or "provider_error"
            last_error_code = (
                search_config.error_last_error_code or "provider_error"
            )
            await _record_custom_provider_failure(context, status, last_error_code)
            return _format_custom_provider_error(
                public_code=search_config.error_public_code,
                last_error_code=last_error_code,
                message=search_config.error_message,
            )

        search_override: Optional[Dict[str, Any]] = context.get("search_override")
        if search_override:
            if search_override.get("force_image_search") is True:
                with_images = True
                add_note(notes, "Image search forced by session override.")

        candidate_page_limit = output.candidate_page_limit

        log_event(
            "web_search 开始执行",
            session_id=session_id,
            mode=mode,
            queries=len(queries),
            wikipedia_keywords=wikipedia_keywords,
            candidate_page_limit=candidate_page_limit,
            with_images=with_images,
            language=language,
        )

        try:
            request = SearchManyRequest(
                queries=queries,
                language=language,
                mode=mode,
                with_images=with_images,
                custom_provider_params=custom_provider_credentials,
                provider_mode=provider_mode,
                user_id=context.get("user_id"),
                wikipedia_keywords=wikipedia_keywords,
            )
            many_result = await self._coordinator.search_many(request)
            response = many_result.response
            grounding = many_result.grounding
        except CustomSearchProviderUnavailableError as e:
            await _record_custom_provider_failure(
                context,
                e.status,
                e.last_error_code,
            )
            return _format_custom_provider_error(
                public_code=e.public_code,
                last_error_code=e.last_error_code,
                message=str(e),
            )
        except EmptySearchResultError:
            return "[Tool Error] All search queries returned no results. Please try different or more specific queries."
        except Exception as e:
            log_fail(
                "web_search",
                repr(e),
                session_id=session_id,
                query_count=len(queries),
                mode=mode,
                with_images=with_images,
                language=language,
            )
            return "[Tool Error] Unexpected error while searching the web."

        if response is None:
            log_fail(
                "web_search",
                "搜索返回空 response",
                session_id=session_id,
                mode=mode,
                language=language,
            )
            return "[Tool Result] Failed to search the web."

        candidate_pages = _select_candidate_pages(
            response,
            limit=candidate_page_limit,
        )
        if candidate_page_limit > 0 and not candidate_pages:
            add_note(
                notes, "No candidate URLs were available for follow-up web_fetch calls."
            )

        log_ok(
            "web_search",
            session_id=session_id,
            mode=mode,
            query_count=len(queries),
            results=len(response.results),
            images=len(response.images),
            candidate_pages=len(candidate_pages),
            notes=len(notes),
            language=language,
        )

        formatted = _format_response(
            response,
            mode=mode,
            queries=queries,
            notes=notes,
            candidate_pages=candidate_pages,
            source_display_limit=output.source_display_limit,
            grounding=grounding,
        )
        return _window_long_response(
            session_id=session_id,
            mode=mode,
            queries=queries,
            text=formatted,
            citations=_build_citations(response),
        )

    async def close(self) -> None:
        await self._coordinator.close()


def _format_response(
    response: SearchResponse,
    *,
    mode: str,
    queries: List[str],
    notes: Optional[List[str]] = None,
    candidate_pages: Optional[List[Tuple[int, SearchResult]]] = None,
    source_display_limit: int,
    grounding: Tuple[WikipediaGroundingResult, ...] = (),
) -> str:
    unique_domains = count_unique_domains(tuple(response.results))
    output_notes = list(notes or [])

    display_results = response.results[:source_display_limit]
    if len(response.results) > source_display_limit:
        add_note(
            output_notes,
            f"Sources list was shortened to {source_display_limit} items because {mode} mode prioritizes concise evidence.",
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
    lines.append(
        "Result order: reranked order after multi-query/provider fusion and deduplication."
    )
    lines.append(
        "Assistant instructions: follow the conversation language policy for the final answer. "
        "Use the user's explicit language request first, otherwise prefer the language of the "
        "current user message, and use the user's preferred locale only when the message language "
        "is ambiguous. Do not return this evidence pack, "
        "titles, URLs, snippets, or candidate list as the user-facing answer. Synthesize a concise "
        "answer with analysis from the evidence. In fast mode, answer from snippets only and do not "
        "call web_fetch. In normal/deep mode, snippets are search-engine previews, not page content. "
        "You SHOULD call web_fetch with the most relevant candidate URLs to get actual page evidence, "
        "unless the question is trivially answered by snippet text alone."
    )
    if response.images or any(result.images for result in response.results):
        lines.append(
            "Image instruction: image results are available. Include the relevant image URL(s) "
            "in the final answer. If an image has a source page URL, include that source URL too."
        )

    if output_notes:
        lines.append("Notes:")
        for note in output_notes:
            lines.append(f"- {note}")

    if grounding:
        lines.append("\nBackground Grounding:")
        lines.append(
            "The following are Wikipedia search results, use them as background information. "
            "Wikipedia content provides reference for entity disambiguation and terminology context. "
            "It is not fetched page content, not a ranked evidence source, not a candidate page "
            "for web_fetch, and must not override current web search results."
        )
        for gr in grounding:
            kw = gr.keyword.text
            lines.append(f"- {gr.title} (keyword: {kw}, lang: {gr.language})")
            if gr.extract:
                lines.append(f"  {gr.extract}")
            if gr.url:
                lines.append(f"  Source: {gr.url}")

    if candidate_pages:
        candidate_indices = {idx for idx, _ in candidate_pages}
        lines.append("\nCandidate pages for web_fetch:")
        lines.append(
            "These pages have NOT been fetched. Snippets are search-engine previews, not page content. "
            "In normal/deep mode, you SHOULD call web_fetch with the most relevant candidate URLs "
            "to get actual page evidence before answering. "
            "Only skip web_fetch when the question is trivially answered by snippet text alone. "
            f"{_candidate_fetch_requirement(mode)} "
            "If you decide to call web_fetch, select only the necessary URLs and pass them in one call: "
            "urls=[url1, url2, ...]. Do not call web_fetch once per URL. "
            "See Sources below for full details; candidate pages are marked with [C]."
        )
        for result_index, _ in candidate_pages:
            lines.append(f"  [C] [{result_index}]")
    else:
        candidate_indices = set()

    if display_results:
        lines.append("\nSources (reranked order for citations):")
        lines.append(
            "Evidence only. Source markers [1], [2], ... and citation metadata use this reranked order, "
            "not the original search-provider order. Do not copy this source list to the user; "
            "use it to synthesize the final answer in the appropriate response language."
        )

    for index, result in enumerate(display_results, 1):
        title = result.title.strip() or result.url or "(no title)"
        url = result.url.strip()
        snippet = result.snippet.strip()
        domain = extract_domain(url)
        candidate_marker = " [C]" if index in candidate_indices else ""

        lines.append(f"\n[{index}]{candidate_marker} Title: {title}")
        if domain:
            lines.append(f"   Domain: {domain}")
        if url:
            lines.append(f"   URL: {url}")
        if snippet:
            lines.append(f"   Snippet: {snippet}")
        if result.images:
            image_lines = [
                _format_image_line(image, indent="      ")
                for image in result.images[:2]
                if image.url.strip()
            ]
            if image_lines:
                lines.append("   Images:")
                lines.extend(image_lines)

    query_image_lines = [
        _format_image_line(image, indent="   ")
        for image in response.images[:5]
        if image.url.strip()
    ]
    if query_image_lines:
        lines.append("\nQuery-level images:")
        lines.extend(query_image_lines)

    return "\n".join(lines).strip()


def _select_candidate_pages(
    response: SearchResponse,
    *,
    limit: int,
) -> List[Tuple[int, SearchResult]]:
    if limit <= 0:
        return []

    candidates: List[Tuple[int, SearchResult]] = []
    seen: Set[str] = set()

    for result_index, result in enumerate(response.results, 1):
        url = result.url.strip()
        if not url:
            continue

        normalized_url = canonicalize_url(url)
        if not normalized_url or normalized_url in seen:
            continue

        seen.add(normalized_url)
        candidates.append((result_index, result))

        if len(candidates) >= limit:
            break

    return candidates


def _candidate_fetch_requirement(mode: str) -> str:
    if mode == "deep":
        return "Up to 12 candidate URLs if web_fetch is necessary."
    if mode == "normal":
        return "Up to 5 candidate URLs if web_fetch is necessary."
    return ""


def _build_citations(response: SearchResponse) -> List[Dict[str, str]]:
    citations: List[Dict[str, str]] = []

    for index, result in enumerate(response.results, 1):
        url = result.url.strip()
        if not url:
            continue

        title = result.title.strip() or url or "(no title)"
        citations.append(
            {
                "source_id": str(index),
                "title": title,
                "url": url,
                "domain": extract_domain(url),
                "order": "reranked",
            }
        )

    return citations


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
    citations: Optional[List[Dict[str, str]]] = None,
) -> str:
    result = text.strip()

    max_chars = TOOL_RESULT_MAX_CHARS

    if len(result) <= max_chars:
        return result

    metadata: Dict[str, Any] = {
        "content_kind": "web_search_evidence_pack",
        "mode": mode,
        "queries": queries,
        "source_order": "reranked",
    }
    if citations:
        metadata["citations"] = citations

    return cache_and_format(
        session_id=session_id,
        tool_name="web_search",
        source="; ".join(queries),
        text=result,
        content_type="text/plain",
        metadata=metadata,
        limit=max_chars,
    )


async def _record_custom_provider_failure(
    context: Dict[str, Any],
    status: str,
    last_error_code: str,
) -> None:
    handler = context.get("search_provider_failure_handler")
    if not callable(handler):
        return

    maybe_awaitable = handler(status, last_error_code)
    if inspect.isawaitable(maybe_awaitable):
        await maybe_awaitable


def _format_custom_provider_error(
    *,
    public_code: str,
    last_error_code: str,
    message: Optional[str] = None,
) -> str:
    reason = message or _custom_provider_reason(last_error_code)
    return (
        "[Tool Error] The current web search mode is custom, but the configured "
        f"custom search provider is unavailable. Reason: {reason}. "
        f"Error code: {public_code}. Ask the user to recharge that provider API key, "
        "replace the key, or switch back to default search mode."
    )


def _custom_provider_reason(last_error_code: str) -> str:
    if last_error_code == "invalid_key":
        return "the API key is invalid"
    if last_error_code == "quota_exhausted":
        return "the API key quota is exhausted"
    if last_error_code == "rate_limited":
        return "the provider rate limited the request"
    if last_error_code == "timeout":
        return "the provider request timed out"
    if last_error_code == "empty_result":
        return "the provider returned no usable search results"
    if last_error_code == "not_configured":
        return "no provider or API key is configured"
    if last_error_code == "master_key_required":
        return (
            "SEARCH_PROVIDER_CREDENTIAL_MASTER_KEY is required when using stored "
            "custom search provider credentials."
        )
    return "the provider returned an error"
