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
class _RankPartialResult:
    evidence: Tuple[RankedEvidence, ...]
    total_scanned: int


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
    heading_path: Tuple[str, ...] = ()


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

    evidence_list: List[RankedEvidence] = []
    total_scanned = 0

    if web_search_items:
        web_result = _rank_web_search_evidence(
            query=query,
            contents=web_search_items,
            max_evidence=max_evidence,
            notes=notes,
            query_terms=query_terms,
        )
        evidence_list.extend(web_result.evidence)
        total_scanned += web_result.total_scanned

    if generic_items and len(evidence_list) < max_evidence:
        generic_result = _rank_generic_content_chunks(
            query=query,
            contents=generic_items,
            max_evidence=max_evidence - len(evidence_list),
            max_chunks_per_content=max_chunks_per_content,
            notes=notes,
            query_terms=query_terms,
        )
        evidence_list.extend(generic_result.evidence)
        total_scanned += generic_result.total_scanned

    evidence_list = sorted(
        evidence_list,
        key=lambda item: item.score,
        reverse=True,
    )[:max_evidence]

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
) -> _RankPartialResult:
    documents: List[FieldedDocument] = []
    chunk_meta: Dict[str, _ChunkEvidenceMeta] = {}
    dedupe_texts: Dict[str, str] = {}

    for cid, stored in contents.items():
        title = _display_title(stored)
        source = stored.source or ""
        url = stored.metadata.get("url", "") or ""

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
                    heading_path=heading_path,
                )
        else:
            for chunk in stored.chunks:
                chunk_text = _extract_chunk_text(stored.text, chunk)
                if not chunk_text.strip():
                    continue
                doc_id = f"{cid}:{chunk.index}"
                heading_path = _extract_heading_path(chunk, stored)
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
        return _RankPartialResult(evidence=(), total_scanned=0)

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
    evidence_list: List[RankedEvidence] = []

    for doc_id, score, rank in ranked:
        meta = chunk_meta.get(doc_id)
        if meta is None:
            continue

        count = per_content_count.get(meta.content_id, 0)
        if count >= max_chunks_per_content:
            continue

        field_texts = _chunk_field_texts(meta)
        term_hit_stats = _build_term_hit_stats(
            query_terms=query_terms,
            field_texts=field_texts,
        )

        evidence_list.append(
            RankedEvidence(
                content_id=meta.content_id,
                chunk_index=meta.chunk_index,
                score=score,
                rank=rank,
                title=meta.title,
                source=meta.source,
                url=meta.url,
                excerpt=_make_excerpt(meta.chunk_text),
                start_offset=meta.start_offset,
                end_offset=meta.end_offset,
                matched_reason=_build_matched_reason(
                    evidence_type="chunk",
                    term_hit_stats=term_hit_stats,
                ),
                term_hit_stats=term_hit_stats,
            )
        )
        per_content_count[meta.content_id] = count + 1

        if len(evidence_list) >= max_evidence:
            break

    return _RankPartialResult(
        evidence=tuple(evidence_list),
        total_scanned=total_scanned,
    )


def _rank_web_search_evidence(
    *,
    query: str,
    contents: Dict[str, StoredContent],
    max_evidence: int,
    notes: List[str],
    query_terms: Tuple[str, ...],
) -> _RankPartialResult:
    documents: List[FieldedDocument] = []
    meta: Dict[str, Dict[str, str]] = {}

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
            meta[doc_id] = {
                "content_id": cid,
                "source_id": source_id,
                "title": title,
                "url": url,
                "domain": domain,
                "snippet": snippet,
            }

    if not documents:
        add_note(
            notes,
            "No readable web_search results found in the specified content.",
        )
        return _RankPartialResult(evidence=(), total_scanned=0)

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

    evidence: List[RankedEvidence] = []
    same_domain_count: Dict[str, int] = {}

    for doc_id, score, rank in ranked:
        data = meta.get(doc_id)
        if data is None:
            continue

        domain = data["domain"]
        domain_count = same_domain_count.get(domain, 0)
        if domain and domain_count >= 2:
            continue

        field_texts = {
            "title": data["title"],
            "domain": data["domain"],
            "snippet": data["snippet"],
        }
        term_hit_stats = _build_term_hit_stats(
            query_terms=query_terms,
            field_texts=field_texts,
        )
        evidence.append(
            RankedEvidence(
                content_id=data["content_id"],
                chunk_index=-1,
                score=score,
                rank=rank,
                title=data["title"],
                source=data["domain"],
                url=data["url"],
                excerpt=data["snippet"],
                source_id=data["source_id"],
                domain=data["domain"],
                evidence_type="web_search_result",
                matched_reason=_build_matched_reason(
                    evidence_type="web_search_result",
                    term_hit_stats=term_hit_stats,
                ),
                term_hit_stats=term_hit_stats,
            )
        )

        if domain:
            same_domain_count[domain] = domain_count + 1

        if len(evidence) >= max_evidence:
            break

    return _RankPartialResult(
        evidence=tuple(evidence),
        total_scanned=len(documents),
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


def _make_excerpt(text: str) -> str:
    clean = " ".join(text.split())
    if len(clean) > _EXCERPT_MAX_CHARS:
        return clean[:_EXCERPT_MAX_CHARS] + "..."
    return clean


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
