from pathlib import Path, PureWindowsPath
from typing import Any, Dict, List, Optional

from chat.application.tools.services.attachment_read import (
    AttachmentReadError,
    AttachmentReadRequest,
    AttachmentReadService,
)
from chat.application.tools.services.attachment_read.formatting import format_attachment_read_result
from chat.domain.interfaces.tool import BaseTool

_TOOL_DESCRIPTION = (
    "Read attachments uploaded by the user in the current conversation.\n\n"
    "Rules:\n"
    "- Input must be attachment_ref values from the current conversation.\n"
    "- Do not pass file paths, file_ref values, or content_id values.\n"
    "- Direct readable text files are cached and returned with ToolContent windows.\n"
    "- Binary documents are not parsed by attachment_read. They are returned as file_ref handoffs for document_parse.\n"
    "- Images are always OCR-processed first.\n"
    "- OCR text is only extracted text from the image. It does not replace visual analysis by the model.\n"
    "- OCR failure does not block the tool result. If image_ref is returned, the model should inspect the image directly when visual analysis is needed.\n"
    "- Long text and OCR output may return content_id values; use tool_content_read to continue reading.\n"
    "- Multiple attachments are processed concurrently."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "attachment_refs": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 10,
            "description": (
                "Attachment references from the current conversation. "
                "Do not pass file paths, file_ref values, or content_id values."
            ),
        },
        "purpose": {
            "type": "string",
            "description": "Optional description of what the user wants to know from the attachments.",
        },
    },
    "required": ["attachment_refs"],
    "additionalProperties": False,
}


class AttachmentReadTool(BaseTool):
    def __init__(self, *, service: AttachmentReadService):
        self._service = service

    @property
    def name(self) -> str:
        return "attachment_read"

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

        attachment_refs: List[str] = kwargs.get("attachment_refs", [])
        if not attachment_refs:
            return "[Tool Error] Missing required attachment_refs parameter."

        invalid_reason = _invalid_attachment_ref_reason(attachment_refs)
        if invalid_reason is not None:
            return f"[Tool Error] Invalid attachment_refs parameter: {invalid_reason}"

        try:
            result = await self._service.read_attachments(
                AttachmentReadRequest(
                    session_id=session_id,
                    user_id=user_id,
                    attachment_refs=attachment_refs,
                    purpose=kwargs.get("purpose"),
                )
            )
            return format_attachment_read_result(result)
        except AttachmentReadError as exc:
            return f"[Tool Error] {exc.message}"
        except NotImplementedError:
            return "[Tool Error] Attachment resolver is not configured."
        except Exception:
            return "[Tool Error] Unexpected error while reading attachments."


def _invalid_attachment_ref_reason(attachment_refs: List[str]) -> Optional[str]:
    for attachment_ref in attachment_refs:
        value = str(attachment_ref).strip()
        if value.startswith("cnt_"):
            return "content_id values must be passed to tool_content_read or evidence_rank, not attachment_read."
        if value.startswith(("http://", "https://")):
            return "URLs must be passed to web_fetch, not attachment_read."
        if (
            Path(value).is_absolute()
            or PureWindowsPath(value).drive
            or "/" in value
            or "\\" in value
        ):
            return "file paths, file_ref values, and download_ref values are not valid attachment_refs."
    return None
