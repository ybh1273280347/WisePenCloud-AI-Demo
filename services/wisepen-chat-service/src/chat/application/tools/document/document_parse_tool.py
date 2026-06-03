import json
from pathlib import PurePath
from typing import Any, Dict, List, Optional

from chat.application.infra.content_store.formatting import format_tool_content_receipt
from chat.application.infra.content_store.models import ContentReceipt
from chat.application.infra.document_temp_files.errors import (
    InvalidDocumentRefError,
    UnreadableDocumentRefError,
)
from chat.application.infra.document_temp_files.processing_scope import document_processing_scope
from chat.application.infra.document_temp_files.resolver import DocumentTempFileResolver
from chat.application.tools.common.evidence_ranking.models import RankedEvidence
from chat.application.tools.common.evidence_ranking.ranking import rank_evidence
from chat.application.tools.document.services.document_parse import (
    DocumentParseService,
)
from chat.application.tools.document.services.document_parse.models import DocumentParseResultItem
from chat.application.tools.tool_content_store import ToolContentStore
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail

_DOCUMENT_EVIDENCE_LIMIT_PER_FILE = 3

_TOOL_DESCRIPTION = (
    "Parses local or cached binary document files referenced by file_ref into "
    "Markdown text and structured tables. Only values explicitly labeled file_ref "
    "are valid inputs.\n\n"
    "Always pass all selected file_refs in one populated file_refs array.\n"
    "Never call document_parse once per file_ref for the same task.\n"
    "Never issue parallel document_parse calls for the same task.\n"
    "Never pass ToolContent content_id values such as cnt_* to document_parse.\n"
    "Never pass URLs to document_parse.\n\n"
    "Supported formats: PDF, DOCX, DOCM, PPTX, PPTM, EPUB, XLSX, XLS, XLSM, and ODS. "
    "Unsupported: HTML, TXT, MD, CSV, JSON, XML, images, audio, and video.\n\n"
    "document_parse returns complete parsed Markdown as the raw tool result. "
    "When objective is provided, document_parse also caches each parsed document "
    "as ToolContent and returns objective-relevant evidence snippets from the parsed "
    "Markdown. Use objective for the user's specific evidence goal, not a generic "
    "phrase.\n\n"
    "When more context is needed around returned evidence, call tool_content_read "
    "with the returned parsed_content_id and chunk_index. For second-pass reranking "
    "across parsed_content_id values, use evidence_rank.\n"
    "Do not pass file_ref values to evidence_rank or tool_content_read."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "file_refs": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "description": (
                "One array of file_ref values. Do not pass content_id/cnt_* values."
            ),
        },
        "objective": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Optional evidence extraction goal. When provided, document_parse "
                "caches parsed Markdown per file and returns ranked evidence snippets "
                "relevant to this goal."
            ),
        },
    },
    "required": ["file_refs"],
    "additionalProperties": False,
}


