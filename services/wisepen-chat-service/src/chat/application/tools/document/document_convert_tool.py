from typing import Any, Dict, Optional

from chat.application.document_convert import DocumentConvertService
from chat.application.document_convert.errors import DocumentConvertError
from chat.application.document_export.errors import DocumentExportError
from chat.application.tools.document.formatting import format_generated_document_result
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail, log_ok

_TOOL_DESCRIPTION = (
    "Converts a local or cached binary document file referenced by file_ref into a generated file. "
    "The conversion always parses the source document into Markdown first, then exports that Markdown to the requested target format. "
    "Supports output formats: markdown, html, pdf, docx, and txt. "
    "Use this when the user asks to convert an uploaded or cached document file into another downloadable file format. "
    "Does not fetch URLs. "
    "Does not perform direct high-fidelity file-to-file conversion."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "file_ref": {
            "type": "string",
            "minLength": 1,
            "description": "Reference to a local or cached binary document file.",
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

        file_ref: str = kwargs["file_ref"]
        target_format: str = kwargs["target_format"]
        file_name: Optional[str] = kwargs.get("file_name")

        try:
            generated = await self.convert_service.convert_file(
                session_id=session_id,
                file_ref=file_ref,
                target_format=target_format,
                file_name=file_name,
            )

            log_ok(
                "document_convert",
                session_id=session_id,
                file_ref=file_ref,
                target_format=target_format,
                file_name=generated.file_name,
            )

            return format_generated_document_result(
                session_id=session_id, generated=generated
            )

        except DocumentExportError as e:
            return f"[Tool Error] Export failed: {e}"
        except DocumentConvertError as e:
            return f"[Tool Error] {e}"
        except Exception as e:
            log_fail(
                "document_convert",
                e,
                session_id=session_id,
                file_ref=file_ref,
                target_format=target_format,
            )
            return "[Tool Error] Unexpected error while converting document."
