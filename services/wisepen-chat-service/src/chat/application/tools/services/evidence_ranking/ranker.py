from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePath
from typing import Dict, List, Optional, Set, Tuple

from chat.application.algorithms.ranking import (
    FieldedDocument,
    score_fielded_bm25,
    tokenize_for_bm25,
)
from chat.application.tools.services.evidence_ranking.models import (
    EvidenceFieldHitStat,
    EvidenceRankResult,
    EvidenceTermHitStat,
    RankedEvidence,
)
from chat.application.tools.common.tool_content_store import tool_content_store
from chat.application.web_search.utils.notes import add_note
from chat.core.content_store.models import ContentChunk, StoredContent
from common.logger import log_event

_MAX_CHUNKS_PER_CONTENT = 5

_EXCERPT_MAX_CHARS = 300
_FIRST_SEEN_CONTENT_STRIDE = 100_000
_FIELD_WEIGHTS_CHUNK = {
    "title": 3.0,
    "heading": 2.0,
    "body": 1.0,
}
_FIELD_WEIGHTS_WEB_SEARCH = {
    "title": 3.0,
    "domain": 1.0,
    "snippet": 1.0,
}


@dataclass(frozen=True, slots=True)
class _EvidenceCandidate:
    content_id: str
    chunk_index: int
    evidence_type: str
    score: float
    original_rank: int
    title: str
    source: str
    url: str
    domain: str
    source_id: str
    excerpt_source_text: str
    start_offset: int
    end_offset: int
    term_hit_stats: Tuple[EvidenceTermHitStat, ...]
    matched_reason: str
    first_seen_order: int


@dataclass(frozen=True, slots=True)
class _ChunkEvidenceMeta:
    content_id: str
    chunk_index: int
    title: str
    source: str
    url: str
    chunk_text: str
    start_offset: int
    end_offset: int
    first_seen_order: int
    heading_path: Tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _WebEvidenceMeta:
    content_id: str
    source_id: str
    title: str
    url: str
    domain: str
    snippet: str
    first_seen_order: int


@dataclass(frozen=True, slots=True)
class _RankPartialResult:
    candidates: Tuple[_EvidenceCandidate, ...]
    total_scanned: int


def rank_evidence(
    *,
    query: str,
    content_ids: List[str],
    session_id: str,
    max_evidence: int = 8,
    max_chunks_per_content: int = _MAX_CHUNKS_PER_CONTENT,
) -> EvidenceRankResult:
    log_event(
        "evidence ranking",
        query=query,
        content_ids=len(content_ids),
        max_evidence=max_evidence,
    )

    found: Dict[str, StoredContent] = {}
    missing: List[str] = []
    notes: List[str] = []

    for cid in content_ids:
        stored = tool_content_store.get(content_id=cid, session_id=session_id)
        if stored is not None:
            found[cid] = stored
        else:
            missing.append(cid)

    if missing:
        log_event(
            "evidence ranking 加载",
            found=len(found),
            missing=len(missing),
            missing_ids=",".join(missing),
        )
        add_note(
            notes,
            f"content_id not found or expired: {', '.join(missing)}",
        )

    if not found:
        return EvidenceRankResult(
            query=query,
            total_chunks_scanned=0,
            content_ids_found=(),
            content_ids_missing=tuple(missing),
            notes=tuple(notes),
        )

    query_terms = _extract_bm25_query_terms(query)
    web_search_items: Dict[str, StoredContent] = {}
    generic_items: Dict[str, StoredContent] = {}

    for cid, stored in found.items():
        if stored.metadata.get("content_kind") == "web_search_evidence_pack":
            web_search_items[cid] = stored
        else:
            generic_items[cid] = stored

    content_order = {
        cid: index
        for index, cid in enumerate(content_ids)
        if cid in found
    }
    candidate_window = max(max_evidence, max_evidence * 2)
    candidates: List[_EvidenceCandidate] = []
    total_scanned = 0

    if web_search_items:
        web_result = _rank_web_search_evidence(
            query=query,
            contents=web_search_items,
            max_evidence=candidate_window,
            notes=notes,
            query_terms=query_terms,
            content_order=content_order,
        )
        candidates.extend(web_result.candidates)
        total_scanned += web_result.total_scanned

    if generic_items:
        generic_result = _rank_generic_content_chunks(
            query=query,
            contents=generic_items,
            max_evidence=candidate_window,
            max_chunks_per_content=max_chunks_per_content,
            notes=notes,
            query_terms=query_terms,
            content_order=content_order,
        )
        candidates.extend(generic_result.candidates)
        total_scanned += generic_result.total_scanned

    ranked_candidates = sorted(
        candidates,
        key=lambda item: (-item.score, item.first_seen_order),
    )
    positive_candidates = [
        candidate for candidate in ranked_candidates if candidate.score > 0.0
    ]
    if candidates and not positive_candidates:
        add_note(notes, "No positive lexical match found in ranked candidates.")

    ranked_candidates = positive_candidates[:max_evidence]
    evidence_list = [
        _candidate_to_ranked_evidence(candidate, query_terms=query_terms)
        for candidate in ranked_candidates
    ]

    log_event(
        "evidence ranking 完成",
        query=query,
        total_chunks_scanned=total_scanned,
        sources_with_evidence=len({ev.content_id for ev in evidence_list}),
        evidence_snippets=len(evidence_list),
        max_evidence=max_evidence,
    )

    return EvidenceRankResult(
        query=query,
        evidence=tuple(evidence_list),
        total_chunks_scanned=total_scanned,
        content_ids_found=tuple(found.keys()),
        content_ids_missing=tuple(missing),
        notes=tuple(notes),
    )


