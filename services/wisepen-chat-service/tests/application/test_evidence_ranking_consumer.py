from __future__ import annotations

import json

from chat.application.tools.services.evidence_ranking import ranker as ranker_module
from chat.application.tools.services.evidence_ranking.formatter import (
    format_evidence_result,
)
from chat.application.tools.services.evidence_ranking.models import (
    EvidenceRankResult,
    RankedEvidence,
)
from chat.core.content_store.models import ContentChunk, StoredContent


_SESSION_ID = "evidence-ranking-consumer-tests"


class _FakeContentStore:
    def __init__(self, contents: list[StoredContent]) -> None:
        self._contents = {content.content_id: content for content in contents}

    def get(self, *, content_id: str, session_id: str) -> StoredContent | None:
        content = self._contents.get(content_id)
        if content is None or content.scope_id != session_id:
            return None
        return content


def _install_store(monkeypatch, contents: list[StoredContent]) -> None:
    monkeypatch.setattr(
        ranker_module,
        "tool_content_store",
        _FakeContentStore(contents),
    )


def _stored_content(
    content_id: str,
    text: str,
    *,
    chunks: list[ContentChunk] | None = None,
    title: str = "",
    source: str = "source.md",
) -> StoredContent:
    metadata = {"title": title} if title else {}
    return StoredContent(
        content_id=content_id,
        scope_id=_SESSION_ID,
        producer="test",
        source=source,
        content_type="text/markdown",
        text=text,
        chunks=chunks or [],
        metadata=metadata,
    )


def _web_pack(content_id: str, results: list[dict[str, str]]) -> StoredContent:
    return StoredContent(
        content_id=content_id,
        scope_id=_SESSION_ID,
        producer="web_search",
        source="web_search",
        content_type="application/json",
        text=json.dumps({"results": results}),
        metadata={
            "content_kind": "web_search_evidence_pack",
            "title": "web search evidence",
        },
    )


def _chunk(index: int, start: int, end: int, *, heading: str = "") -> ContentChunk:
    metadata = {"heading_path": [heading]} if heading else {}
    return ContentChunk(
        index=index,
        start_offset=start,
        end_offset=end,
        metadata=metadata,
    )


def _result(
    source_id: str,
    title: str,
    domain: str,
    snippet: str,
) -> dict[str, str]:
    return {
        "source_id": source_id,
        "title": title,
        "url": f"https://{domain}/{source_id}",
        "domain": domain,
        "snippet": snippet,
    }


def _notes_text(result: EvidenceRankResult) -> str:
    return "\n".join(result.notes)


def test_rank_evidence_merges_web_and_generic_candidates_before_top_k(monkeypatch):
    generic = _stored_content(
        "generic-high",
        "needle",
        chunks=[_chunk(0, 0, len("needle"), heading="needle")],
        title="needle",
    )
    web = _web_pack(
        "web-pack",
        [
            _result("s1", "needle web", "web.example", "needle snippet"),
            _result("s2", "unrelated", "other.example", "nothing useful"),
            _result("s3", "baseline", "base.example", "background only"),
        ],
    )
    _install_store(monkeypatch, [web, generic])

    result = ranker_module.rank_evidence(
        query="needle",
        content_ids=["web-pack", "generic-high"],
        session_id=_SESSION_ID,
        max_evidence=2,
    )

    assert [ev.content_id for ev in result.evidence][0] == "generic-high"
    assert {ev.evidence_type for ev in result.evidence} == {
        "chunk",
        "web_search_result",
    }
    assert len(result.evidence) == 2
    assert all(ev.score > 0 for ev in result.evidence)


def test_rank_evidence_does_not_return_zero_score_generic_candidates(monkeypatch):
    generic = _stored_content(
        "generic",
        "alpha beta gamma",
        title="ordinary document",
    )
    _install_store(monkeypatch, [generic])

    result = ranker_module.rank_evidence(
        query="needle",
        content_ids=["generic"],
        session_id=_SESSION_ID,
        max_evidence=3,
    )

    assert result.evidence == ()
    assert "No positive lexical match found in ranked candidates." in _notes_text(
        result
    )


def test_rank_evidence_does_not_return_zero_score_web_candidates(monkeypatch):
    web = _web_pack(
        "web-pack",
        [
            _result("s1", "alpha", "alpha.example", "beta gamma"),
        ],
    )
    _install_store(monkeypatch, [web])

    result = ranker_module.rank_evidence(
        query="needle",
        content_ids=["web-pack"],
        session_id=_SESSION_ID,
        max_evidence=3,
    )

    assert result.evidence == ()
    assert "No positive lexical match found in ranked candidates." in _notes_text(
        result
    )


def test_rank_evidence_records_duplicate_chunk_skip_note(monkeypatch):
    part = "needle duplicate body"
    text = f"{part}\n{part}"
    generic = _stored_content(
        "generic",
        text,
        chunks=[
            _chunk(0, 0, len(part)),
            _chunk(1, len(part) + 1, len(text)),
        ],
    )
    _install_store(monkeypatch, [generic])

    result = ranker_module.rank_evidence(
        query="needle",
        content_ids=["generic"],
        session_id=_SESSION_ID,
        max_evidence=5,
    )

    assert len(result.evidence) == 1
    assert "duplicate chunk" in _notes_text(result)


