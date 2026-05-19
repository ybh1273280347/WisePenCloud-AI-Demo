from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import date
from typing import Dict, Iterable, List

from chat.application.algorithms.ranking import FieldedDocument, score_fielded_bm25

from .config import MAX_ADJUSTMENT_RATIO, RRF_K
from .models import PaperEntity, PaperSearchFreshness
from .recency import compute_recency_score

DOMINANT_SIGNAL_WEIGHTS = {
    "query_relevance": 1.0,
    "rewrite_rrf": 0.3,
}

RECENCY_WEIGHTS = {
    PaperSearchFreshness.LATEST: 0.4,
    PaperSearchFreshness.BALANCED: 0.15,
    PaperSearchFreshness.STABLE: 0.05,
}

ADJUSTMENT_SIGNAL_WEIGHTS = {
    "metadata_confidence": 0.1,
    "source_confidence": 0.05,
    "evidence_diversity": 0.05,
}

_FIELD_WEIGHTS = {
    "title": 4.0,
    "abstract": 1.7,
    "authors": 0.4,
    "venue": 0.6,
}


def compute_rewrite_rrf_scores(
    per_rewrite_rankings: Dict[str, List[str]],
    *,
    k: int = RRF_K,
) -> Dict[str, float]:
    scores: Dict[str, float] = defaultdict(float)

    for ranked_ids in per_rewrite_rankings.values():
        for rank, canonical_id in enumerate(ranked_ids):
            scores[canonical_id] += 1.0 / (k + rank + 1)

    max_score = max(scores.values(), default=0.0)
    if max_score <= 0.0:
        return {}

    return {canonical_id: score / max_score for canonical_id, score in scores.items()}


def evidence_diversity_score(entity: PaperEntity) -> float:
    source_count = len(set(entity.evidence_sources))

    if source_count >= 3:
        return 1.0
    if source_count == 2:
        return 0.6
    if source_count == 1:
        return 0.2
    return 0.0


def rank_entity(
    entity: PaperEntity,
    *,
    query_relevance: float,
    rewrite_rrf: float,
    freshness: PaperSearchFreshness,
    reference_date: date,
) -> float:
    dominant = (
        DOMINANT_SIGNAL_WEIGHTS["query_relevance"] * query_relevance
        + DOMINANT_SIGNAL_WEIGHTS["rewrite_rrf"] * rewrite_rrf
    )

    recency = RECENCY_WEIGHTS[freshness] * compute_recency_score(
        entity,
        freshness=freshness,
        reference_date=reference_date,
    )

    raw_adjustment = (
        ADJUSTMENT_SIGNAL_WEIGHTS["metadata_confidence"] * entity.metadata_confidence
        + ADJUSTMENT_SIGNAL_WEIGHTS["source_confidence"] * entity.source_confidence
        + ADJUSTMENT_SIGNAL_WEIGHTS["evidence_diversity"]
        * evidence_diversity_score(entity)
    )

    max_adjustment = MAX_ADJUSTMENT_RATIO * max(query_relevance, 0.01)
    adjustment = min(raw_adjustment, max_adjustment)

    return dominant + recency + adjustment


def rank_entities(
    *,
    query: str,
    entities: List[PaperEntity],
    per_rewrite_rankings: Dict[str, List[str]],
    freshness: PaperSearchFreshness,
    reference_date: date,
) -> List[PaperEntity]:
    if not entities:
        return []

    relevance = compute_query_relevance_scores(query, entities)
    rrf = compute_rewrite_rrf_scores(per_rewrite_rankings)
    scored: List[tuple[int, float, PaperEntity]] = []

    for index, entity in enumerate(entities):
        query_relevance = relevance.get(entity.canonical_id, 0.0)
        recency_score = compute_recency_score(entity, freshness, reference_date)
        score = rank_entity(
            entity,
            query_relevance=query_relevance,
            rewrite_rrf=rrf.get(entity.canonical_id, 0.0),
            freshness=freshness,
            reference_date=reference_date,
        )
        scored.append(
            (
                index,
                score,
                replace(
                    entity,
                    relevance_score=score,
                    recency_score=recency_score,
                ),
            )
        )

    scored.sort(key=lambda item: (-item[1], item[0]))
    return [entity for _, _, entity in scored]


def compute_query_relevance_scores(
    query: str,
    entities: Iterable[PaperEntity],
) -> Dict[str, float]:
    entity_list = list(entities)
    docs = [
        FieldedDocument(
            id=entity.canonical_id,
            fields={
                "title": entity.title or "",
                "abstract": entity.abstract or "",
                "authors": " ".join(entity.authors),
                "venue": entity.venue or "",
            },
        )
        for entity in entity_list
    ]
    raw_scores = score_fielded_bm25(query, docs, _FIELD_WEIGHTS)
    max_score = max(raw_scores.values(), default=0.0)

    if max_score <= 0.0:
        return {
            entity.canonical_id: max(0.0, min(1.0, entity.discovery_score))
            for entity in entity_list
        }

    return {
        entity.canonical_id: max(raw_scores.get(entity.canonical_id, 0.0) / max_score, min(entity.discovery_score, 1.0))
        for entity in entity_list
    }