def _rank_generic_content_chunks(
    *,
    query: str,
    contents: Dict[str, StoredContent],
    max_evidence: int,
    max_chunks_per_content: int,
    notes: List[str],
    query_terms: Tuple[str, ...],
    content_order: Dict[str, int],
) -> _RankPartialResult:
    documents: List[FieldedDocument] = []
    chunk_meta: Dict[str, _ChunkEvidenceMeta] = {}
    dedupe_texts: Dict[str, str] = {}

    for cid, stored in contents.items():
        title = _display_title(stored)
        source = stored.source or ""
        url = stored.metadata.get("url", "") or ""
        content_seen_order = content_order.get(cid, len(content_order))
        local_order = 0

        if not stored.chunks:
            doc_id = f"{cid}:-1"
            chunk_text = stored.text or ""
            if chunk_text.strip():
                heading_path = _extract_heading_path(None, stored)
                documents.append(
                    _build_chunk_fielded_document(
                        doc_id=doc_id,
                        title=title,
                        heading_path=heading_path,
                        chunk_text=chunk_text,
                    )
                )
                dedupe_texts[doc_id] = _dedupe_chunk_text(chunk_text)
                chunk_meta[doc_id] = _ChunkEvidenceMeta(
                    content_id=cid,
                    chunk_index=-1,
                    title=title,
                    source=source,
                    url=url,
                    chunk_text=chunk_text,
                    start_offset=0,
                    end_offset=len(chunk_text),
                    first_seen_order=(
                        content_seen_order * _FIRST_SEEN_CONTENT_STRIDE
                    ),
                    heading_path=heading_path,
                )
        else:
            for chunk in stored.chunks:
                chunk_text = _extract_chunk_text(stored.text, chunk)
                if not chunk_text.strip():
                    continue
                doc_id = f"{cid}:{chunk.index}"
                heading_path = _extract_heading_path(chunk, stored)
                first_seen_order = (
                    content_seen_order * _FIRST_SEEN_CONTENT_STRIDE
                    + local_order
                )
                local_order += 1
                documents.append(
                    _build_chunk_fielded_document(
                        doc_id=doc_id,
                        title=title,
                        heading_path=heading_path,
                        chunk_text=chunk_text,
                    )
                )
                dedupe_texts[doc_id] = _dedupe_chunk_text(chunk_text)
                chunk_meta[doc_id] = _ChunkEvidenceMeta(
                    content_id=cid,
                    chunk_index=chunk.index,
                    title=title,
                    source=source,
                    url=url,
                    chunk_text=chunk_text,
                    start_offset=chunk.start_offset,
                    end_offset=chunk.end_offset,
                    first_seen_order=first_seen_order,
                    heading_path=heading_path,
                )

    total_scanned = len(documents)
    log_event(
        "evidence ranking 分块",
        query=query,
        total_chunks=total_scanned,
        contents=len(contents),
    )

    if not documents:
        add_note(notes, "No readable chunks found in the specified content.")
        return _RankPartialResult(candidates=(), total_scanned=0)

    before_dedup = len(documents)
    documents = _deduplicate_exact_chunks(documents, dedupe_texts)
    dedup_hit = before_dedup - len(documents)
    if dedup_hit > 0:
        log_event(
            "evidence ranking 去重",
            query=query,
            before=before_dedup,
            after=len(documents),
            duplicates=dedup_hit,
        )
        add_note(
            notes,
            f"Skipped {dedup_hit} duplicate chunk(s) with identical normalized text.",
        )

    rank_started = time.monotonic()
    scores_by_id = score_fielded_bm25(query, documents, _FIELD_WEIGHTS_CHUNK)
    ranked = _rank_fielded_documents(documents, scores_by_id)
    rank_elapsed_ms = int((time.monotonic() - rank_started) * 1000)
    scores = [s for _, s, _ in ranked]
    log_event(
        "evidence ranking fielded BM25",
        query=query,
        scored=len(ranked),
        top_score=f"{max(scores):.4f}" if scores else "0",
        avg_score=f"{sum(scores) / len(scores):.4f}" if scores else "0",
        chunk_count=len(documents),
        rank_elapsed_ms=rank_elapsed_ms,
    )

    per_content_count: Dict[str, int] = {}
    candidates: List[_EvidenceCandidate] = []
    max_chunks_skipped = 0

    for doc_id, score, rank in ranked:
        meta = chunk_meta.get(doc_id)
        if meta is None:
            continue

        count = per_content_count.get(meta.content_id, 0)
        if count >= max_chunks_per_content:
            max_chunks_skipped += 1
            continue

        field_texts = _chunk_field_texts(meta)
        term_hit_stats = _build_term_hit_stats(
            query_terms=query_terms,
            field_texts=field_texts,
        )

        candidates.append(
            _EvidenceCandidate(
                content_id=meta.content_id,
                chunk_index=meta.chunk_index,
                evidence_type="chunk",
                score=score,
                original_rank=rank,
                title=meta.title,
                source=meta.source,
                url=meta.url,
                domain="",
                source_id="",
                excerpt_source_text=meta.chunk_text,
                start_offset=meta.start_offset,
                end_offset=meta.end_offset,
                matched_reason=_build_matched_reason(
                    evidence_type="chunk",
                    term_hit_stats=term_hit_stats,
                ),
                term_hit_stats=term_hit_stats,
                first_seen_order=meta.first_seen_order,
            )
        )
        per_content_count[meta.content_id] = count + 1

        if len(candidates) >= max_evidence:
            break

    if max_chunks_skipped > 0:
        add_note(
            notes,
            f"Skipped {max_chunks_skipped} chunk(s) after "
            f"max_chunks_per_content={max_chunks_per_content}.",
        )

    return _RankPartialResult(
        candidates=tuple(candidates),
        total_scanned=total_scanned,
    )


