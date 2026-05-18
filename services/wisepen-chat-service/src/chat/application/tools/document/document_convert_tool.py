from typing import Any, Dict, Optional

from chat.application.document_export import DocumentExportError
from chat.application.tools.document.formatting import format_generated_document_result
from chat.application.tools.services.document_convert import DocumentConvertService
from chat.application.tools.services.document_convert.errors import DocumentConvertError
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail, log_ok

_TOOL_DESCRIPTION = (
    "Converts a server-side temporary document file_ref into a generated downloadable file. "
    "The source must already be an ingested server temporary file_ref. Markdown/plain text/HTML "
    "are read directly and exported; binary documents are parsed by the existing document_parse "
    "strategy before export. Supports output formats: markdown, html, pdf, docx, and txt."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "file_ref": {
            "type": "string",
            "minLength": 1,
            "description": "Server-side temporary document file path issued by the system.",
        },
        "target_format": {
            "type": "string",
            "enum": ["markdown", "html", "pdf", "docx", "txt"],
            "description": "Output file format.",
        },
        "file_name": {
            "type": "string",
            "description": "Optional output file name. Name only, not a path.",
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

        try:
            generated = await self.convert_service.convert_document(
                user_id=user_id,
                session_id=session_id,
                file_ref=file_ref,
                target_format=target_format,
                file_name=file_name,
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

        except DocumentExportError as exc:
            return f"[Tool Error] Export failed: {exc}"
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
