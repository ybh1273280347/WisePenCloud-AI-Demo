import inspect
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from chat.application.algorithms.url import canonicalize_url
from chat.application.tools.common.tool_content_store import (
    cache_artifact_and_format_receipt,
)
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
    "Use web_search when the user needs current or external web recall, source discovery, "
    "official pages, images, broad comparison, or candidate URLs for later page-body fetches.\n\n"
    "Always issue at most one web_search call per user request.\n"
    "Always put all query variants into that single queries array.\n"
    "Always pass 2-4 short search-engine-style queries.\n"
    "Every call MUST include at least one pure English query. A pure English query uses only "
    "ASCII English words, numbers, and punctuation, while preserving key technical terms in English.\n"
    "Never call web_search with only one query.\n"
    "Never pass more than four queries.\n"
    "Other queries may use the user's language, English, or any language directly useful for the task.\n\n"
    "Mode rules: choose the most specific mode.\n"
    "Never use normal as the default.\n"
    "- fast: quick facts, definitions, official sites, images/photos, lightweight overviews, "
    "or questions answerable from snippets/images.\n"
    "- normal: medium-complexity searches that need a small candidate URL list but do not meet "
    "fast or deep conditions.\n"
    "- deep: technical research, engineering decisions, academic/paper research, community best "
    "practices, official documentation comparison, multi-source verification, recent rules/prices/"
    "laws/news, medical/legal/financial accuracy, or broad bilingual recall.\n"
    "Never call web_fetch after fast mode.\n\n"
    "Return protocol: fast mode returns lightweight visible snippets/images that can be used "
    "directly when sufficient. normal and deep mode return only a ToolContent Receipt for a "
    "cached JSON evidence artifact. The receipt contains content_id and required_next_tool. "
    "The search snippets and source list are intentionally omitted from the visible tool result "
    "in normal/deep mode.\n\n"
    "After normal/deep web_search, you MUST call evidence_rank with the user's question and "
    "the returned content_id before answering.\n\n"
    "web_search candidate pages have NOT been fetched. If page-body evidence is needed after "
    "ranking, use web_fetch on the selected URLs."
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
                "Two to four distinct query variants for the same user intent. "
                "Include at least one pure English query."
            ),
        },
        "wikipedia_keywords": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 3,
            "description": (
                "Optional short entity/concept keywords for Wikipedia grounding. "
                "Use entities, organizations, products, laws, or technical terms; not full questions."
            ),
        },
        "mode": {
            "type": "string",
            "enum": ["fast", "normal", "deep"],
            "description": (
                "Explicit search depth: fast, normal, or deep. Follow the mode rules in the tool description."
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

    if mode == "normal":
        return _OutputBudget(
            candidate_page_limit=_NORMAL_CANDIDATE_PAGE_LIMIT,
            source_display_limit=_NORMAL_SOURCE_DISPLAY_LIMIT,
        )

    raise ValueError(f"Unsupported web_search mode: {mode}")


def _is_pure_english_query(query: str) -> bool:
    normalized = query.strip()
    if not normalized:
        return False
    if any(not char.isascii() for char in normalized):
        return False
    return any(char.isalpha() for char in normalized)


def _has_pure_english_query(queries: List[str]) -> bool:
    return any(_is_pure_english_query(query) for query in queries)


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

        raw_queries = kwargs.get("queries")

        if not isinstance(raw_queries, list):
            return "[Tool Error] queries must be a list of strings."

        if (
            len(raw_queries) < _SEARCH_QUERY_MIN_COUNT
            or len(raw_queries) > _SEARCH_QUERY_LIMIT
        ):
            return "[Tool Error] web_search requires 2-4 search queries."

        for query in raw_queries:
            if type(query) is not str:
                return "[Tool Error] queries items must be strings."
            if not query:
                return "[Tool Error] queries items must be non-empty strings."
            if query.strip() != query:
                return (
                    "[Tool Error] queries items must not contain leading or trailing "
                    "whitespace."
                )

        queries, _ = normalize_queries(
            raw_queries,
            limit=_SEARCH_QUERY_LIMIT,
            notes=notes,
        )
        if len(queries) != len(raw_queries):
            return (
                "[Tool Error] queries must be distinct after normalization; do not "
                "pass duplicate or equivalent queries."
            )

        if not _has_pure_english_query(queries):
            return (
                "[Tool Error] web_search requires at least one pure English query. "
                "A pure English query must be written with ASCII English words, numbers, and punctuation only, "
                "and should preserve key technical terms in English. "
                "Keep any non-English query only when it is useful for the user's request, and add one pure English query. "
                "You MUST call web_search again immediately. Do not proceed without searching."
            )

        mode = kwargs.get("mode")
        if type(mode) is not str:
            return (
                "[Tool Error] mode is required and must be one of: fast, normal, "
                "deep."
            )
        if mode not in {"fast", "normal", "deep"}:
            return "[Tool Error] mode must be one of: fast, normal, deep."

        output = _get_output_budget(mode)
        with_images = kwargs.get("with_images", False)
        if type(with_images) is not bool:
            return "[Tool Error] with_images must be a boolean."

        runtime_context = get_runtime_context(context)
        language = kwargs.get("language")
        if language is not None and language not in {"en", "zh-CN"}:
            return "[Tool Error] language must be one of: en, zh-CN."
        wikipedia_keywords = kwargs.get("wikipedia_keywords")
        if wikipedia_keywords is not None:
            if not isinstance(wikipedia_keywords, list):
                return "[Tool Error] wikipedia_keywords must be a list of strings."
            if len(wikipedia_keywords) > 3:
                return "[Tool Error] wikipedia_keywords accepts at most 3 items."
            for item in wikipedia_keywords:
                if type(item) is not str or not item:
                    return (
                        "[Tool Error] wikipedia_keywords items must be non-empty "
                        "strings."
                    )
                if item.strip() != item:
                    return (
                        "[Tool Error] wikipedia_keywords items must not contain "
                        "leading or trailing whitespace."
                    )
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

        if mode == "fast":
            return _format_response(
                response,
                mode=mode,
                queries=queries,
                notes=notes,
                candidate_pages=candidate_pages,
                source_display_limit=output.source_display_limit,
                grounding=grounding,
            )

        citations = _build_citations(response)
        artifact_text = _build_web_search_artifact_json(
            response=response,
            mode=mode,
            queries=queries,
            notes=notes,
            candidate_pages=candidate_pages,
            grounding=grounding,
        )

        metadata: Dict[str, Any] = {
            "content_kind": "web_search_evidence_pack",
            "mode": mode,
            "queries": queries,
            "source_order": "reranked",
            "required_next_tool": "evidence_rank",
            "blocking_final_answer": True,
            "result_count": len(response.results),
            "candidate_page_count": len(candidate_pages),
            "unique_domain_count": count_unique_domains(tuple(response.results)),
            "citations": citations,
        }

        return cache_artifact_and_format_receipt(
            session_id=session_id,
            tool_name="web_search",
            source="; ".join(queries),
            text=artifact_text,
            content_type="application/json",
            metadata=metadata,
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
        "Tool-use note: fast mode returns snippets/images only. Never call web_fetch after fast mode. "
        "normal/deep mode returns candidate evidence, not fetched page bodies. "
        "Use web_fetch on selected URLs only when page-body evidence is needed."
    )
    if response.images or any(result.images for result in response.results):
        lines.append(
            "Image note: image URLs and source page URLs are available in this result."
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
            f"{_candidate_fetch_requirement(mode)} "
            "If page-body evidence is needed, select only the necessary URLs and pass them to "
            "web_fetch in one urls array. Candidate pages are marked with [C]."
        )
        for result_index, _ in candidate_pages:
            lines.append(f"  [C] [{result_index}]")
    else:
        candidate_indices = set()

    if display_results:
        lines.append("\nSources (reranked order for citations):")
        lines.append(
            "Evidence only. Source markers [1], [2], ... and citation metadata use this reranked order, "
            "not the original search-provider order."
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


def _build_web_search_artifact_json(
    *,
    response: SearchResponse,
    mode: str,
    queries: List[str],
    notes: List[str],
    candidate_pages: List[Tuple[int, SearchResult]],
    grounding: Tuple[WikipediaGroundingResult, ...],
) -> str:
    candidate_source_ids = {str(index) for index, _ in candidate_pages}

    payload: Dict[str, Any] = {
        "content_kind": "web_search_evidence_pack",
        "mode": mode,
        "queries": queries,
        "source_order": "reranked",
        "summary": {
            "result_count": len(response.results),
            "query_image_count": len(response.images),
            "unique_domain_count": count_unique_domains(tuple(response.results)),
            "candidate_page_count": len(candidate_pages),
            "source": response.source,
        },
        "results": [],
        "candidate_pages": [],
        "grounding": [],
        "notes": _deduplicate_notes(notes),
        "citations": _build_citations(response),
    }

    for index, result in enumerate(response.results, 1):
        url = result.url.strip()
        title = result.title.strip() or url or "(no title)"
        domain = extract_domain(url)

        payload["results"].append(
            {
                "source_id": str(index),
                "title": title,
                "url": url,
                "domain": domain,
                "snippet": result.snippet.strip(),
                "is_candidate_page": str(index) in candidate_source_ids,
                "images": [
                    {
                        "url": image.url.strip(),
                        "desc": image.desc.strip() if image.desc else "",
                        "resolution": image.resolution,
                        "source_url": image.source_url,
                    }
                    for image in result.images
                    if image.url.strip()
                ],
            }
        )

    for index, result in candidate_pages:
        url = result.url.strip()
        payload["candidate_pages"].append(
            {
                "source_id": str(index),
                "title": result.title.strip() or url or "(no title)",
                "url": url,
                "domain": extract_domain(url),
            }
        )

    for item in grounding:
        payload["grounding"].append(
            {
                "keyword": item.keyword.text,
                "title": item.title,
                "language": item.language,
                "extract": item.extract,
                "url": item.url,
            }
        )

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


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