def _rank_web_search_evidence(
    *,
    query: str,
    contents: Dict[str, StoredContent],
    max_evidence: int,
    notes: List[str],
    query_terms: Tuple[str, ...],
    content_order: Dict[str, int],
) -> _RankPartialResult:
    documents: List[FieldedDocument] = []
    meta: Dict[str, _WebEvidenceMeta] = {}

    for cid, stored in contents.items():
        try:
            payload = json.loads(stored.text)
        except json.JSONDecodeError:
            add_note(notes, f"web_search_evidence_pack is not valid JSON: {cid}")
            continue

        results = payload.get("results")
        if not isinstance(results, list):
            add_note(notes, f"web_search_evidence_pack missing results array: {cid}")
            continue

        content_seen_order = content_order.get(cid, len(content_order))
        local_order = 0
        for item in results:
            if not isinstance(item, dict):
                continue

            source_id = item.get("source_id")
            title = item.get("title")
            url = item.get("url")
            domain = item.get("domain")
            snippet = item.get("snippet")

            if not all(
                isinstance(value, str)
                for value in [source_id, title, url, domain, snippet]
            ):
                continue

            if not source_id or not url:
                continue

            doc_id = f"{cid}:source:{source_id}"
            fields = {
                "title": title,
                "domain": domain,
                "snippet": snippet,
            }

            if not any(value.strip() for value in fields.values()):
                continue

            documents.append(FieldedDocument(id=doc_id, fields=fields))
            meta[doc_id] = _WebEvidenceMeta(
                content_id=cid,
                source_id=source_id,
                title=title,
                url=url,
                domain=domain,
                snippet=snippet,
                first_seen_order=(
                    content_seen_order * _FIRST_SEEN_CONTENT_STRIDE
                    + local_order
                ),
            )
            local_order += 1

    if not documents:
        add_note(
            notes,
            "No readable web_search results found in the specified content.",
        )
        return _RankPartialResult(candidates=(), total_scanned=0)

    rank_started = time.monotonic()
    scores_by_id = score_fielded_bm25(
        query,
        documents,
        _FIELD_WEIGHTS_WEB_SEARCH,
    )
    ranked = _rank_fielded_documents(documents, scores_by_id)
    rank_elapsed_ms = int((time.monotonic() - rank_started) * 1000)
    log_event(
        "evidence ranking web_search fielded BM25",
        query=query,
        scored=len(ranked),
        result_count=len(documents),
        rank_elapsed_ms=rank_elapsed_ms,
    )

    candidates: List[_EvidenceCandidate] = []
    same_domain_count: Dict[str, int] = {}
    same_domain_skipped = 0

    for doc_id, score, rank in ranked:
        data = meta.get(doc_id)
        if data is None:
            continue

        domain = data.domain
        domain_count = same_domain_count.get(domain, 0)
        if domain and domain_count >= 2:
            same_domain_skipped += 1
            continue

        field_texts = {
            "title": data.title,
            "domain": data.domain,
            "snippet": data.snippet,
        }
        term_hit_stats = _build_term_hit_stats(
            query_terms=query_terms,
            field_texts=field_texts,
        )
        candidates.append(
            _EvidenceCandidate(
                content_id=data.content_id,
                chunk_index=-1,
                score=score,
                original_rank=rank,
                title=data.title,
                source=data.domain,
                url=data.url,
                domain=data.domain,
                source_id=data.source_id,
                excerpt_source_text=data.snippet,
                start_offset=0,
                end_offset=0,
                evidence_type="web_search_result",
                matched_reason=_build_matched_reason(
                    evidence_type="web_search_result",
                    term_hit_stats=term_hit_stats,
                ),
                term_hit_stats=term_hit_stats,
                first_seen_order=data.first_seen_order,
            )
        )

        if domain:
            same_domain_count[domain] = domain_count + 1

        if len(candidates) >= max_evidence:
            break

    if same_domain_skipped > 0:
        add_note(
            notes,
            f"Skipped {same_domain_skipped} web result(s) after same-domain cap of 2.",
        )

    return _RankPartialResult(
        candidates=tuple(candidates),
        total_scanned=len(documents),
    )


