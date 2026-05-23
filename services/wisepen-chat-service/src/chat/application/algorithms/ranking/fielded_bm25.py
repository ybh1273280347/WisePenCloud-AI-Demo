from __future__ import annotations

import hashlib
from typing import Dict, List, Mapping, Sequence, Tuple

from chat.application.algorithms.ranking.bm25 import rank_documents_by_bm25
from chat.application.algorithms.ranking.models import FieldedDocument

_FIELDED_BM25_CACHE_PREFIX = "fielded_bm25"


def score_fielded_bm25(
    query: str,
    documents: Sequence[FieldedDocument],
    field_weights: Mapping[str, float],
) -> Dict[str, float]:
    scores = {document.id: 0.0 for document in documents}
    if not documents:
        return scores

    for field_name, weight in field_weights.items():
        field_documents = [
            (document.id, document.fields.get(field_name, "") or "")
            for document in documents
        ]
        result = rank_documents_by_bm25(
            query,
            field_documents,
            cache_key=_field_bm25_cache_key(field_name, field_documents),
        )
        for item in result.ranked:
            scores[item.id] = scores.get(item.id, 0.0) + (float(weight) * item.score)

    return scores


def rank_fielded_bm25(
    query: str,
    documents: Sequence[FieldedDocument],
    field_weights: Mapping[str, float],
) -> List[str]:
    scores = score_fielded_bm25(query, documents, field_weights)
    ordered = sorted(
        enumerate(documents),
        key=lambda item: (-scores.get(item[1].id, 0.0), item[0]),
    )
    return [document.id for _, document in ordered]


def _field_bm25_cache_key(
    field_name: str,
    field_documents: Sequence[Tuple[str, str]],
) -> str:
    return (
        f"{_FIELDED_BM25_CACHE_PREFIX}:"
        f"{field_name}:"
        f"{_field_documents_fingerprint(field_documents)}"
    )


def _field_documents_fingerprint(
    field_documents: Sequence[Tuple[str, str]],
) -> str:
    digest = hashlib.sha256()
    for doc_id, text in field_documents:
        digest.update(doc_id.encode("utf-8", errors="ignore"))
        digest.update(b"\0")
        digest.update((text or "").encode("utf-8", errors="ignore"))
        digest.update(b"\0")
    return digest.hexdigest()