class DocumentParseTool(BaseTool):

    def __init__(
        self,
        *,
        parse_service: DocumentParseService,
        temp_file_resolver: DocumentTempFileResolver,
        content_store: ToolContentStore,
    ):
        self.parse_service = parse_service
        self.temp_file_resolver = temp_file_resolver
        self.content_store = content_store

    @property
    def name(self) -> str:
        return "document_parse"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        """执行工具入口流程。"""
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."
        user_id: Optional[str] = context.get("user_id")
        if not user_id:
            return "[Tool Error] Missing user_id in execution context."

        file_refs: List[str] = kwargs["file_refs"]
        raw_objective: Optional[str] = kwargs.get("objective")
        objective = raw_objective.strip() if raw_objective else None

        for file_ref in file_refs:
            if file_ref.startswith("cnt_"):
                return (
                    "[Tool Error] Invalid file_refs parameter: content_id values "
                    "must be passed to tool_content_read or evidence_rank, not "
                    "document_parse."
                )
            if file_ref.startswith(("http://", "https://")):
                return (
                    "[Tool Error] Invalid file_refs parameter: URLs must be passed "
                    "to web_fetch, not document_parse."
                )

        resolved_paths = []
        for file_ref in file_refs:
            try:
                resolved = self.temp_file_resolver.resolve(
                    file_ref=file_ref,
                    user_id=user_id,
                    session_id=session_id,
                )
                resolved_paths.append((file_ref, resolved.path))
            except (InvalidDocumentRefError, UnreadableDocumentRefError):
                resolved_paths.append((file_ref, None))

        valid_paths = [p for _, p in resolved_paths if p is not None]
        valid_refs = [ref for ref, p in resolved_paths if p is not None]
        failed_resolutions = [(ref, p) for ref, p in resolved_paths if p is None]

        results: List[DocumentParseResultItem] = []
        if valid_paths:
            with document_processing_scope(
                self.temp_file_resolver.session_root(
                    user_id=user_id,
                    session_id=session_id,
                )
            ):
                results = await self.parse_service.parse_many(
                    valid_paths, file_refs=valid_refs
                )

        return self._format_batch_result(
            session_id=session_id,
            results=results,
            failed_resolutions=failed_resolutions,
            objective=objective,
        )

    def _format_batch_result(
        self,
        *,
        session_id: str,
        results: List[DocumentParseResultItem],
        failed_resolutions: List[tuple],
        objective: Optional[str],
    ) -> str:

        lines: List[str] = ["[Tool Result] Document parse batch results"]

        success_count = sum(1 for r in results if r.success)
        fail_count = len(results) - success_count + len(failed_resolutions)
        total = len(results) + len(failed_resolutions)
        lines.append(
            f"Total: {total} files, {success_count} succeeded, {fail_count} failed."
        )

        for ref, _ in failed_resolutions:
            display_name = ref.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or "document"
            lines.append("")
            lines.append(f"--- File: {display_name} ---")
            lines.append("[Parse Error] Document file not found.")

        for item in results:
            lines.append("")
            display_name = (
                item.file_ref.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or "document"
            )
            lines.append(f"--- File: {display_name} ---")

            if item.success and item.result is not None:
                result = item.result
                receipt: Optional[ContentReceipt] = None
                if objective:
                    receipt = self.content_store.put_receipt(
                        session_id=session_id,
                        tool_name=self.name,
                        source=item.file_ref,
                        text=result.text,
                        content_type="text/markdown",
                        metadata=_build_document_metadata(
                            item=item,
                            objective=objective,
                        ),
                    )

                lines.append("[Parse Success]")
                lines.append(f"Source: {result.source}")
                lines.append(f"File type: {result.file_type}")
                lines.append(f"Pages: {len(result.pages)}")
                lines.append(f"Tables: {len(result.tables)}")

                if result.warnings:
                    lines.append(
                        f"Warnings: {result.warnings}"
                    )

                if result.metadata:
                    lines.append(
                        f"Metadata: {result.metadata}"
                    )

                if objective:
                    parsed_content_id = (
                        receipt.content_id if receipt is not None and receipt.cached else None
                    )
                    lines.extend(
                        _format_document_evidence_lines(
                            session_id=session_id,
                            parsed_content_id=parsed_content_id,
                            cache_error=(
                                receipt.cache_error
                                if receipt is not None
                                else "failed_to_cache_parsed_document"
                            ),
                            objective=objective,
                            content_store=self.content_store,
                        )
                    )

                    if receipt is not None:
                        lines.append("")
                        lines.append(format_tool_content_receipt(receipt))
                    else:
                        lines.extend(
                            [
                                "",
                                "[Parsed Markdown]",
                                "omitted: true",
                                "reason: Parsed Markdown could not be cached; use the parse metadata and error notes above.",
                            ]
                        )
                else:
                    lines.append("")
                    lines.append("[Parsed Markdown]")
                    lines.append(result.text)
            else:
                lines.append(f"[Parse Error] {item.error}")

        return "\n".join(lines)