def _candidate_to_ranked_evidence(
    candidate: _EvidenceCandidate,
    *,
    query_terms: Tuple[str, ...],
) -> RankedEvidence:
    return RankedEvidence(
        content_id=candidate.content_id,
        chunk_index=candidate.chunk_index,
        score=candidate.score,
        rank=candidate.original_rank,
        title=candidate.title,
        source=candidate.source,
        url=candidate.url,
        excerpt=_make_excerpt(
            candidate.excerpt_source_text,
            query_terms=query_terms,
        ),
        start_offset=candidate.start_offset,
        end_offset=candidate.end_offset,
        source_id=candidate.source_id,
        domain=candidate.domain,
        evidence_type=candidate.evidence_type,
        matched_reason=candidate.matched_reason,
        term_hit_stats=candidate.term_hit_stats,
    )


def _extract_chunk_text(full_text: str, chunk: ContentChunk) -> str:
    if chunk.start_offset < 0 or chunk.end_offset < 0:
        return ""
    end = min(chunk.end_offset, len(full_text))
    if chunk.start_offset >= end:
        return ""
    return full_text[chunk.start_offset : end]


def _build_chunk_fielded_document(
    *,
    doc_id: str,
    title: str,
    heading_path: Tuple[str, ...],
    chunk_text: str,
) -> FieldedDocument:
    return FieldedDocument(
        id=doc_id,
        fields={
            "title": title,
            "heading": " > ".join(heading_path),
            "body": chunk_text,
        },
    )


def _chunk_field_texts(meta: _ChunkEvidenceMeta) -> Dict[str, str]:
    return {
        "title": meta.title,
        "heading": " > ".join(meta.heading_path),
        "body": meta.chunk_text,
    }


def _extract_bm25_query_terms(query: str) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(tokenize_for_bm25(query)))


def _build_term_hit_stats(
    *,
    query_terms: Tuple[str, ...],
    field_texts: Dict[str, str],
) -> Tuple[EvidenceTermHitStat, ...]:
    if not query_terms:
        return ()

    field_token_counts: Dict[str, Counter[str]] = {
        field: Counter(tokenize_for_bm25(text))
        for field, text in field_texts.items()
        if text
    }

    stats: List[EvidenceTermHitStat] = []
    for term in query_terms:
        field_stats: List[EvidenceFieldHitStat] = []
        total_count = 0

        for field, token_counts in field_token_counts.items():
            count = token_counts.get(term, 0)
            if count <= 0:
                continue
            field_stats.append(EvidenceFieldHitStat(field=field, count=count))
            total_count += count

        if total_count <= 0:
            continue

        stats.append(
            EvidenceTermHitStat(
                term=term,
                total_count=total_count,
                field_stats=tuple(field_stats),
            )
        )

    return tuple(stats)


