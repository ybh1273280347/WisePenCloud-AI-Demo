from __future__ import annotations

from chat.application.evidence_ranking.models import (
    EvidenceRankResult,
)


def format_evidence_result(result: EvidenceRankResult) -> str:
    lines = ["[Tool Result] Ranked Evidence"]

    lines.append(f"Query: {result.query}")
    lines.append(
        f"Scanned {result.total_chunks_scanned} chunks from "
        f"{len(result.content_ids_found)} content source(s)."
    )

    if result.content_ids_missing:
        lines.append(
            f"Missing/expired content_ids: {', '.join(result.content_ids_missing)}"
        )

    if not result.evidence:
        lines.append("\nNo relevant evidence found for the given query.")
        if result.notes:
            lines.append("Notes:")
            for note in result.notes:
                lines.append(f"- {note}")
        return "\n".join(lines)

    lines.append(f"\nRanked Evidence ({len(result.evidence)} snippet(s)):")

    for ev in result.evidence:
        lines.append(f"\n[{ev.rank + 1}]")
        lines.append(f"   Title: {ev.display_title}")
        if ev.url:
            lines.append(f"   URL: {ev.url}")
        lines.append(f"   content_id: {ev.content_id}")
        lines.append(f"   chunk_index: {ev.chunk_index}")
        lines.append(f"   Score: {ev.score:.4f}")
        if ev.excerpt:
            lines.append(f"   Excerpt:")
            for excerpt_line in ev.excerpt.split("\n"):
                lines.append(f"      {excerpt_line}")

    lines.append("")
    lines.append(
        "To inspect surrounding content, call tool_content_read with "
        "content_id and offset."
    )

    if result.notes:
        lines.append("Notes:")
        for note in result.notes:
            lines.append(f"- {note}")

    return "\n".join(lines)
