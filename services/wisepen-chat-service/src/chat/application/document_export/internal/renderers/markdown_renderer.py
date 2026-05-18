import asyncio
import time
from dataclasses import dataclass

from common.logger import log_event

from ...models import ExportRequest
from .base import DocumentRenderer


@dataclass(frozen=True, slots=True)
class MarkdownRenderer(DocumentRenderer):
    target_format: str = "markdown"

    async def render(self, request: ExportRequest) -> None:
        started = time.monotonic()
        success = False
        try:
            await asyncio.to_thread(
                request.output_path.write_text,
                request.markdown,
                encoding="utf-8",
                newline="\n",
            )
            success = True
        finally:
            log_event(
                "tool_perf",
                tool_name="document_export",
                stage="render_markdown",
                elapsed_ms=int((time.monotonic() - started) * 1000),
                success=success,
                cache_hit=False,
                fallback_used=False,
            )
