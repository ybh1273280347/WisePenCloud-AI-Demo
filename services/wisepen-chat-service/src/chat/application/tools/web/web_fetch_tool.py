from pathlib import Path
from typing import Any, Dict, List, Optional

from chat.application.tools.common.file_handoff import TemporaryFileHandoffStore
from chat.application.tools.common.security.references import reject_non_url_reference
from chat.application.tools.common.tool_content_store import cache_and_format
from chat.application.tools.config import TOOL_RESULT_MAX_CHARS
from chat.application.tools.services.web_fetch import (
    FetchCoordinator,
    FetchedDocument,
    FetchResultItem,
)
from chat.application.tools.services.web_fetch.utils.url_batching import (
    UrlBatchInputError,
    normalize_urls,
)
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_event

_TOOL_DESCRIPTION = (
    "Fetches one or more web URLs concurrently. Use this tool when the user provides URL(s), "
    "or after web_search returns candidate URLs that need page-body evidence.\n\n"
    "Batching rule: always pass URLs as one urls array. Each array item must be exactly one "
    "http:// or https:// URL. If there are multiple URLs, put all of them in the same urls list. "
    "Do not call web_fetch once per URL, and do not put multiple URLs into one string item. "
    "The backend fetches all URLs in the array concurrently.\n\n"
    "HTML pages return readable Markdown content. Long content may be returned as ToolContent "
    "windows with content_id=cnt_* and next_offset. The full content is split into many chunks; "
    "the first window may not contain the key evidence. After web_fetch returns cached content_ids, "
    "you MUST call evidence_rank with the user's question and the content_ids to score all chunks "
    "by relevance and find the most relevant passages before answering. Do not answer directly "
    "from the first truncated window alone. content_id is not file_ref: use content_id only with "
    "tool_content_read for continuation, or with evidence_rank for relevance-based evidence ranking.\n\n"
    "Direct document links such as PDF, DOCX, PPTX, EPUB, XLSX, XLSM, XLS, or ODS are downloaded "
    "and returned as file_ref handoffs instead of being parsed. After web_fetch returns file_ref "
    "values, pass all file_refs together to document_parse in one call."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "urls": {
            "type": "array",
            "items": {
                "type": "string",
                "minLength": 1,
            },
            "minItems": 1,
            "maxItems": 20,
            "uniqueItems": True,
            "description": (
                "One array of URLs to fetch concurrently. Each item must be exactly one "
                "http:// or https:// URL. Put multiple URLs as separate items in this same array; "
                "do not put newline-separated URLs into one string, and do not make one call per URL."
            ),
        },
    },
    "required": ["urls"],
    "additionalProperties": False,
}


class WebFetchTool(BaseTool):
    """web_fetch tool entrypoint."""

    def __init__(
        self,
        fetcher: FetchCoordinator,
        file_handoff_store: TemporaryFileHandoffStore,
    ):
        self._fetcher = fetcher
        self._file_handoff_store = file_handoff_store

    @property
    def name(self) -> str:
        return "web_fetch"

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

        try:
            urls = normalize_urls(kwargs.get("urls", []))
        except UrlBatchInputError as e:
            return f"[Tool Error] Invalid urls parameter: {e}"

        if not urls:
            return "[Tool Error] Missing required urls parameter."

        for url in urls:
            reference_kind = reject_non_url_reference(url)
            if reference_kind is not None:
                return (
                    f"[Tool Error] Invalid urls parameter: {reference_kind} is not a URL. "
                    "Do not pass internal references to web_fetch."
                )

        invalid_urls = [
            url for url in urls if not url.startswith(("http://", "https://"))
        ]
        if invalid_urls:
            return "[Tool Error] Invalid urls parameter: every urls item must be an http:// or https:// URL."

        log_event(
            "web_fetch normalized urls",
            normalized_url_count=len(urls),
            fetch_many_batch_size=len(urls),
        )

        results: List[FetchResultItem] = await self._fetcher.fetch_many(urls)

        return self._format_batch_result(
            user_id=user_id,
            session_id=session_id,
            results=results,
        )

    def _format_batch_result(
        self,
        *,
        user_id: str,
        session_id: str,
        results: List[FetchResultItem],
    ) -> str:
        lines: List[str] = ["[Tool Result] web_fetch 批量结果"]

        success_count = sum(1 for r in results if r.success)
        fail_count = len(results) - success_count
        lines.append(
            f"Total: {len(results)} URLs，{success_count} 个已完成，{fail_count} 个未完成。"
        )

        for item in results:
            if item.success:
                if item.document is not None:
                    lines.append("")
                    lines.extend(
                        self._format_document_handoff_lines(
                            user_id=user_id,
                            session_id=session_id,
                            document=item.document,
                        )
                    )
                elif item.content is not None:
                    lines.append("")
                    lines.append(f"--- URL: {item.url} ---")
                    cached = cache_and_format(
                        session_id=session_id,
                        tool_name=self.name,
                        source=item.url,
                        text=item.content,
                        content_type="text/markdown",
                        metadata={"content_kind": "web_page"},
                        limit=TOOL_RESULT_MAX_CHARS,
                    )
                    lines.append(cached)
            else:
                lines.append("")
                lines.append(f"--- URL: {item.url} ---")
                lines.append(f"[Fetch Error] {item.error}")

        return "\n".join(lines)

    def _format_document_handoff_lines(
        self,
        *,
        user_id: str,
        session_id: str,
        document: FetchedDocument,
    ) -> List[str]:
        handoff = self._file_handoff_store.write_bytes(
            user_id=user_id,
            session_id=session_id,
            filename=document.filename,
            content=document.content,
            canonical_suffix=Path(document.filename).suffix,
            content_type=document.media_type,
        )
        file_ref = handoff.file_ref

        log_event(
            "web_fetch document handoff cached",
            file_ref=file_ref,
            source_url=document.url,
            size=len(document.content),
            content_type=document.media_type,
        )

        return [
            f"--- URL: {document.url} ---",
            "Downloaded a document file. Web Fetch does not parse document content.",
            f"file_ref: {file_ref}",
            f"source_url: {document.url}",
            f"filename: {document.filename}",
            f"content_type: {document.media_type}",
            f"size_bytes: {len(document.content)}",
            "next_step: Collect every file_ref from this web_fetch batch, inject all of them into "
            "one document_parse file_refs list, and call document_parse once: "
            "document_parse(file_refs=[file_ref_1, file_ref_2, ...]).",
        ]
