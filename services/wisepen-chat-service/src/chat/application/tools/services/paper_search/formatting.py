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
        doi = result.external_ids.get("doi")
        arxiv_id = result.external_ids.get("arxiv")
        lines.extend(
            [
                f"[{index}]",
                f"- title: {optional(result.title)}",
                f"- authors: {optional(result.authors[:8])}",
                f"- year: {optional(result.year)}",
                f"- venue: {optional(result.venue)}",
                f"- publisher: {optional(result.publisher)}",
                f"- doi: {optional(doi)}",
                f"- arxiv_id: {optional(arxiv_id)}",
                f"- url: {optional(result.url)}",
                f"- pdf_url: {optional(result.pdf_url)}",
                f"- result_type: {result.result_type.value}",
                f"- resource_type: {result.resource_type.value}",
                f"- hydration_status: {result.hydration_status.value}",
                f"- preferred_version: {optional(result.preferred_version)}",
                f"- authoritative_version: {optional(result.authoritative_version)}",
                f"- publication_date: {optional(result.publication_date)}",
                f"- evidence_sources: {optional(result.evidence_sources)}",
                f"- source_urls: {optional(result.source_urls)}",
                f"- metadata_confidence: {result.metadata_confidence:.2f}",
                f"- source_confidence: {result.source_confidence:.2f}",
                f"- relevance_score: {result.relevance_score:.3f}",
                f"- abstract_source: {optional(result.abstract_source)}",
                f"- abstract: {preview_text(result.abstract)}",
                "",
            ]
        )

    lines.extend(
        [
            "Assistant instructions:",
            "- Report source coverage exactly, including skipped or failed sources.",
            "- Treat exa_highlights as extracted evidence, not the original abstract.",
            "- Use hydration_status to describe whether structured metadata was found.",
            "- Avoid overstating source confidence or publication status.",
        ]
    )
    if "exa" in response.failed_sources:
        lines.extend(
            [
                "- Exa failed as the paper discovery source. Call web_search as a recall fallback before answering.",
                "- Tell the user clearly that Exa discovery is unavailable and that web_search fallback results may have lower scholarly recall quality.",
            ]
        )
    return "\n".join(lines)


def _list_or_none(values: list[str]) -> list[str]:
    return [f"- {value}" for value in values] if values else ["- none"]


def truncate_result(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...[truncated]"
