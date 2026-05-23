from __future__ import annotations

import hashlib
import threading
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from chat.application.algorithms.ranking.models import Bm25RankResult, RankedDocument
from chat.application.algorithms.ranking.tokenizer import tokenize_for_bm25
from rank_bm25 import BM25Okapi

_BM25_INDEX_CACHE_MAXSIZE = 32


@dataclass(frozen=True, slots=True)
class _CachedBm25Index:
    doc_ids: Tuple[str, ...]
    bm25: Optional[BM25Okapi]
    fingerprint: str


_bm25_index_cache: OrderedDict[str, _CachedBm25Index] = OrderedDict()
_bm25_index_cache_lock = threading.Lock()


def rank_documents_by_bm25(
    query: str,
    documents: Sequence[Tuple[str, str]],
    cache_key: Optional[str] = None,
) -> Bm25RankResult:
    if not documents:
        return Bm25RankResult(ranked=())

    doc_ids = tuple(doc_id for doc_id, _ in documents)
    tokenized_query = tokenize_for_bm25(query)

    if not tokenized_query:
        return Bm25RankResult(
            ranked=tuple(
                RankedDocument(id=doc_id, score=0.0, rank=rank)
                for rank, doc_id in enumerate(doc_ids)
            )
        )

    if len(documents) < 2:
        doc_id, text = documents[0]
        return Bm25RankResult(
            ranked=(
                RankedDocument(
                    id=doc_id,
                    score=_score_single_document(text, tokenized_query),
                    rank=0,
                ),
            )
        )

    started = time.monotonic()
    cache_hit = False
    build_index_elapsed_ms = 0

    if cache_key:
        fingerprint = _documents_fingerprint(documents)
        cached = _get_cached_bm25_index(cache_key, fingerprint)
        if cached is not None:
            bm25 = cached.bm25
            cache_hit = True
        else:
            bm25 = _build_bm25_index(documents)
            build_index_elapsed_ms = int((time.monotonic() - started) * 1000)
            _set_cached_bm25_index(
                cache_key,
                _CachedBm25Index(
                    doc_ids=doc_ids,
                    bm25=bm25,
                    fingerprint=fingerprint,
                ),
            )
    else:
        bm25 = _build_bm25_index(documents)
        build_index_elapsed_ms = int((time.monotonic() - started) * 1000)

    ranked = _score_documents(doc_ids, bm25, tokenized_query)
    return Bm25RankResult(
        ranked=ranked,
        cache_hit=cache_hit,
        build_index_elapsed_ms=build_index_elapsed_ms,
    )


def _build_bm25_index(documents: Sequence[Tuple[str, str]]) -> Optional[BM25Okapi]:
    tokenized_docs = [tokenize_for_bm25(text) for _, text in documents]
    if not any(tokenized_docs):
        return None
    return BM25Okapi(tokenized_docs)


def _score_documents(
    doc_ids: Tuple[str, ...],
    bm25: Optional[BM25Okapi],
    tokenized_query: List[str],
) -> Tuple[RankedDocument, ...]:
    if bm25 is None:
        return tuple(
            RankedDocument(id=doc_id, score=0.0, rank=rank)
            for rank, doc_id in enumerate(doc_ids)
        )

    scores = bm25.get_scores(tokenized_query)
    ordered = sorted(
        enumerate(zip(doc_ids, scores)),
        key=lambda item: (-item[1][1], item[0]),
    )
    return tuple(
        RankedDocument(id=doc_id, score=float(score), rank=rank)
        for rank, (_, (doc_id, score)) in enumerate(ordered)
    )


def _score_single_document(text: str, tokenized_query: List[str]) -> float:
    tokenized_document = tokenize_for_bm25(text)
    if not tokenized_document:
        return 0.0

    query_terms = set(tokenized_query)
    document_terms = set(tokenized_document)
    overlap_terms = query_terms.intersection(document_terms)
    if not overlap_terms:
        return 0.0

    query_frequencies = Counter(tokenized_query)
    document_frequencies = Counter(tokenized_document)
    overlap_frequency = sum(
        min(query_frequencies[term], document_frequencies[term])
        for term in overlap_terms
    )
    return float(
        (2 * overlap_frequency) / (len(tokenized_query) + len(tokenized_document))
    )


def _documents_fingerprint(documents: Sequence[Tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for doc_id, text in documents:
        digest.update(doc_id.encode("utf-8", errors="ignore"))
        digest.update(b"\0")
        digest.update((text or "").encode("utf-8", errors="ignore"))
        digest.update(b"\0")
    return digest.hexdigest()


def _get_cached_bm25_index(
    cache_key: str,
    fingerprint: str,
) -> Optional[_CachedBm25Index]:
    with _bm25_index_cache_lock:
        cached = _bm25_index_cache.get(cache_key)
        if cached is None or cached.fingerprint != fingerprint:
            return None
        _bm25_index_cache.move_to_end(cache_key)
        return cached


def _set_cached_bm25_index(
    cache_key: str,
    value: _CachedBm25Index,
) -> None:
    with _bm25_index_cache_lock:
        _bm25_index_cache[cache_key] = value
        _bm25_index_cache.move_to_end(cache_key)
        while len(_bm25_index_cache) > _BM25_INDEX_CACHE_MAXSIZE:
            _bm25_index_cache.popitem(last=False)
