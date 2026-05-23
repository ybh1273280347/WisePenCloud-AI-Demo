import asyncio
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from markdown_it import MarkdownIt
from markdown_it.token import Token

from ...models import ExportRequest
from .base import DocumentRenderer


@dataclass(frozen=True, slots=True)
class TxtRenderer(DocumentRenderer):
    target_format: str = "txt"
    md: MarkdownIt = field(init=False)

    def __post_init__(self) -> None:
        md = MarkdownIt("commonmark", {"html": False, "linkify": True})
        md.enable("table")
        md.enable("strikethrough")
        object.__setattr__(self, "md", md)

    async def render(self, request: ExportRequest) -> None:
        text = self._markdown_to_text(request.markdown)
        await asyncio.to_thread(
            request.output_path.write_text,
            text,
            encoding="utf-8",
            newline="\n",
        )

    def _markdown_to_text(self, markdown: str) -> str:
        tokens = self.md.parse(markdown)
        lines = self._tokens_to_lines(tokens)
        text = "\n".join(self._compact_lines(lines)).strip()
        return text + "\n" if text else "\n"

    def _tokens_to_lines(self, tokens: Sequence[Token]) -> List[str]:
        lines: List[str] = []
        in_table = False
        table_rows: List[List[str]] = []
        current_row: Optional[List[str]] = None
        current_cell_parts: Optional[List[str]] = None
        pending_list_prefix: Optional[str] = None

        for token in tokens:
            if token.type == "table_open":
                in_table = True
                table_rows = []
                continue

            if token.type == "table_close":
                for row in table_rows:
                    if row:
                        lines.append(" | ".join(row))
                lines.append("")
                in_table = False
                continue

            if token.type == "tr_open":
                current_row = []
                continue

            if token.type == "tr_close":
                if current_row is not None:
                    table_rows.append(current_row)
                current_row = None
                continue

            if token.type in ("th_open", "td_open"):
                current_cell_parts = []
                continue

            if token.type in ("th_close", "td_close"):
                if current_row is not None and current_cell_parts is not None:
                    current_row.append(self._normalize_inline_text(current_cell_parts))
                current_cell_parts = None
                continue

            if token.type in ("fence", "code_block"):
                lines.extend(token.content.rstrip("\n").split("\n"))
                lines.append("")
                continue

            if token.type == "list_item_open":
                pending_list_prefix = "- "
                continue

            if token.type in ("bullet_list_close", "ordered_list_close", "blockquote_close"):
                lines.append("")
                continue

            if token.type == "inline":
                text = self._inline_tokens_to_text(token.children or [])
                if in_table and current_cell_parts is not None:
                    current_cell_parts.append(text)
                    continue
                if text.strip():
                    if pending_list_prefix is not None:
                        lines.append(pending_list_prefix + text.strip())
                        pending_list_prefix = None
                    else:
                        lines.extend(text.split("\n"))
                continue

            if token.type in ("paragraph_close", "heading_close"):
                lines.append("")

        return lines

    def _inline_tokens_to_text(self, tokens: Sequence[Token]) -> str:
        parts: List[str] = []
        for token in tokens:
            if token.type in ("text", "code_inline"):
                parts.append(token.content)
            elif token.type in ("softbreak", "hardbreak"):
                parts.append("\n")
            elif token.type == "image":
                image_text = self._inline_tokens_to_text(token.children or [])
                parts.append(image_text or token.content)
            elif token.children:
                parts.append(self._inline_tokens_to_text(token.children))
        return "".join(parts)

    def _normalize_inline_text(self, parts: Sequence[str]) -> str:
        return " ".join("".join(parts).split())

    def _compact_lines(self, lines: Sequence[str]) -> List[str]:
        compacted: List[str] = []
        previous_blank = True
        for line in lines:
            cleaned = line.rstrip()
            is_blank = cleaned == ""
            if is_blank and previous_blank:
                continue
            compacted.append(cleaned)
            previous_blank = is_blank
        while compacted and compacted[-1] == "":
            compacted.pop()
        return compacted
