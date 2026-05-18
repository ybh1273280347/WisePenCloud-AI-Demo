import asyncio
import html
import time
from dataclasses import dataclass, field
from typing import Optional

from common.logger import log_event
from markdown_it import MarkdownIt

from ...models import ExportRequest
from .base import DocumentRenderer

DEFAULT_DOCUMENT_CSS = """
body {
  color: #202124;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
  font-size: 14px;
  line-height: 1.6;
  margin: 32px auto;
  max-width: 860px;
  padding: 0 24px;
}
pre {
  background: #f6f8fa;
  border: 1px solid #d0d7de;
  border-radius: 6px;
  padding: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
table {
  border-collapse: collapse;
  width: 100%;
}
th, td {
  border: 1px solid #d0d7de;
  padding: 6px 8px;
  text-align: left;
}
blockquote {
  border-left: 4px solid #d0d7de;
  color: #4b5563;
  padding-left: 12px;
}
img {
  max-width: 100%;
}
""".strip()


@dataclass(frozen=True, slots=True)
class HtmlRenderer(DocumentRenderer):
    css: str = DEFAULT_DOCUMENT_CSS
    target_format: str = "html"
    md: MarkdownIt = field(init=False)

    def __post_init__(self) -> None:
        md = MarkdownIt("commonmark", {"html": False, "linkify": True})
        md.enable("table")
        md.enable("strikethrough")
        object.__setattr__(self, "md", md)

    async def render(self, request: ExportRequest) -> None:
        started = time.monotonic()
        success = False
        try:
            html_content = self.render_to_string(
                request.markdown, title=request.options.title
            )
            await asyncio.to_thread(
                request.output_path.write_text,
                html_content,
                encoding="utf-8",
                newline="\n",
            )
            success = True
        finally:
            log_event(
                "tool_perf",
                tool_name="document_export",
                stage="render_html",
                elapsed_ms=int((time.monotonic() - started) * 1000),
                success=success,
                cache_hit=False,
                fallback_used=False,
            )

    def render_to_string(self, markdown: str, *, title: Optional[str] = None) -> str:
        body = self.md.render(markdown)
        escaped_title = html.escape(title or "Document")

        return (
            "<!doctype html>\n"
            "<html>\n"
            "<head>\n"
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{escaped_title}</title>\n"
            f"<style>{self.css}</style>\n"
            "</head>\n"
            "<body><main>\n"
            f"{body}\n"
            "</main></body>\n"
            "</html>\n"
        )
