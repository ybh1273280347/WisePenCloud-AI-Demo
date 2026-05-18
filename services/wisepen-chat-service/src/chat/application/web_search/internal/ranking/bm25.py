from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse
from typing import Dict, List, Tuple

from chat.application.algorithms.ranking import (
    FieldedDocument,
    rank_documents_by_bm25 as _rank_documents_by_bm25,
    rank_fielded_bm25,
    score_fielded_bm25,
)
from chat.application.web_search.internal.ranking.models import SearchUrlCandidate


@dataclass(frozen=True, slots=True)
class Bm25RankedItem:
    id: str
    score: float
    rank: int


def rank_documents_by_bm25(
    *,
    query: str,
    documents: List[Tuple[str, str]],
) -> List[Bm25RankedItem]:
    result = _rank_documents_by_bm25(query, documents)
    return [
        Bm25RankedItem(
            id=item.id,
            score=item.score,
            rank=item.rank,
        )
        for item in result.ranked
    ]


def extract_url_path_terms(url: str) -> str:
    parsed = urlparse(url)
    return (
        parsed.path
        .replace("/", " ")
        .replace("-", " ")
        .replace("_", " ")
        .replace(".", " ")
    )


METADATA_FIELD_WEIGHTS = {
    "title": 2.0,
    "snippet": 1.0,
    "url_path": 0.3,
}


def score_metadata_bm25f(
    *,
    query: str,
    candidates: List[SearchUrlCandidate],
) -> Dict[str, float]:
    documents = [
        FieldedDocument(
            id=candidate.id,
            fields={
                "title": candidate.title,
                "snippet": candidate.snippet,
                "url_path": extract_url_path_terms(candidate.url),
            },
        )
        for candidate in candidates
    ]
    return score_fielded_bm25(query, documents, METADATA_FIELD_WEIGHTS)


def rank_by_metadata_bm25f(
    *,
    query: str,
    candidates: List[SearchUrlCandidate],
) -> List[str]:
    documents = [
        FieldedDocument(
            id=candidate.id,
            fields={
                "title": candidate.title,
                "snippet": candidate.snippet,
                "url_path": extract_url_path_terms(candidate.url),
            },
        )
        for candidate in candidates
    ]
    return rank_fielded_bm25(query, documents, METADATA_FIELD_WEIGHTS)
