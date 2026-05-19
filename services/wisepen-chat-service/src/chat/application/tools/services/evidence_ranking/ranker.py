from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import PurePath
from typing import Dict, List, Set, Tuple

from chat.application.algorithms.ranking import rank_documents_by_bm25
from chat.application.tools.services.evidence_ranking.models import (
    EvidenceRankResult,
    RankedEvidence,
)
from chat.application.tools.common.tool_content_store import tool_content_store
from chat.application.web_search.utils.notes import add_note
from chat.core.content_store.models import ContentChunk, StoredContent
from common.logger import log_event

_MAX_CHUNKS_PER_CONTENT = 5

_EXCERPT_MAX_CHARS = 300


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
) -> _RankPartialResult:
    documents: List[Tuple[str, str]] = []
    chunk_meta: Dict[str, _ChunkEvidenceMeta] = {}

    for cid, stored in contents.items():
        title = _display_title(stored)
        source = stored.source or ""
        url = stored.metadata.get("url", "") or ""

        if not stored.chunks:
            doc_id = f"{cid}:-1"
            chunk_text = stored.text or ""
            if chunk_text.strip():
                combined = f"{title} {source} {chunk_text}".strip()
                documents.append((doc_id, combined))
                chunk_meta[doc_id] = _ChunkEvidenceMeta(
                    content_id=cid,
                    chunk_index=-1,
                    title=title,
                    source=source,
                    url=url,
                    chunk_text=chunk_text,
                    start_offset=0,
                    end_offset=len(chunk_text),
                )
        else:
            for chunk in stored.chunks:
                chunk_text = _extract_chunk_text(stored.text, chunk)
                if not chunk_text.strip():
                    continue
                doc_id = f"{cid}:{chunk.index}"
                combined = f"{title} {source} {chunk_text}".strip()
                documents.append((doc_id, combined))
                chunk_meta[doc_id] = _ChunkEvidenceMeta(
                    content_id=cid,
                    chunk_index=chunk.index,
                    title=title,
                    source=source,
                    url=url,
                    chunk_text=chunk_text,
                    start_offset=chunk.start_offset,
                    end_offset=chunk.end_offset,
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

        count = per_content_count.get(meta.content_id, 0)
        if count >= max_chunks_per_content:
            continue

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
) -> _RankPartialResult:
    documents: List[Tuple[str, str]] = []
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
            combined = " ".join(
                part for part in [title, domain, snippet] if part
            ).strip()

            if not combined:
                continue

            documents.append((doc_id, combined))
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
    rank_result = rank_documents_by_bm25(
        query,
        documents,
        cache_key=_make_bm25_cache_key(documents),
    )
    rank_elapsed_ms = int((time.monotonic() - rank_started) * 1000)
    log_event(
        "evidence ranking web_search BM25",
        query=query,
        scored=len(rank_result.ranked),
        cache_hit=rank_result.cache_hit,
        result_count=len(documents),
        rank_elapsed_ms=rank_elapsed_ms,
        build_index_elapsed_ms=rank_result.build_index_elapsed_ms,
    )

    evidence: List[RankedEvidence] = []
    same_domain_count: Dict[str, int] = {}

    for item in rank_result.ranked:
        data = meta.get(item.id)
        if data is None:
            continue

        domain = data["domain"]
        domain_count = same_domain_count.get(domain, 0)
        if domain and domain_count >= 2:
            continue

        evidence.append(
            RankedEvidence(
                content_id=data["content_id"],
                chunk_index=-1,
                score=item.score,
                rank=item.rank,
                title=data["title"],
                source=data["domain"],
                url=data["url"],
                excerpt=data["snippet"],
                source_id=data["source_id"],
                domain=data["domain"],
                evidence_type="web_search_result",
                matched_reason="Matched title/domain/snippet from web search result.",
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
