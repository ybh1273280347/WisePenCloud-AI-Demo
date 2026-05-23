from typing import Any, Dict, Optional

from chat.application.tools.document.formatting import format_generated_document_result
from chat.application.tools.services.document_convert import DocumentConvertService
from chat.application.tools.services.document_convert.errors import DocumentConvertError
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail, log_ok

_TOOL_DESCRIPTION = (
    "Converts a server-side temporary document file_ref into a generated downloadable file. "
    "Use this when the user wants an already-ingested document file converted into markdown, "
    "html, pdf, docx, or txt.\n\n"
    "The input MUST be a file_ref value, not a content_id.\n"
    "Markdown, plain text, and HTML sources are read directly before export.\n"
    "Binary documents are parsed by the document conversion service before export."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "file_ref": {
            "type": "string",
            "minLength": 1,
            "description": "file_ref value for a server-side temporary document. Do not pass content_id values.",
        },
        "target_format": {
            "type": "string",
            "enum": ["markdown", "html", "pdf", "docx", "txt"],
            "description": "Output file format.",
        },
        "file_name": {
            "type": "string",
            "description": "Optional output file name. Name only; not a path.",
        },
        "title": {
            "type": "string",
            "description": "Optional non-empty document title for generated outputs.",
        },
        "reference_docx_file_ref": {
            "type": "string",
            "description": "Optional server-side temporary .docx file_ref used as a DOCX style reference. Only valid when target_format is docx.",
        },
    },
    "required": ["file_ref", "target_format"],
    "additionalProperties": False,
}


class DocumentConvertTool(BaseTool):
    def __init__(self, *, convert_service: DocumentConvertService):
        self.convert_service = convert_service

    @property
    def name(self) -> str:
        return "document_convert"

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

        file_ref: Optional[str] = kwargs.get("file_ref")
        if not isinstance(file_ref, str) or not file_ref.strip():
            return "[Tool Error] Missing required file_ref parameter."

        target_format: str = kwargs["target_format"]
        file_name: Optional[str] = kwargs.get("file_name")
        title: Optional[str] = kwargs.get("title")
        reference_docx_file_ref: Optional[str] = kwargs.get("reference_docx_file_ref")

        try:
            generated = await self.convert_service.convert_document(
                user_id=user_id,
                session_id=session_id,
                file_ref=file_ref,
                target_format=target_format,
                file_name=file_name,
                title=title,
                reference_docx_file_ref=reference_docx_file_ref,
            )

            log_ok(
                "document_convert",
                user_id=user_id,
                session_id=session_id,
                target_format=target_format,
                file_name=generated.file_name,
            )

            return format_generated_document_result(
                user_id=user_id,
                session_id=session_id,
                generated=generated,
            )

        except DocumentConvertError as exc:
            return f"[Tool Error] {exc}"
        except Exception as exc:
            log_fail(
                "document_convert",
                exc,
                user_id=user_id,
                session_id=session_id,
                target_format=target_format,
            )
            return "[Tool Error] Unexpected error while converting document."