def test_rank_evidence_records_max_chunks_per_content_skip_note(monkeypatch):
    chunks = ["needle one", "needle two", "alpha", "beta", "gamma"]
    text = "\n".join(chunks)
    offsets: list[ContentChunk] = []
    cursor = 0
    for index, chunk_text in enumerate(chunks):
        offsets.append(_chunk(index, cursor, cursor + len(chunk_text)))
        cursor += len(chunk_text) + 1
    generic = _stored_content("generic", text, chunks=offsets)
    _install_store(monkeypatch, [generic])

    result = ranker_module.rank_evidence(
        query="needle",
        content_ids=["generic"],
        session_id=_SESSION_ID,
        max_evidence=5,
        max_chunks_per_content=1,
    )

    assert len(result.evidence) == 1
    assert "max_chunks_per_content=1" in _notes_text(result)


def test_rank_evidence_records_same_domain_skip_note(monkeypatch):
    web = _web_pack(
        "web-pack",
        [
            _result("s1", "needle result one", "same.example", "needle details"),
            _result("s2", "needle result two", "same.example", "needle details"),
            _result("s3", "needle result three", "same.example", "needle details"),
            _result("s4", "alpha", "other1.example", "background"),
            _result("s5", "beta", "other2.example", "background"),
            _result("s6", "gamma", "other3.example", "background"),
            _result("s7", "delta", "other4.example", "background"),
        ],
    )
    _install_store(monkeypatch, [web])

    result = ranker_module.rank_evidence(
        query="needle",
        content_ids=["web-pack"],
        session_id=_SESSION_ID,
        max_evidence=5,
    )

    assert len(result.evidence) == 2
    assert {ev.domain for ev in result.evidence} == {"same.example"}
    assert "same-domain cap" in _notes_text(result)


def test_rank_evidence_excerpt_centers_first_query_term_hit(monkeypatch):
    text = ("alpha " * 120) + "needle " + ("omega " * 120)
    generic = _stored_content("generic", text)
    _install_store(monkeypatch, [generic])

    result = ranker_module.rank_evidence(
        query="needle",
        content_ids=["generic"],
        session_id=_SESSION_ID,
        max_evidence=1,
    )

    assert len(result.evidence) == 1
    excerpt = result.evidence[0].excerpt
    assert excerpt.startswith("...")
    assert "needle" in excerpt
    assert len(excerpt) <= 306


def test_make_excerpt_falls_back_to_prefix_when_query_term_misses():
    text = "alpha " * 120

    excerpt = ranker_module._make_excerpt(text, query_terms=("needle",))

    assert excerpt == " ".join(text.split())[:300] + "..."


def test_rank_evidence_term_hit_stats_cover_generic_and_web_fields(monkeypatch):
    generic = _stored_content(
        "generic",
        "vector body vector",
        chunks=[
            _chunk(0, 0, len("vector body vector"), heading="vector heading"),
        ],
        title="vector title",
    )
    web = _web_pack(
        "web-pack",
        [
            _result(
                "s1",
                "vector.example title",
                "vector.example",
                "snippet about vector.example",
            ),
            _result("s2", "alpha", "alpha.example", "background"),
            _result("s3", "beta", "beta.example", "background"),
        ],
    )
    _install_store(monkeypatch, [generic, web])

    generic_result = ranker_module.rank_evidence(
        query="vector",
        content_ids=["generic"],
        session_id=_SESSION_ID,
        max_evidence=1,
    )
    generic_fields = {
        field_stat.field
        for term_stat in generic_result.evidence[0].term_hit_stats
        for field_stat in term_stat.field_stats
    }

    web_result = ranker_module.rank_evidence(
        query="vector.example",
        content_ids=["web-pack"],
        session_id=_SESSION_ID,
        max_evidence=1,
    )
    web_fields = {
        field_stat.field
        for term_stat in web_result.evidence[0].term_hit_stats
        for field_stat in term_stat.field_stats
    }

    assert generic_fields == {"title", "heading", "body"}
    assert web_fields == {"title", "domain", "snippet"}


def test_formatter_does_not_describe_score_as_probability_or_confidence():
    result = EvidenceRankResult(
        query="needle",
        evidence=(
            RankedEvidence(
                content_id="web-pack",
                chunk_index=-1,
                score=1.25,
                rank=0,
                title="Needle",
                url="https://example.com/needle",
                evidence_type="web_search_result",
            ),
        ),
        total_chunks_scanned=1,
        content_ids_found=("web-pack",),
    )

    formatted = format_evidence_result(result).lower()

    assert "probability" not in formatted
    assert "confidence" not in formatted


def test_evidence_rank_public_models_remain_backward_compatible():
    evidence = RankedEvidence(
        content_id="content",
        chunk_index=0,
        score=1.0,
        rank=0,
    )
    result = EvidenceRankResult(query="needle", evidence=(evidence,))

    assert evidence.display_title == "(untitled)"
    assert evidence.term_hit_stats == ()
    assert result.evidence == (evidence,)
