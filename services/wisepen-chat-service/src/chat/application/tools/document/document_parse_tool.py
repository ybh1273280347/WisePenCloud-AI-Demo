from typing import Any, Dict, List, Optional

from chat.application.infra.document_temp_files.errors import (
    InvalidDocumentRefError,
    UnreadableDocumentRefError,
)
from chat.application.infra.document_temp_files.processing_scope import document_processing_scope
from chat.application.infra.document_temp_files.resolver import DocumentTempFileResolver
from chat.application.tools.document.services.document_parse import (
    DocumentParseService,
)
from chat.application.tools.document.services.document_parse.models import DocumentParseResultItem
from chat.application.tools.tool_content_store import cache_and_format
from chat.core.config.app_settings import settings as app_settings
from chat.domain.interfaces.tool import BaseTool

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
    "When document_parse returns content_id, the parsed document is available as "
    "cached ToolContent. Use evidence_rank to locate relevant passages, or "
    "tool_content_read to continue a known window by next_offset.\n"
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
    """表示当前组件。"""
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
        """执行工具入口流程。"""
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."
        user_id: Optional[str] = context.get("user_id")
        if not user_id:
            return "[Tool Error] Missing user_id in execution context."

        file_refs: List[str] = kwargs["file_refs"]

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
            display_name = ref.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or "document"
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
                lines.append("")
                display_name = (
                    item.file_ref.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or "document"
                )
                lines.append(f"--- File: {display_name} ---")
                cached = cache_and_format(
                    session_id=session_id,
                    tool_name=self.name,
                    source=result.source,
                    text=result.text,
                    content_type="text/markdown",
                    metadata=metadata,
                    limit=app_settings.TOOL_RESULT_MAX_CHARS,
                )
                lines.append(cached)
            else:
                lines.append("")
                display_name = (
                    item.file_ref.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or "document"
                )
                lines.append(f"--- File: {display_name} ---")
                lines.append(f"[Parse Error] {item.error}")

        return "\n".join(lines)
