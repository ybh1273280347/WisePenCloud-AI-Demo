from __future__ import annotations

from typing import Any, Optional

from .models import PaperSearchResponse


def optional(value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "none"
    return str(value)


def preview_text(value: Optional[str], *, max_chars: int = 700) -> str:
    if not value:
        return "unknown"
    text = " ".join(value.split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "...[truncated]"


def format_paper_search_response(response: PaperSearchResponse) -> str:
    lines = [
        "[Tool Result] paper_search",
        "",
        f"Query: {response.query}",
        "",
        "Sources searched:",
    ]
    lines.extend(_list_or_none(response.searched_sources))
    lines.extend(["", "Sources skipped:"])
    lines.extend(_list_or_none(response.skipped_sources))
    lines.extend(["", "Sources failed:"])
    lines.extend(_list_or_none(response.failed_sources))
    lines.extend(["", "Warnings:"])
    lines.extend(_list_or_none(response.warnings))
    lines.extend(["", "Results:"])

    if not response.results:
        lines.append("- none")

    for index, result in enumerate(response.results, 1):
        lines.extend(
            [
                f"[{index}]",
                f"- title: {optional(result.title)}",
                f"- authors: {optional(result.authors[:8])}",
                f"- year: {optional(result.year)}",
                f"- venue: {optional(result.venue)}",
                f"- doi: {optional(result.doi)}",
                f"- arxiv_id: {optional(result.arxiv_id)}",
                f"- url: {optional(result.url)}",
                f"- pdf_url: {optional(result.pdf_url)}",
                f"- source_names: {optional(result.source_names)}",
                f"- source_urls: {optional(result.source_urls)}",
                f"- result_type: {optional(result.result_type)}",
                f"- publication_date: {optional(result.publication_date)}",
                f"- is_open_access: {optional(result.is_open_access)}",
                f"- abstract: {preview_text(result.abstract)}",
                "",
            ]
        )

    lines.extend(
        [
            "Assistant instructions:",
            "- Prefer DOI-backed and publisher-backed records from Crossref or DataCite when available.",
            "- Treat arXiv results as preprints unless there is DOI or publisher metadata.",
            "- If one source failed, was skipped, or was rate-limited, do not claim that no papers exist.",
            "- Summarize source coverage and warnings.",
            "- If the user asks for full text, prefer OA links from Unpaywall or arXiv PDF when available.",
            "- If no result is found and the original user query was not English, retry once with concise English academic keywords.",
        ]
    )
    return "\n".join(lines)


def _list_or_none(values: list[str]) -> list[str]:
    return [f"- {value}" for value in values] if values else ["- none"]


def truncate_result(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...[truncated]"
