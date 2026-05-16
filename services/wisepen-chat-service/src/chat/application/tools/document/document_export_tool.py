from typing import Any, Dict, Optional

from chat.application.document_export.errors import DocumentExportError
from chat.application.document_export.models import GeneratedDocumentFile
from chat.application.document_export.service import DocumentExportService
from chat.application.tools.document.formatting import format_generated_document_result
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail, log_ok

_TOOL_DESCRIPTION = (
    "Exports Markdown-like text or cached tool content into a generated file. "
    "Supports markdown, html, pdf, docx, and txt. "
    "Use this when the user asks to save, export, or convert content into a downloadable file. "
    "For uploaded binary documents, call document_parse first, then document_export. "
    "Does not parse binary files directly. "
    "Does not fetch URLs. "
    "source_format: markdown means content is interpreted as Markdown; "
    "plain_text means content is treated as plain text (V1: preserved as-is, may still render Markdown syntax)."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "target_format": {
            "type": "string",
            "enum": ["markdown", "html", "pdf", "docx", "txt"],
            "description": "Output file format.",
        },
        "source_format": {
            "type": "string",
            "enum": ["markdown", "plain_text"],
            "description": "Format of the input content. markdown: content is interpreted as Markdown. plain_text: content is treated as plain text.",
        },
        "content": {
            "type": "string",
            "description": "Markdown-like content to export. Use for direct short content.",
        },
        "content_ref": {
            "type": "string",
            "description": "Reference to cached content returned by another tool.",
        },
        "file_name": {
            "type": "string",
            "description": "Optional output file name. Name only, not a path.",
        },
    },
    "required": ["target_format"],
}


class DocumentExportTool(BaseTool):
    def __init__(self, *, export_service: DocumentExportService, content_store):
        self.export_service = export_service
        self.content_store = content_store

    @property
    def name(self) -> str:
        return "document_export"

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

        target_format: str = kwargs["target_format"]
        source_format: str = kwargs.get("source_format", "markdown")
        content_ref: Optional[str] = kwargs.get("content_ref")
        content: Optional[str] = kwargs.get("content")
        file_name: Optional[str] = kwargs.get("file_name")

        try:
            if content_ref:
                stored = self.content_store.get(
                    session_id=session_id,
                    content_id=content_ref,
                )
                if stored is None:
                    return "[Tool Error] Cached content not found, expired, or inaccessible."
                markdown = stored.text
            else:
                markdown = content

            if not markdown or not markdown.strip():
                return "[Tool Error] Missing content or content_ref."

            generated: GeneratedDocumentFile = await self.export_service.export_content(
                session_id=session_id,
                content=markdown,
                target_format=target_format,
                source_format=source_format,
                file_name=file_name,
            )

            log_ok(
                "document_export",
                session_id=session_id,
                target_format=target_format,
                file_name=generated.file_name,
            )

            return format_generated_document_result(
                session_id=session_id, generated=generated
            )

        except DocumentExportError as e:
            return f"[Tool Error] {e}"
        except Exception as e:
            log_fail(
                "document_export",
                e,
                session_id=session_id,
                target_format=target_format,
                content_ref=content_ref,
            )
            return "[Tool Error] Unexpected error while exporting document."
