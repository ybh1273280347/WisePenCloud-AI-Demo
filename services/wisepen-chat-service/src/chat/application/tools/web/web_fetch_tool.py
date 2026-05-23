import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from chat.application.tools.common.file_handoff import TemporaryFileHandoffStore
from chat.application.tools.common.security.references import reject_non_url_reference
from chat.application.tools.common.tool_content_store import (
    cache_and_window,
    tool_content_store,
)
from chat.application.tools.config import TOOL_RESULT_MAX_CHARS
from chat.application.tools.services.evidence_ranking import rank_evidence
from chat.application.tools.services.evidence_ranking.models import RankedEvidence
from chat.application.tools.services.web_fetch import (
    FetchCoordinator,
    FetchedDocument,
    FetchResultItem,
)
from chat.application.tools.services.web_fetch.utils.page_metadata import (
    extract_markdown_title,
    extract_page_domain,
)
from chat.application.tools.services.web_fetch.utils.url_batching import (
    MAX_FETCH_URLS,
    UrlBatchInputError,
    normalize_urls,
)
from chat.core.content_store.formatters import format_tool_content_window
from chat.core.content_store.models import ContentWindow
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_event, log_fail

_PAGE_EVIDENCE_LIMIT_PER_PAGE = 3

_TOOL_DESCRIPTION = (
    "Fetches one or more web URLs concurrently. Use this tool when the user provides URL(s), "
    "or after web_search returns candidate URLs that need page-body evidence.\n\n"
    "Always pass all selected URLs in one urls array.\n"
    "Each array item MUST be exactly one http:// or https:// URL.\n"
    "Never call web_fetch once per URL.\n"
    "Never put multiple URLs into one string item.\n\n"
    "After web_search/evidence_rank, prefer passing from_search_content_id plus source_ids "
    "instead of manually copying URLs. web_fetch will resolve source_ids from the cached "
    "web_search evidence pack, fetch those URLs, and preserve source provenance.\n\n"
    "HTML pages return readable Markdown content. Long content may be returned as ToolContent "
    "windows with content_id=cnt_* and next_offset.\n"
    "When objective is provided, web_fetch caches the full raw Markdown first, then returns "
    "objective-relevant page evidence extracted from the cached raw content. This extraction "
    "does not generate a final answer.\n"
    "When web_fetch returns cached content_ids, use the returned page evidence, evidence_rank "
    "for alternate ranking, or tool_content_read to continue a known window.\n"
    "content_id is not file_ref.\n\n"
    "Direct document links such as PDF, DOCX, PPTX, EPUB, XLSX, XLSM, XLS, or ODS are downloaded "
    "and returned as file_ref handoffs instead of being parsed.\n"
    "After web_fetch returns file_ref values, pass all file_refs together to document_parse in one call."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "urls": {
            "type": "array",
            "items": {
                "type": "string",
                "minLength": 1,
            },
            "minItems": 1,
            "maxItems": MAX_FETCH_URLS,
            "uniqueItems": True,
            "description": (
                "One array of http:// or https:// URLs. Each item is one URL."
            ),
        },
        "from_search_content_id": {
            "type": "string",
            "description": (
                "Optional content_id from a prior web_search evidence pack. "
                "Use together with source_ids so web_fetch can resolve URLs automatically."
            ),
        },
        "source_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": MAX_FETCH_URLS,
            "uniqueItems": True,
            "description": (
                "Source IDs from a prior web_search/evidence_rank result to fetch."
            ),
        },
        "objective": {
            "type": "string",
            "description": (
                "Optional information goal for extracting page evidence from fetched raw Markdown. "
                "web_fetch caches the full raw page and returns relevant evidence snippets, not a final answer."
            ),
        },
    },
    "anyOf": [
        {"required": ["urls"]},
        {"required": ["from_search_content_id", "source_ids"]},
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class _FetchSourceContext:
    from_search_content_id: str = ""
    source_id: str = ""
    title: str = ""
    url: str = ""
    domain: str = ""


@dataclass(frozen=True, slots=True)
class _FetchTarget:
    url: str
    source_context: Optional[_FetchSourceContext] = None


class WebFetchTool(BaseTool):
    """web_fetch tool entrypoint."""

    def __init__(
        self,
        fetcher: FetchCoordinator,
        file_handoff_store: TemporaryFileHandoffStore,
    ):
        self._fetcher = fetcher
        self._file_handoff_store = file_handoff_store

    @property
    def name(self) -> str:
        return "web_fetch"

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
        user_id: Optional[str] = context.get("user_id")
        if not user_id:
            return "[Tool Error] Missing user_id in execution context."

        targets_or_error = self._resolve_fetch_targets(
            session_id=session_id,
            kwargs=kwargs,
        )
        if isinstance(targets_or_error, str):
            return targets_or_error

        targets = targets_or_error
        if not targets:
            return (
                "[Tool Error] Missing required input: pass urls, or pass "
                "from_search_content_id with source_ids."
            )

        urls = [target.url for target in targets]

        for url in urls:
            reference_kind = reject_non_url_reference(url)
            if reference_kind is not None:
                return (
                    f"[Tool Error] Invalid urls parameter: {reference_kind} is not a URL. "
                    "Do not pass internal references to web_fetch."
                )

        invalid_urls = [
            url for url in urls if not url.startswith(("http://", "https://"))
        ]
        if invalid_urls:
            return "[Tool Error] Invalid urls parameter: every urls item must be an http:// or https:// URL."

        log_event(
            "web_fetch normalized urls",
            normalized_url_count=len(urls),
            fetch_many_batch_size=len(urls),
        )

        results: List[FetchResultItem] = await self._fetcher.fetch_many(urls)

        return self._format_batch_result(
            user_id=user_id,
            session_id=session_id,
            results=results,
            target_contexts=[target.source_context for target in targets],
            objective=kwargs.get("objective"),
        )

    def _resolve_fetch_targets(
        self,
        *,
        session_id: str,
        kwargs: Dict[str, Any],
    ) -> List[_FetchTarget] | str:
        raw_objective = kwargs.get("objective")
        if raw_objective is not None:
            if type(raw_objective) is not str:
                return "[Tool Error] objective must be a string."
            if not raw_objective.strip():
                return "[Tool Error] objective must be a non-empty string."
            if raw_objective.strip() != raw_objective:
                return (
                    "[Tool Error] objective must not contain leading or trailing "
                    "whitespace."
                )

        targets: List[_FetchTarget] = []
        seen_urls: set[str] = set()

        raw_urls = kwargs.get("urls")
        if raw_urls is not None:
            try:
                urls = normalize_urls(raw_urls)
            except UrlBatchInputError as e:
                return f"[Tool Error] Invalid urls parameter: {e}"

            for url in urls:
                self._append_target(
                    targets=targets,
                    seen_urls=seen_urls,
                    target=_FetchTarget(url=url),
                )

        from_search_content_id = kwargs.get("from_search_content_id")
        raw_source_ids = kwargs.get("source_ids")
        if from_search_content_id is None and raw_source_ids is None:
            return targets

        if type(from_search_content_id) is not str or not from_search_content_id:
            return (
                "[Tool Error] from_search_content_id must be a non-empty content_id "
                "when source_ids is provided."
            )
        if not isinstance(raw_source_ids, list):
            return "[Tool Error] source_ids must be a list of strings."

        source_ids: List[str] = []
        seen_source_ids: set[str] = set()
        for item in raw_source_ids:
            if type(item) is not str or not item:
                return "[Tool Error] source_ids items must be non-empty strings."
            if item in seen_source_ids:
                return "[Tool Error] source_ids must be distinct."
            seen_source_ids.add(item)
            source_ids.append(item)

        if len(source_ids) > MAX_FETCH_URLS:
            return f"[Tool Error] source_ids accepts at most {MAX_FETCH_URLS} items."

        resolved_or_error = _resolve_source_id_targets(
            session_id=session_id,
            from_search_content_id=from_search_content_id,
            source_ids=source_ids,
        )
        if isinstance(resolved_or_error, str):
            return resolved_or_error

        for target in resolved_or_error:
            self._append_target(
                targets=targets,
                seen_urls=seen_urls,
                target=target,
            )

        if len(targets) > MAX_FETCH_URLS:
            return f"[Tool Error] web_fetch accepts at most {MAX_FETCH_URLS} URLs per call."

        return targets

    @staticmethod
    def _append_target(
        *,
        targets: List[_FetchTarget],
        seen_urls: set[str],
        target: _FetchTarget,
    ) -> None:
        if target.url in seen_urls:
            return

        seen_urls.add(target.url)
        targets.append(target)

    def _format_batch_result(
        self,
        *,
        user_id: str,
        session_id: str,
        results: List[FetchResultItem],
        target_contexts: List[Optional[_FetchSourceContext]],
        objective: Optional[str],
    ) -> str:
        lines: List[str] = ["[Tool Result] web_fetch 批量结果"]

        success_count = sum(1 for r in results if r.success)
        fail_count = len(results) - success_count
        lines.append(
            f"Total: {len(results)} URLs，{success_count} 个已完成，{fail_count} 个未完成。"
        )

        for item, source_context in zip(results, target_contexts):
            if item.success:
                if item.document is not None:
                    lines.append("")
                    lines.extend(
                        self._format_document_handoff_lines(
                            user_id=user_id,
                            session_id=session_id,
                            document=item.document,
                        )
                    )
                elif item.content is not None:
                    lines.append("")
                    lines.append(f"--- URL: {item.url} ---")
                    lines.extend(
                        _format_page_provenance_lines(
                            item=item,
                            source_context=source_context,
                        )
                    )
                    raw_window = cache_and_window(
                        session_id=session_id,
                        tool_name=self.name,
                        source=item.url,
                        text=item.content,
                        content_type="text/markdown",
                        metadata=_build_page_metadata(
                            item=item,
                            source_context=source_context,
                            objective=objective,
                        ),
                        limit=TOOL_RESULT_MAX_CHARS,
                    )
                    lines.append(format_tool_content_window(raw_window))
                    if objective:
                        lines.extend(
                            _format_page_evidence_lines(
                                session_id=session_id,
                                raw_window=raw_window,
                                objective=objective,
                            )
                        )
            else:
                lines.append("")
                lines.append(f"--- URL: {item.url} ---")
                if source_context is not None:
                    lines.append(f"source_id: {source_context.source_id}")
                    lines.append(
                        f"from_search_content_id: {source_context.from_search_content_id}"
                    )
                if item.redirect_url:
                    lines.extend(_format_redirect_lines(item))
                    continue
                lines.append(f"[Fetch Error] {item.error}")

        return "\n".join(lines)

    def _format_document_handoff_lines(
        self,
        *,
        user_id: str,
        session_id: str,
        document: FetchedDocument,
    ) -> List[str]:
        handoff = self._file_handoff_store.write_bytes(
            user_id=user_id,
            session_id=session_id,
            filename=document.filename,
            content=document.content,
            canonical_suffix=Path(document.filename).suffix,
            content_type=document.media_type,
        )
        file_ref = handoff.file_ref

        log_event(
            "web_fetch document handoff cached",
            file_ref=file_ref,
            source_url=document.url,
            size=len(document.content),
            content_type=document.media_type,
        )

        return [
            f"--- URL: {document.url} ---",
            "Downloaded a document file. Web Fetch does not parse document content.",
            f"file_ref: {file_ref}",
            f"source_url: {document.url}",
            f"filename: {document.filename}",
            f"content_type: {document.media_type}",
            f"size_bytes: {len(document.content)}",
            "next_step: Collect every file_ref from this web_fetch batch, inject all of them into "
            "one document_parse file_refs list, and call document_parse once: "
            "document_parse(file_refs=[file_ref_1, file_ref_2, ...]).",
        ]


def _resolve_source_id_targets(
    *,
    session_id: str,
    from_search_content_id: str,
    source_ids: List[str],
) -> List[_FetchTarget] | str:
    stored = tool_content_store.get(
        content_id=from_search_content_id,
        session_id=session_id,
    )
    if stored is None:
        return (
            "[Tool Error] from_search_content_id not found, expired, or inaccessible."
        )

    try:
        payload = json.loads(stored.text)
    except json.JSONDecodeError:
        return "[Tool Error] from_search_content_id does not contain valid JSON."

    if not isinstance(payload, dict):
        return "[Tool Error] from_search_content_id does not contain a web_search evidence pack."

    content_kind = stored.metadata.get("content_kind") or payload.get("content_kind")
    if content_kind != "web_search_evidence_pack":
        return (
            "[Tool Error] from_search_content_id must reference a web_search evidence pack."
        )

    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        return "[Tool Error] web_search evidence pack is missing results."

    results_by_source_id: Dict[str, Dict[str, Any]] = {}
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        source_id = item.get("source_id")
        if isinstance(source_id, str) and source_id:
            results_by_source_id[source_id] = item

    missing = [
        source_id for source_id in source_ids if source_id not in results_by_source_id
    ]
    if missing:
        return (
            "[Tool Error] source_ids not found in from_search_content_id: "
            + ", ".join(missing)
            + "."
        )

    targets: List[_FetchTarget] = []
    for source_id in source_ids:
        item = results_by_source_id[source_id]
        raw_url = item.get("url")
        if not isinstance(raw_url, str) or not raw_url.strip():
            return f"[Tool Error] source_id {source_id} has no URL."

        try:
            urls = normalize_urls([raw_url])
        except UrlBatchInputError as e:
            return f"[Tool Error] source_id {source_id} has invalid URL: {e}"

        if not urls:
            return f"[Tool Error] source_id {source_id} has no URL."

        url = urls[0]
        title = item.get("title")
        domain = item.get("domain")
        targets.append(
            _FetchTarget(
                url=url,
                source_context=_FetchSourceContext(
                    from_search_content_id=from_search_content_id,
                    source_id=source_id,
                    title=title if isinstance(title, str) else "",
                    url=url,
                    domain=domain if isinstance(domain, str) else extract_page_domain(url),
                ),
            )
        )

    return targets


def _build_page_metadata(
    *,
    item: FetchResultItem,
    source_context: Optional[_FetchSourceContext],
    objective: Optional[str],
) -> Dict[str, Any]:
    final_url = item.final_url or item.url
    title = item.title or (source_context.title if source_context else "")
    if not title:
        title = extract_markdown_title(item.content or "")

    domain = (
        item.domain
        or extract_page_domain(final_url)
        or (source_context.domain if source_context else "")
    )

    metadata: Dict[str, Any] = {
        "content_kind": "web_page",
        "url": item.url,
        "final_url": final_url,
        "title": title,
        "domain": domain,
        "status_code": item.status_code,
        "fetcher": item.fetcher or "",
        "cache_hit": item.fetcher == "cache",
        "source_id": source_context.source_id if source_context else "",
        "from_search_content_id": (
            source_context.from_search_content_id if source_context else ""
        ),
    }
    if objective:
        metadata["objective"] = objective

    return metadata


def _format_page_evidence_lines(
    *,
    session_id: str,
    raw_window: ContentWindow,
    objective: str,
) -> List[str]:
    lines = [
        "",
        "[Page Evidence]",
        f"raw_content_id: {raw_window.content_id}",
        f"objective: {objective}",
        "extraction_method: lexical_chunk_ranking",
        "final_answer_generated: false",
        "note: Evidence snippets are extracted from cached raw Markdown; web_fetch does not answer the user directly.",
    ]

    if not raw_window.cached or not raw_window.content_id:
        lines.extend(
            [
                "needs_more_context: true",
                "extracted_evidence: []",
                "notes:",
                f"- Raw page content was not cached: {raw_window.cache_error or 'unknown cache error'}.",
            ]
        )
        return lines

    try:
        ranking_result = rank_evidence(
            query=objective,
            content_ids=[raw_window.content_id],
            session_id=session_id,
            max_evidence=_PAGE_EVIDENCE_LIMIT_PER_PAGE,
        )
    except Exception as e:
        log_fail(
            "web_fetch page evidence extraction",
            repr(e),
            session_id=session_id,
            content_id=raw_window.content_id,
        )
        lines.extend(
            [
                "needs_more_context: true",
                "extracted_evidence: []",
                "notes:",
                "- Page evidence extraction failed; raw Markdown remains cached.",
            ]
        )
        return lines

    evidence_items = [
        evidence
        for evidence in ranking_result.evidence
        if evidence.content_id == raw_window.content_id and evidence.chunk_index >= 0
    ]
    needs_more_context = not evidence_items
    lines.append(f"needs_more_context: {str(needs_more_context).lower()}")

    if not evidence_items:
        lines.append("extracted_evidence: []")
    else:
        lines.append("extracted_evidence:")
        for index, evidence in enumerate(evidence_items, 1):
            lines.extend(_format_page_evidence_item(index=index, evidence=evidence))

    if ranking_result.notes:
        lines.append("notes:")
        for note in ranking_result.notes:
            lines.append(f"- {note}")

    return lines


def _format_page_evidence_item(
    *,
    index: int,
    evidence: RankedEvidence,
) -> List[str]:
    lines = [
        f"- rank: {index}",
        f"  raw_content_id: {evidence.content_id}",
        f"  chunk_index: {evidence.chunk_index}",
        f"  title: {evidence.display_title}",
    ]
    if evidence.url:
        lines.append(f"  url: {evidence.url}")
    if evidence.domain:
        lines.append(f"  domain: {evidence.domain}")

    lines.extend(
        [
            f"  start_offset: {evidence.start_offset}",
            f"  end_offset: {evidence.end_offset}",
            f"  lexical_score: {evidence.score:.4f}",
            f"  matched_reason: {evidence.matched_reason}",
        ]
    )

    if evidence.term_hit_stats:
        lines.append(
            "  term_hit_stats: "
            + json.dumps(_format_term_hit_stats(evidence), ensure_ascii=False)
        )

    if evidence.excerpt:
        lines.append("  excerpt: |-")
        for excerpt_line in evidence.excerpt.splitlines():
            lines.append(f"    {excerpt_line}")

    lines.append("  suggested_next_action: use_page_evidence_or_read_more_context")
    return lines


def _format_redirect_lines(item: FetchResultItem) -> List[str]:
    lines = [
        "[Redirect]",
        f"redirect_url: {item.redirect_url}",
        (
            "redirect_policy: same-host redirects are followed automatically; "
            "cross-host redirects require an explicit follow-up fetch."
        ),
        (
            "next_step: decide whether the redirect target is relevant, then call "
            "web_fetch with redirect_url if needed."
        ),
    ]
    if item.status_code is not None:
        lines.insert(2, f"status_code: {item.status_code}")
    if item.fetcher:
        lines.append(f"fetcher: {item.fetcher}")
    if item.error:
        lines.append(f"note: {item.error}")
    return lines


def _format_term_hit_stats(evidence: RankedEvidence) -> List[Dict[str, Any]]:
    stats: List[Dict[str, Any]] = []
    for stat in evidence.term_hit_stats:
        field_stats = [
            {"field": field_stat.field, "count": field_stat.count}
            for field_stat in stat.field_stats
        ]
        stats.append(
            {
                "term": stat.term,
                "total_count": stat.total_count,
                "field_stats": field_stats,
            }
        )

    return stats


def _format_page_provenance_lines(
    *,
    item: FetchResultItem,
    source_context: Optional[_FetchSourceContext],
) -> List[str]:
    metadata = _build_page_metadata(
        item=item,
        source_context=source_context,
        objective=None,
    )
    lines: List[str] = []

    for key in (
        "source_id",
        "from_search_content_id",
        "title",
        "domain",
        "final_url",
        "status_code",
        "fetcher",
        "cache_hit",
    ):
        value = metadata.get(key)
        if value in ("", None):
            continue
        if isinstance(value, bool):
            value = str(value).lower()
        lines.append(f"{key}: {value}")

    return lines
