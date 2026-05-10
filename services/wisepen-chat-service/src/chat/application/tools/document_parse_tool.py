from typing import Any, Dict, Optional

from chat.application.tool_content_store import cache_and_format
from chat.core.config.app_settings import settings
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail


_TOOL_DESCRIPTION = (
    "Parses binary documents into Markdown text and structured tables. "
    "Supports PDF, DOCX, PPTX, EPUB, XLSX, XLS, and ODS. "
    "Does not fetch URLs. "
    "Does not handle HTML, TXT, MD, CSV, JSON, XML, images, audio, or video."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "file_ref": {
            "type": "string",
            "minLength": 1,
            "description": "Reference to a local or cached binary document file.",
        },
    },
    "required": ["file_ref"],
}


class DocumentParseTool(BaseTool):
    def __init__(self, *, parse_service: Any, file_resolver: Any):
        self.parse_service = parse_service
        self.file_resolver = file_resolver

    @property
    def name(self) -> str:
        return "document_parse"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        file_ref: str = kwargs["file_ref"]

        try:
            resolved = self.file_resolver.resolve(file_ref)
            result = await self.parse_service.parse_path(resolved.local_path)
        except FileNotFoundError as e:
            return f"[Tool Error] {e}"
        except ValueError as e:
            return f"[Tool Error] {e}"
        except RuntimeError as e:
            return f"[Tool Error] {e}"
        except Exception as e:
            log_fail("文档解析工具", e, session_id=session_id, file_ref=file_ref)
            return "[Tool Error] Unexpected error while parsing document content."

        metadata: Dict[str, Any] = {
            **result.metadata,
            "tool": self.name,
            "file_type": result.file_type,
            "source": result.source,
            "page_count": len(result.pages),
            "table_count": len(result.tables),
            "warnings": result.warnings,
        }

        return cache_and_format(
            session_id=session_id,
            tool_name=self.name,
            source=result.source,
            text=result.text,
            content_type="text/markdown",
            metadata=metadata,
            limit=settings.TOOL_RESULT_MAX_CHARS,
        )