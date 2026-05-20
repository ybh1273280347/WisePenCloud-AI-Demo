from pathlib import Path
from typing import Any, Dict, List, Optional

from chat.application.tools.services.document_parse import DocumentParseService
from chat.application.tools.services.document_parse.document_parse_service import (
    DocumentParseResultItem,
)
from chat.application.tools.services.document_file import (
    DocumentTempFileResolver,
    InvalidDocumentRefError,
    UnreadableDocumentRefError,
    document_processing_scope,
)
from chat.application.tools.common.tool_content_store import cache_and_format
from chat.application.tools.config import TOOL_RESULT_MAX_CHARS
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_event, log_fail, log_ok

_TOOL_DESCRIPTION = (
    "Parses local or cached binary document files referenced by file_ref into Markdown text "
    "and structured tables. Only values explicitly labeled file_ref are valid inputs.\n\n"
    "Always pass all selected file_refs in one populated file_refs array.\n"
    "Never call document_parse once per file_ref for the same task.\n"
    "Never issue parallel document_parse calls for the same task.\n"
    "Never pass ToolContent content_id values such as cnt_* to document_parse.\n"
    "Never pass URLs to document_parse.\n\n"
    "Supported formats: PDF, DOCX, DOCM, PPTX, PPTM, EPUB, XLSX, XLS, XLSM, and ODS. "
    "Unsupported: HTML, TXT, MD, CSV, JSON, XML, images, audio, and video.\n\n"
    "When document_parse returns content_id, the parsed document is available as cached "
    "ToolContent. Use evidence_rank to locate relevant passages, or tool_content_read to "
    "continue a known window by next_offset.\n"
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
    ):
        self.parse_service = parse_service
        self.temp_file_resolver = temp_file_resolver

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
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."
        user_id: Optional[str] = context.get("user_id")
        if not user_id:
            return "[Tool Error] Missing user_id in execution context."

        file_refs: List[str] = kwargs.get("file_refs", [])
        if not file_refs:
            return "[Tool Error] Missing required file_refs parameter."

        invalid_reason = _invalid_file_ref_reason(file_refs)
        if invalid_reason is not None:
            return f"[Tool Error] Invalid file_refs parameter: {invalid_reason}"

        resolved_paths = []
        for file_ref in file_refs:
            try:
                resolved = self.temp_file_resolver.resolve(
                    file_ref=file_ref,
                    user_id=user_id,
                    session_id=session_id,
                )
                resolved_paths.append((file_ref, resolved.path))
            except (InvalidDocumentRefError, UnreadableDocumentRefError) as e:
                resolved_paths.append((file_ref, None))
                log_fail(
                    "document_parse file_ref",
                    repr(e),
                    session_id=session_id,
                    file_ref=file_ref,
                )

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

        self._log_batch_result(
            user_id=user_id,
            session_id=session_id,
            results=results,
            failed_resolutions=failed_resolutions,
        )

        return self._format_batch_result(
            session_id=session_id,
            results=results,
            failed_resolutions=failed_resolutions,
        )

    def _format_batch_result(
        self,
        *,
        session_id: str,
        results: List[DocumentParseResultItem],
        failed_resolutions: List[tuple],
    ) -> str:
        lines: List[str] = ["[Tool Result] Document parse batch results"]

        success_count = sum(1 for r in results if r.success)
        fail_count = len(results) - success_count + len(failed_resolutions)
        total = len(results) + len(failed_resolutions)
        lines.append(
            f"Total: {total} files, {success_count} succeeded, {fail_count} failed."
        )

        for ref, _ in failed_resolutions:
            display_name = _display_file_ref(ref)
            lines.append("")
            lines.append(f"--- File: {display_name} ---")
            lines.append("[Parse Error] Document file not found.")

        for item in results:
            if item.success and item.result is not None:
                result = item.result
                metadata: Dict[str, Any] = {
                    **result.metadata,
                    "tool": self.name,
                    "file_type": result.file_type,
                    "source": result.source,
                    "page_count": len(result.pages),
                    "table_count": len(result.tables),
                    "warnings": result.warnings,
                }

                log_event(
                    "document_parse 单文件完成",
                    session_id=session_id,
                    file_ref=item.file_ref,
                    parser=metadata.get("parser"),
                    selected_parser=metadata.get("selected_parser"),
                    file_type=result.file_type,
                    page_count=len(result.pages),
                    table_count=len(result.tables),
                    length=len(result.text),
                )

                lines.append("")
                lines.append(f"--- File: {_display_file_ref(item.file_ref)} ---")
                cached = cache_and_format(
                    session_id=session_id,
                    tool_name=self.name,
                    source=result.source,
                    text=result.text,
                    content_type="text/markdown",
                    metadata=metadata,
                    limit=TOOL_RESULT_MAX_CHARS,
                )
                lines.append(cached)
            else:
                lines.append("")
                lines.append(f"--- File: {_display_file_ref(item.file_ref)} ---")
                lines.append(f"[Parse Error] {item.error}")

        return "\n".join(lines)

    def _log_batch_result(
        self,
        *,
        user_id: str,
        session_id: str,
        results: List[DocumentParseResultItem],
        failed_resolutions: List[tuple],
    ) -> None:
        success_count = sum(1 for item in results if item.success)
        fail_count = len(results) - success_count + len(failed_resolutions)
        total = len(results) + len(failed_resolutions)

        successful_refs = []
        parser_counts: Dict[str, int] = {}
        for item in results:
            if not item.success or item.result is None:
                continue
            successful_refs.append(item.file_ref)
            parser = str(
                item.result.metadata.get("selected_parser")
                or item.result.metadata.get("parser")
                or item.result.file_type
            )
            parser_counts[parser] = parser_counts.get(parser, 0) + 1

        unresolved = [ref for ref, _ in failed_resolutions]
        parse_errors = [
            {
                "file_ref": item.file_ref,
                "原因": item.error,
            }
            for item in results
            if not item.success
        ]
        visible_successful_refs = successful_refs[:5]
        visible_unresolved = unresolved[:5]
        visible_parse_errors = parse_errors[:5]

        fields = {
            "user_id": user_id,
            "session_id": session_id,
            "总数": total,
            "已完成": success_count,
            "未完成": fail_count,
            "parser分布": parser_counts,
            "已完成_file_refs": visible_successful_refs,
            "已完成_file_refs_省略": max(
                0, len(successful_refs) - len(visible_successful_refs)
            ),
            "未解析_file_refs": visible_unresolved,
            "未解析_file_refs_省略": max(0, len(unresolved) - len(visible_unresolved)),
            "未完成原因": visible_parse_errors,
            "未完成原因_省略": max(0, len(parse_errors) - len(visible_parse_errors)),
        }

        if fail_count == 0:
            log_ok("document_parse", **fields)
        elif success_count == 0:
            log_fail("document_parse", "所有文件未完成", **fields)
        else:
            log_fail("document_parse 部分", "部分文件未完成", **fields)


def _invalid_file_ref_reason(file_refs: List[str]) -> Optional[str]:
    for file_ref in file_refs:
        value = str(file_ref).strip()
        if value.startswith("cnt_"):
            return "content_id values must be passed to tool_content_read or evidence_rank, not document_parse."
        if value.startswith(("http://", "https://")):
            return "URLs must be passed to web_fetch, not document_parse."
        if not value:
            return "file_refs must contain non-empty file_ref values."
    return None


def _display_file_ref(file_ref: str) -> str:
    name = Path(file_ref).name
    return name or "document"
