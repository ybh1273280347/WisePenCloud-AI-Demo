from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from chat.application.algorithms.ranking import (
    FieldedDocument,
    RankedList,
    rank_fielded_bm25,
    weighted_rrf,
)
from chat.application.algorithms.text import normalize_title_key
from chat.application.algorithms.url import stable_hash

from .models import PaperSearchResult


_FIELD_WEIGHTS = {
    "title": 4.0,
    "abstract": 2.0,
    "venue": 0.8,
    "authors": 0.5,
    "subject": 1.0,
}

_RECENT_QUERY = re.compile(
    r"\b(latest|recent|new|newest|current|preprint|202[0-9]|203[0-9])\b|最新|近期|近年|预印本",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PaperRankingCandidate:
    id: str
    paper: PaperSearchResult
    title: str
    abstract: str
    authors: str
    year: Optional[int]
    venue: str
    doi: Optional[str]
    arxiv_id: Optional[str]
    source: str
    source_rank: int
    is_preprint: bool
    has_open_access_url: bool
    url: Optional[str]


def normalize_paper_candidates(
    results: List[PaperSearchResult],
) -> List[PaperRankingCandidate]:
    return [_normalize_candidate(result, source_rank=index) for index, result in enumerate(results)]


def rank_papers(
    results: List[PaperSearchResult],
    *,
    query: str = "",
) -> List[PaperSearchResult]:
    return rank_paper_candidates(
        query=query,
        candidates=normalize_paper_candidates(results),
    )


def rank_paper_candidates(
    *,
    query: str,
    candidates: List[PaperRankingCandidate],
) -> List[PaperSearchResult]:
    if len(candidates) < 2:
        return [candidate.paper for candidate in candidates]

    fielded_docs = [
        FieldedDocument(
            id=candidate.id,
            fields={
                "title": candidate.title,
                "abstract": candidate.abstract,
                "venue": candidate.venue,
                "authors": candidate.authors,
                "subject": " ".join(
                    part
                    for part in [
                        candidate.paper.result_type or "",
                        " ".join(candidate.paper.source_names),
                    ]
                    if part
                ),
            },
        )
        for candidate in candidates
    ]

    recency_weight = 1.2 if _RECENT_QUERY.search(query or "") else 0.55
    fused = weighted_rrf(
        [
            RankedList(
                name="source_original_rank",
                ids=[candidate.id for candidate in sorted(candidates, key=lambda item: item.source_rank)],
                weight=0.5,
            ),
            RankedList(
                name="fielded_bm25_rank",
                ids=rank_fielded_bm25(query, fielded_docs, _FIELD_WEIGHTS),
                weight=1.5,
            ),
            RankedList(
                name="recency_rank",
                ids=_rank_candidates_by_score(candidates, _recency_score),
                weight=recency_weight,
            ),
            RankedList(
                name="open_access_rank",
                ids=_rank_candidates_by_score(candidates, lambda item: 1.0 if item.has_open_access_url else 0.0),
                weight=0.45,
            ),
            RankedList(
                name="publication_quality_rank",
                ids=_rank_candidates_by_score(candidates, _publication_quality_score),
                weight=3.5,
            ),
        ]
    )

    by_id = {candidate.id: candidate for candidate in candidates}
    return [by_id[item.id].paper for item in fused if item.id in by_id]


def _normalize_candidate(
    result: PaperSearchResult,
    *,
    source_rank: int,
) -> PaperRankingCandidate:
    source = result.source_names[0] if result.source_names else ""
    doi = result.doi.lower() if result.doi else None
    arxiv_id = result.arxiv_id.lower() if result.arxiv_id else None
    is_preprint = _is_preprint(result)
    has_open_access_url = bool(result.is_open_access or result.pdf_url)
    key = doi or arxiv_id or normalize_title_key(result.title) or result.url or str(source_rank)

    return PaperRankingCandidate(
        id=f"paper:{stable_hash(key)}:{source_rank}",
        paper=result,
        title=result.title or "",
        abstract=result.abstract or "",
        authors=" ".join(result.authors),
        year=result.year,
        venue=result.venue or "",
        doi=doi,
        arxiv_id=arxiv_id,
        source=source,
        source_rank=source_rank,
        is_preprint=is_preprint,
        has_open_access_url=has_open_access_url,
        url=result.url,
    )


def _rank_candidates_by_score(candidates: List[PaperRankingCandidate], scorer) -> List[str]:
    scored = [
        (index, candidate.id, float(scorer(candidate)))
        for index, candidate in enumerate(candidates)
    ]
    scored.sort(key=lambda item: (-item[2], item[0]))
    return [candidate_id for _, candidate_id, _ in scored]


def _publication_quality_score(candidate: PaperRankingCandidate) -> float:
    score = 0.0
    if candidate.doi and not candidate.is_preprint:
        score += 3.0
    elif candidate.doi:
        score += 2.0
    elif candidate.arxiv_id:
        score += 1.0
    if candidate.venue:
        score += 0.25
    score += min(len(candidate.paper.source_names), 3) * 0.2
    return score


def _recency_score(candidate: PaperRankingCandidate) -> float:
    if not candidate.year:
        return 0.0
    current_year = datetime.now(timezone.utc).year
    age = max(0, current_year - candidate.year)
    return max(0.0, 1.0 - age * 0.12)


def _is_preprint(result: PaperSearchResult) -> bool:
    result_type = (result.result_type or "").lower()
    if "preprint" in result_type:
        return True
    if "arxiv" in {source.lower() for source in result.source_names} and not result.doi:
        return True
    return False