def _build_document_metadata(
    *,
    item: DocumentParseResultItem,
    objective: str,
) -> Dict[str, Any]:
    result = item.result
    source = result.source if result is not None else item.file_ref
    title = PurePath(source).name or PurePath(item.file_ref).name or "document"
    metadata: Dict[str, Any] = {
        "content_kind": "document_parse_markdown",
        "file_ref": item.file_ref,
        "title": title,
        "display_name": title,
        "source": source,
        "objective": objective,
    }
    if result is not None:
        metadata.update(
            {
                "file_type": str(result.file_type),
                "page_count": len(result.pages),
                "table_count": len(result.tables),
            }
        )
    return metadata


def _format_document_evidence_lines(
    *,
    session_id: str,
    parsed_content_id: Optional[str],
    cache_error: Optional[str],
    objective: str,
    content_store: ToolContentStore,
) -> List[str]:
    lines = [
        "",
        "[DocumentParse Evidence]",
        f"parsed_content_id: {parsed_content_id or ''}",
        f"objective: {objective}",
        "extraction_method: lexical_chunk_ranking",
        "final_answer_generated: false",
        "note: Evidence snippets are extracted from parsed Markdown; document_parse does not answer the user directly.",
    ]

    if not parsed_content_id:
        lines.extend(
            [
                "needs_more_context: true",
                "extracted_evidence: []",
                "notes:",
                f"- Parsed document was not cached: {cache_error or 'unknown cache error'}.",
            ]
        )
        return lines

    try:
        ranking_result = rank_evidence(
            query=objective,
            content_ids=[parsed_content_id],
            session_id=session_id,
            max_evidence=_DOCUMENT_EVIDENCE_LIMIT_PER_FILE,
            content_store=content_store,
        )
    except Exception as e:
        log_fail(
            "document_parse evidence extraction",
            repr(e),
            session_id=session_id,
            content_id=parsed_content_id,
        )
        lines.extend(
            [
                "needs_more_context: true",
                "extracted_evidence: []",
                "notes:",
                "- Document evidence extraction failed; parsed Markdown remains cached.",
            ]
        )
        return lines

    evidence_items = [
        evidence
        for evidence in ranking_result.evidence
        if evidence.content_id == parsed_content_id and evidence.chunk_index >= 0
    ]
    lines.append(f"needs_more_context: {str(not evidence_items).lower()}")

    if not evidence_items:
        lines.append("extracted_evidence: []")
    else:
        lines.append("extracted_evidence:")
        for index, evidence in enumerate(evidence_items, 1):
            lines.extend(_format_document_evidence_item(index=index, evidence=evidence))

    if ranking_result.notes:
        lines.append("notes:")
        for note in ranking_result.notes:
            lines.append(f"- {note}")

    return lines


def _format_document_evidence_item(
    *,
    index: int,
    evidence: RankedEvidence,
) -> List[str]:
    lines = [
        f"- rank: {index}",
        f"  parsed_content_id: {evidence.content_id}",
        f"  chunk_index: {evidence.chunk_index}",
        f"  title: {evidence.display_title}",
        f"  start_offset: {evidence.start_offset}",
        f"  end_offset: {evidence.end_offset}",
        f"  lexical_score: {evidence.score:.4f}",
        f"  matched_reason: {evidence.matched_reason}",
    ]

    if evidence.term_hit_stats:
        term_hit_stats = []
        for stat in evidence.term_hit_stats:
            term_hit_stats.append(
                {
                    "term": stat.term,
                    "total_count": stat.total_count,
                    "field_stats": [
                        {"field": field_stat.field, "count": field_stat.count}
                        for field_stat in stat.field_stats
                    ],
                }
            )
        lines.append(
            "  term_hit_stats: " + json.dumps(term_hit_stats, ensure_ascii=False)
        )

    if evidence.excerpt:
        lines.append("  excerpt: |-")
        for excerpt_line in evidence.excerpt.splitlines():
            lines.append(f"    {excerpt_line}")

    lines.append("  suggested_next_action: use_document_evidence_or_read_more_context")
    return lines
