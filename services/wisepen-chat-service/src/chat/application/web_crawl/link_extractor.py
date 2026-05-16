from __future__ import annotations

import re
from typing import List, Tuple

from chat.application.web_crawl.models import ExtractedLink


_MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]{1,200})\]\(([^)\s]+)\)")
_BARE_URL_RE = re.compile(r"https?://[^\s<>)\]]+")


def extract_markdown_links(markdown: str, *, context_chars: int = 240) -> List[ExtractedLink]:
    links: List[ExtractedLink] = []
    occupied_ranges: List[Tuple[int, int]] = []

    for match in _MARKDOWN_LINK_RE.finditer(markdown or ""):
        anchor = match.group(1).strip()
        url = match.group(2).strip()
        context = _extract_context(markdown, match.start(), match.end(), context_chars)
        links.append(
            ExtractedLink(
                url=url,
                anchor_text=anchor,
                surrounding_text=context,
            )
        )
        occupied_ranges.append(match.span())

    for match in _BARE_URL_RE.finditer(markdown or ""):
        if _inside_any_range(match.start(), occupied_ranges):
            continue

        url = match.group(0).rstrip(".,;:")
        context = _extract_context(markdown, match.start(), match.end(), context_chars)
        links.append(
            ExtractedLink(
                url=url,
                anchor_text="",
                surrounding_text=context,
            )
        )

    return links


def extract_markdown_title(markdown: str) -> str:
    match = re.search(r"(?m)^#{1,2}\s+(.+?)\s*$", markdown or "")
    if match:
        return match.group(1).strip()
    return ""


def _extract_context(text: str, start: int, end: int, context_chars: int) -> str:
    left = max(0, start - context_chars)
    right = min(len(text), end + context_chars)
    context = text[left:right]
    return re.sub(r"\s+", " ", context).strip()


def _inside_any_range(index: int, ranges: List[Tuple[int, int]]) -> bool:
    return any(start <= index < end for start, end in ranges)

