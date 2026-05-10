import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from chat.application.tool_content_store import cache_and_format
from chat.application.web_fetch import FetchCoordinator, FetchedDocument
from chat.application.web_fetch.utils import UrlSecurityError
from chat.core.config.app_settings import settings
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail, log_ok


_HANDOFF_DIR = Path(".wisepen-web-fetch-documents")
_SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")

_TOOL_DESCRIPTION = (
    "Fetches a web URL and extracts readable Markdown from HTML pages. "
    "For direct file links, this tool only performs binary/file handoff; "
    "document content parsing belongs to document_parse."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "minLength": 1,
            "description": "The URL to fetch. Must start with http:// or https://.",
        },
    },
    "required": ["url"],
}


class WebFetchTool(BaseTool):
    """网页抓取工具入口。"""

    def __init__(self, fetcher: FetchCoordinator):
        self._fetcher = fetcher

    @property
    def name(self) -> str:
        return "web_fetch"

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

        url: str = kwargs["url"]

        log_ok("网页抓取参数", url=url, session_id=session_id)

        try:
            fetched = await self._fetcher.fetch(url)
        except UrlSecurityError as e:
            return f"[Tool Error] URL rejected by security policy: {e}"
        except Exception as e:
            log_fail("网页抓取工具", e, session_id=session_id, url=url)
            return "[Tool Error] Unexpected error while fetching web page content."

        if fetched is None:
            return "[Tool Result] Failed to fetch web page content (all fetch methods exhausted)"

        if isinstance(fetched, FetchedDocument):
            return self._format_document_handoff(
                session_id=session_id,
                document=fetched,
            )

        markdown = fetched.strip()
        if not markdown:
            return "[Tool Result] Failed to fetch web page content (empty content returned)"

        return cache_and_format(
            session_id=session_id,
            tool_name=self.name,
            source=url,
            text=markdown,
            content_type="text/markdown",
            metadata={"content_kind": "web_page"},
            limit=settings.TOOL_RESULT_MAX_CHARS,
        )

    def _format_document_handoff(
        self,
        *,
        session_id: str,
        document: FetchedDocument,
    ) -> str:
        file_ref = self._write_handoff_file(session_id=session_id, document=document)

        return "\n".join(
            [
                "[Tool Result] Downloaded a document file. Web Fetch does not parse document content.",
                f"file_ref: {file_ref}",
                f"source_url: {document.url}",
                f"filename: {document.filename}",
                f"content_type: {document.media_type}",
                f"size_bytes: {len(document.content)}",
                "next_step: Use document_parse with this file_ref to parse the document.",
            ]
        )

    def _write_handoff_file(
        self,
        *,
        session_id: str,
        document: FetchedDocument,
    ) -> str:
        safe_session_id = _safe_filename(session_id, default="session")
        safe_filename = _safe_filename(document.filename, default="document.bin")
        target_dir = _HANDOFF_DIR / safe_session_id
        target_dir.mkdir(parents=True, exist_ok=True)

        target = target_dir / f"{uuid.uuid4().hex[:16]}-{safe_filename}"
        target.write_bytes(document.content)

        file_ref = str(target.resolve(strict=False))

        log_ok(
            "文档直链缓存",
            file_ref=file_ref,
            source_url=document.url,
            size=len(document.content),
            content_type=document.media_type,
        )
        return file_ref


def _safe_filename(value: str, *, default: str) -> str:
    name = Path(value.strip()).name
    name = _SAFE_FILENAME_PATTERN.sub("_", name).strip("._")
    return name or default
