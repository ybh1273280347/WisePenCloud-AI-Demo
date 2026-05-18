import asyncio
import re
from dataclasses import dataclass
from typing import List

from ...models import ExportRequest
from .base import DocumentRenderer

_HEADING_PATTERN = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
_FENCE_PATTERN = re.compile(r"(?m)^(`{3,}|~{3,})")
_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_UNORDERED_LIST_PATTERN = re.compile(r"(?m)^\s*[-*+]\s+")
_ORDERED_LIST_PATTERN = re.compile(r"(?m)^\s*\d+\.\s+")
_STAR_EMPHASIS_PATTERN = re.compile(r"(?m)(\*{1,3})(.+?)\1")
_INLINE_CODE_PATTERN = re.compile(r"`([^`]+)`")
_STRIKETHROUGH_PATTERN = re.compile(r"~~(.+?)~~")


def _make_placeholder(index: int) -> str:
    return f"\x00CODEBLOCK{index}\x00"


@dataclass(frozen=True, slots=True)
class TxtRenderer(DocumentRenderer):
    target_format: str = "txt"

    async def render(self, request: ExportRequest) -> None:
        text = self._markdown_to_text(request.markdown)
        await asyncio.to_thread(
            request.output_path.write_text,
            text,
            encoding="utf-8",
            newline="\n",
        )

    def _markdown_to_text(self, markdown: str) -> str:
        code_blocks: List[str] = []
        text = self._extract_code_blocks(markdown, code_blocks)
        text = self._strip_markdown_syntax(text)
        text = self._restore_code_blocks(text, code_blocks)
        return text.strip() + "\n"

    def _extract_code_blocks(self, markdown: str, code_blocks: List[str]) -> str:
        lines = markdown.split("\n")
        result: List[str] = []
        in_fence = False
        fence_char = ""

        for line in lines:
            if not in_fence:
                m = _FENCE_PATTERN.match(line)
                if m:
                    in_fence = True
                    fence_char = m.group(1)[0]
                    code_blocks.append("")
                    continue
                result.append(line)
            else:
                if (
                    line.strip().startswith(fence_char * 3)
                    and line.strip().strip(fence_char) == ""
                ):
                    in_fence = False
                    fence_char = ""
                    placeholder = _make_placeholder(len(code_blocks) - 1)
                    result.append(placeholder)
                    continue
                idx = len(code_blocks) - 1
                code_blocks[idx] = code_blocks[idx] + line + "\n"

        return "\n".join(result)

    def _strip_markdown_syntax(self, text: str) -> str:
        text = _HEADING_PATTERN.sub("", text)
        text = _IMAGE_PATTERN.sub(r"\1", text)
        text = _LINK_PATTERN.sub(r"\1", text)
        text = _UNORDERED_LIST_PATTERN.sub("", text)
        text = _ORDERED_LIST_PATTERN.sub("", text)
        text = _INLINE_CODE_PATTERN.sub(r"\1", text)
        text = _STAR_EMPHASIS_PATTERN.sub(r"\2", text)
        text = _STRIKETHROUGH_PATTERN.sub(r"\1", text)
        return text

    def _restore_code_blocks(self, text: str, code_blocks: List[str]) -> str:
        for i, block in enumerate(code_blocks):
            placeholder = _make_placeholder(i)
            text = text.replace(placeholder, block.rstrip())
        return text