def _build_matched_reason(
    *,
    evidence_type: str,
    term_hit_stats: Tuple[EvidenceTermHitStat, ...],
) -> str:
    if term_hit_stats:
        fields: List[str] = []
        seen: Set[str] = set()
        for term_stat in term_hit_stats:
            for field_stat in term_stat.field_stats:
                if field_stat.field in seen:
                    continue
                seen.add(field_stat.field)
                fields.append(field_stat.field)
        if fields:
            return "Matched BM25 query terms in " + ", ".join(fields) + "."

    if evidence_type == "web_search_result":
        return "Ranked by fielded BM25 over web search title, domain, and snippet."

    return "Ranked by fielded BM25 over document title, heading, and body."


def _rank_fielded_documents(
    documents: List[FieldedDocument],
    scores_by_id: Dict[str, float],
) -> List[Tuple[str, float, int]]:
    ordered = sorted(
        enumerate(documents),
        key=lambda item: (-scores_by_id.get(item[1].id, 0.0), item[0]),
    )
    return [
        (document.id, scores_by_id.get(document.id, 0.0), rank)
        for rank, (_, document) in enumerate(ordered)
    ]


def _make_excerpt(
    text: str,
    *,
    query_terms: Tuple[str, ...] = (),
) -> str:
    clean = " ".join(text.split())
    if len(clean) <= _EXCERPT_MAX_CHARS:
        return clean

    hit_index = _first_query_term_index(clean, query_terms)
    if hit_index is None:
        return clean[:_EXCERPT_MAX_CHARS] + "..."

    half_window = _EXCERPT_MAX_CHARS // 2
    start = max(0, hit_index - half_window)
    end = min(len(clean), start + _EXCERPT_MAX_CHARS)
    start = max(0, end - _EXCERPT_MAX_CHARS)

    excerpt = clean[start:end]
    if start > 0:
        excerpt = "..." + excerpt.lstrip()
    if end < len(clean):
        excerpt = excerpt.rstrip() + "..."
    return excerpt


def _first_query_term_index(
    text: str,
    query_terms: Tuple[str, ...],
) -> Optional[int]:
    if not query_terms:
        return None

    text_lower = text.lower()
    hit_indexes = [
        index
        for term in query_terms
        if term
        for index in [text_lower.find(term.lower())]
        if index >= 0
    ]
    if not hit_indexes:
        return None
    return min(hit_indexes)


def _display_title(stored: StoredContent) -> str:
    title = stored.metadata.get("title")
    if isinstance(title, str) and title:
        return title

    display_name = stored.metadata.get("display_name")
    if isinstance(display_name, str) and display_name:
        return display_name

    source = stored.source or ""
    if PurePath(source).is_absolute() or source.startswith("/"):
        return PurePath(source).name

    return source


def _extract_heading_path(
    chunk: Optional[ContentChunk],
    stored: StoredContent,
) -> Tuple[str, ...]:
    raw = None
    if chunk is not None:
        raw = chunk.metadata.get("heading_path")
        if raw is None:
            raw = chunk.metadata.get("headings")
        if raw is None:
            raw = chunk.metadata.get("heading")

    if raw is None:
        raw = stored.metadata.get("heading_path")
    if raw is None:
        raw = stored.metadata.get("headings")
    if raw is None:
        raw = stored.metadata.get("heading")

    if isinstance(raw, tuple):
        return tuple(item for item in raw if isinstance(item, str) and item)
    if isinstance(raw, list):
        return tuple(item for item in raw if isinstance(item, str) and item)
    if isinstance(raw, str) and raw:
        return (raw,)
    return ()


def _dedupe_chunk_text(text: str) -> str:
    return " ".join(text.split())


def _deduplicate_exact_chunks(
    documents: List[FieldedDocument],
    dedupe_texts: Dict[str, str],
) -> List[FieldedDocument]:
    seen: Set[str] = set()
    deduped: List[FieldedDocument] = []
    for document in documents:
        text_normalized = dedupe_texts.get(document.id, "")
        if text_normalized not in seen:
            seen.add(text_normalized)
            deduped.append(document)
    return deduped
