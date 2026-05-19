from __future__ import annotations

from chat.application.tools.services.evidence_ranking.models import (
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

    for display_index, ev in enumerate(result.evidence, 1):
        lines.append(f"\n[{display_index}]")
        lines.append(f"   Raw rank: {ev.rank + 1}")
        lines.append(f"   Title: {ev.display_title}")
        if ev.source_id:
            lines.append(f"   source_id: {ev.source_id}")
        if ev.domain:
            lines.append(f"   Domain: {ev.domain}")
        if ev.url:
            lines.append(f"   URL: {ev.url}")
        lines.append(f"   content_id: {ev.content_id}")
        if ev.chunk_index >= 0:
            lines.append(f"   chunk_index: {ev.chunk_index}")
            lines.append(f"   start_offset: {ev.start_offset}")
            lines.append(f"   end_offset: {ev.end_offset}")
        if ev.evidence_type:
            lines.append(f"   Evidence type: {ev.evidence_type}")
        lines.append(f"   Score: {ev.score:.4f}")
        if ev.term_hit_stats:
            lines.append("   Term hit stats:")
            for term_stat in ev.term_hit_stats:
                field_parts = [
                    f"{field_stat.field}={field_stat.count}"
                    for field_stat in term_stat.field_stats
                ]
                field_text = ", ".join(field_parts)
                if field_text:
                    lines.append(
                        f"      - {term_stat.term}: total={term_stat.total_count}; {field_text}"
                    )
                else:
                    lines.append(
                        f"      - {term_stat.term}: total={term_stat.total_count}"
                    )
        if ev.matched_reason:
            lines.append(f"   Matched reason: {ev.matched_reason}")
        if ev.excerpt:
            lines.append("   Excerpt:")
            for excerpt_line in ev.excerpt.split("\n"):
                lines.append(f"      {excerpt_line}")

    lines.append("")
    has_web_search_result = any(
        ev.evidence_type == "web_search_result" for ev in result.evidence
    )
    has_chunk_evidence = any(ev.chunk_index >= 0 for ev in result.evidence)
    if has_web_search_result:
        lines.append(
            "These ranked items are search-result snippets, not fetched page bodies. "
            "For technical details, direct quotes, conflict resolution, or high-confidence evidence, "
            "call web_fetch with the selected URLs in one batch."
        )
    if has_chunk_evidence:
        chunk_evidence = [ev for ev in result.evidence if ev.chunk_index >= 0]
        lines.append(
            "To inspect surrounding content for chunk evidence, call "
            "tool_content_read with content_id, chunk_index, before_chunks=1, "
            "and after_chunks=1. Example:"
        )
        first_chunk = chunk_evidence[0]
        lines.append(
            'tool_content_read({"content_id": "'
            f"{first_chunk.content_id}"
            '", "chunk_index": '
            f"{first_chunk.chunk_index}"
            ', "before_chunks": 1, "after_chunks": 1})'
        )
        if len(chunk_evidence) >= 2:
            examples = chunk_evidence[: min(3, len(chunk_evidence))]
            lines.append(
                "Batch expand example for related chunk evidence. Use this only when these "
                "chunk evidence items are thematically related and need to be inspected together:"
            )
            lines.append('tool_content_batch_read({"items": [')
            for index, ev in enumerate(examples):
                suffix = "," if index < len(examples) - 1 else ""
                lines.append(
                    '  {"content_id": "'
                    f"{ev.content_id}"
                    '", "chunk_index": '
                    f"{ev.chunk_index}"
                    ', "before_chunks": 1, "after_chunks": 1}'
                    f"{suffix}"
                )
            lines.append('], "max_total_chars": 12000})')
    if not has_web_search_result and not has_chunk_evidence:
        lines.append(
            "To inspect surrounding content, call tool_content_read with "
            "content_id and offset."
        )

    if result.notes:
        lines.append("Notes:")
        for note in result.notes:
            lines.append(f"- {note}")

    return "\n".join(lines)
