from __future__ import annotations

import re
from html import unescape
from typing import Iterable, List, Tuple
from urllib.parse import unquote, urljoin

from chat.application.tools.services.web_crawl.models import ExtractedLink
from chat.application.tools.services.web_fetch.models import FetchedLink


_MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]{1,200})\]\(([^)\s]+)\)")
_BARE_URL_RE = re.compile(r"https?://[^\s<>)\]]+")
_HTML_HREF_RE = re.compile(
    r"""<a\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'<>`]+))[^>]*>(.*?)</a\s*>""",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_MAX_EXTRACTED_LINKS = 500


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
                source="markdown_link",
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
                source="bare_url",
            )
        )

    return links


def merge_extracted_links(
    *,
    markdown: str,
    base_url: str,
    fetched_links: Iterable[FetchedLink] | None = None,
) -> List[ExtractedLink]:
    links: List[ExtractedLink] = []
    seen: set[str] = set()

    for link in fetched_links or []:
        _append_unique_link(
            links,
            seen,
            ExtractedLink(
                url=link.url,
                anchor_text=link.anchor_text,
                surrounding_text=link.surrounding_text,
                source="dom",
            ),
            base_url=base_url,
        )

    for link in extract_markdown_links(markdown):
        _append_unique_link(links, seen, link, base_url=base_url)

    for link in extract_html_anchor_links(markdown, base_url=base_url):
        _append_unique_link(links, seen, link, base_url=base_url)

    return links[:_MAX_EXTRACTED_LINKS]


def extract_html_anchor_links(
    html_or_markdown: str,
    *,
    base_url: str,
    context_chars: int = 240,
) -> List[ExtractedLink]:
    links: List[ExtractedLink] = []
    text = html_or_markdown or ""

    for match in _HTML_HREF_RE.finditer(text):
        href = (match.group(1) or match.group(2) or match.group(3) or "").strip()
        if not href:
            continue

        anchor_html = match.group(4) or ""
        anchor = _clean_inline_html(anchor_html)
        context = _extract_context(text, match.start(), match.end(), context_chars)
        links.append(
            ExtractedLink(
                url=urljoin(base_url, html_unescape_url(href)),
                anchor_text=anchor,
                surrounding_text=_clean_inline_html(context),
                source="html_anchor",
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


def _append_unique_link(
    links: List[ExtractedLink],
    seen: set[str],
    link: ExtractedLink,
    *,
    base_url: str,
) -> None:
    key = _dedupe_key(link.url, base_url=base_url)
    if key in seen:
        return

    seen.add(key)
    links.append(link)


def _dedupe_key(url: str, *, base_url: str) -> str:
    absolute = urljoin(base_url, url.strip())
    absolute = absolute.split("#", 1)[0]
    return unquote(absolute).rstrip("/")


def _clean_inline_html(value: str) -> str:
    stripped = _TAG_RE.sub(" ", value or "")
    return re.sub(r"\s+", " ", unescape(stripped)).strip()


def html_unescape_url(value: str) -> str:
    return unescape(value or "").strip()

