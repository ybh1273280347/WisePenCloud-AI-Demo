from __future__ import annotations

import hashlib
import time
from typing import Dict, List, Set, Tuple

from chat.application.algorithms.ranking import rank_documents_by_bm25
from chat.application.evidence_ranking.models import (
    EvidenceRankResult,
    RankedEvidence,
)
from chat.application.tool_content_store import tool_content_store
from chat.application.web_search.utils.notes import add_note
from chat.core.content_store.models import ContentChunk, StoredContent
from common.logger import log_event

_MAX_CHUNKS_PER_CONTENT = 5

_EXCERPT_MAX_CHARS = 300


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

    documents: List[Tuple[str, str]] = []
    chunk_meta: Dict[str, Tuple[str, int, str, str, str]] = {}

    for cid, stored in found.items():
        title = stored.metadata.get("title", "") or stored.source or ""
        source = stored.source or ""
        url = stored.metadata.get("url", "") or ""

        if not stored.chunks:
            doc_id = f"{cid}:-1"
            chunk_text = stored.text or ""
            if chunk_text.strip():
                combined = f"{title} {source} {chunk_text}".strip()
                documents.append((doc_id, combined))
                chunk_meta[doc_id] = (cid, -1, title, source, url)
        else:
            for chunk in stored.chunks:
                chunk_text = _extract_chunk_text(stored.text, chunk)
                if not chunk_text.strip():
                    continue
                doc_id = f"{cid}:{chunk.index}"
                combined = f"{title} {source} {chunk_text}".strip()
                documents.append((doc_id, combined))
                chunk_meta[doc_id] = (cid, chunk.index, title, source, url)

    total_scanned = len(documents)
    log_event(
        "evidence ranking 分块",
        query=query,
        total_chunks=total_scanned,
        contents=len(found),
    )

    if not documents:
        add_note(notes, "No readable chunks found in the specified content.")
        return EvidenceRankResult(
            query=query,
            total_chunks_scanned=0,
            content_ids_found=tuple(found.keys()),
            content_ids_missing=tuple(missing),
            notes=tuple(notes),
        )

    before_dedup = len(documents)
    documents = _deduplicate_exact_chunks(documents)
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
    rank_result = rank_documents_by_bm25(
        query,
        documents,
        cache_key=_make_bm25_cache_key(documents),
    )
    ranked = [(item.id, item.score, item.rank) for item in rank_result.ranked]
    rank_elapsed_ms = int((time.monotonic() - rank_started) * 1000)
    scores = [s for _, s, _ in ranked]
    log_event(
        "evidence ranking BM25",
        query=query,
        scored=len(ranked),
        top_score=f"{max(scores):.4f}" if scores else "0",
        avg_score=f"{sum(scores) / len(scores):.4f}" if scores else "0",
        cache_hit=rank_result.cache_hit,
        chunk_count=len(documents),
        rank_elapsed_ms=rank_elapsed_ms,
        build_index_elapsed_ms=rank_result.build_index_elapsed_ms,
    )

    per_content_count: Dict[str, int] = {}
    evidence_list: List[RankedEvidence] = []

    for doc_id, score, rank in ranked:
        meta = chunk_meta.get(doc_id)
        if meta is None:
            continue

        cid, chunk_index, title, source, url = meta

        count = per_content_count.get(cid, 0)
        if count >= max_chunks_per_content:
            continue

        excerpt = _get_excerpt(documents, doc_id)

        evidence_list.append(
            RankedEvidence(
                content_id=cid,
                chunk_index=chunk_index,
                score=score,
                rank=rank,
                title=title,
                source=source,
                url=url,
                excerpt=excerpt,
            )
        )
        per_content_count[cid] = count + 1

        if len(evidence_list) >= max_evidence:
            break

    log_event(
        "evidence ranking 完成",
        query=query,
        total_chunks_scanned=total_scanned,
        sources_with_evidence=len(per_content_count),
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


def _extract_chunk_text(full_text: str, chunk: ContentChunk) -> str:
    if chunk.start_offset < 0 or chunk.end_offset < 0:
        return ""
    end = min(chunk.end_offset, len(full_text))
    if chunk.start_offset >= end:
        return ""
    return full_text[chunk.start_offset : end]


def _get_excerpt(
    documents: List[Tuple[str, str]],
    doc_id: str,
) -> str:
    for did, text in documents:
        if did == doc_id:
            clean = " ".join(text.split())
            if len(clean) > _EXCERPT_MAX_CHARS:
                return clean[:_EXCERPT_MAX_CHARS] + "..."
            return clean
    return ""


def _deduplicate_exact_chunks(
    documents: List[Tuple[str, str]],
) -> List[Tuple[str, str]]:
    seen: Set[str] = set()
    deduped: List[Tuple[str, str]] = []
    for item in documents:
        text_normalized = " ".join(item[1].split())
        if text_normalized not in seen:
            seen.add(text_normalized)
            deduped.append(item)
    return deduped


def _make_bm25_cache_key(documents: List[Tuple[str, str]]) -> str:
    content_ids = []
    for doc_id, _ in documents:
        content_id, _, _ = doc_id.partition(":")
        if content_id:
            content_ids.append(content_id)

    if content_ids:
        return "content_ids:" + "|".join(dict.fromkeys(content_ids))

    return "chunks:" + _documents_fingerprint(documents)


def _documents_fingerprint(documents: List[Tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for doc_id, text in documents:
        digest.update(doc_id.encode("utf-8", errors="ignore"))
        digest.update(b"\0")
        digest.update(text.encode("utf-8", errors="ignore"))
        digest.update(b"\0")
    return digest.hexdigest()
