from __future__ import annotations

from typing import List

from chat.application.algorithms.ranking import (
    FieldedDocument,
    RankedList,
    rank_fielded_bm25,
    tokenize_for_bm25,
    weighted_rrf,
)
from chat.application.tools.services.software_ecosystem.common.normalization import (
    package_entity_id,
)

from .models import PackageCandidate

_PACKAGE_CANDIDATE_FIELD_WEIGHTS = {
    "name": 3.0,
    "summary": 2.0,
    "repository_url": 0.8,
    "homepage_url": 0.4,
    "source": 0.2,
}

_SOURCE_WEIGHTS = {
    "ecosystems": 1.0,
    "npm": 0.85,
    "github": 0.55,
}


def rank_package_candidates(query: str, candidates: List[PackageCandidate]) -> List[PackageCandidate]:
    if len(candidates) < 2:
        return candidates

    candidates = _deduplicate_candidates(candidates)
    ids = [package_entity_id(item.ecosystem, item.normalized_name) for item in candidates]
    by_id = dict(zip(ids, candidates))
    position = {item_id: index for index, item_id in enumerate(ids)}
    docs = [
        FieldedDocument(
            id=item_id,
            fields={
                "name": candidate.name,
                "summary": candidate.summary or "",
                "repository_url": candidate.repository_url or "",
                "homepage_url": candidate.homepage_url or "",
                "source": candidate.source,
            },
        )
        for item_id, candidate in zip(ids, candidates)
    ]
    overlap_scores = {
        item_id: _candidate_overlap_score(query, candidate)
        for item_id, candidate in zip(ids, candidates)
    }

    fused = weighted_rrf(
        [
            RankedList(name="source_original", ids=ids, weight=0.5),
            RankedList(
                name="fielded_bm25",
                ids=rank_fielded_bm25(query, docs, _PACKAGE_CANDIDATE_FIELD_WEIGHTS),
                weight=2.6,
            ),
            RankedList(
                name="name_overlap",
                ids=sorted(ids, key=lambda item_id: (-overlap_scores[item_id], position[item_id])),
                weight=1.8,
            ),
            RankedList(
                name="source_score",
                ids=sorted(
                    ids,
                    key=lambda item_id: (
                        -_source_score(by_id[item_id]),
                        -by_id[item_id].raw_score,
                        position[item_id],
                    ),
                ),
                weight=0.8,
            ),
            RankedList(
                name="repository_hint",
                ids=sorted(
                    ids,
                    key=lambda item_id: (
                        not bool(by_id[item_id].repository_url),
                        position[item_id],
                    ),
                ),
                weight=0.4,
            ),
        ]
    )

    rrf_rank = {item.id: item.rank for item in fused}
    rrf_score = {item.id: item.score for item in fused}
    ordered_ids = sorted(
        [item.id for item in fused],
        key=lambda item_id: (
            -overlap_scores[item_id],
            -rrf_score[item_id],
            rrf_rank[item_id],
        ),
    )
    return [by_id[item_id] for item_id in ordered_ids]


def _deduplicate_candidates(candidates: List[PackageCandidate]) -> List[PackageCandidate]:
    best: dict[str, PackageCandidate] = {}
    for item in candidates:
        item_id = package_entity_id(item.ecosystem, item.normalized_name)
        previous = best.get(item_id)
        if previous is None or _candidate_quality(item) > _candidate_quality(previous):
            best[item_id] = item
    return list(best.values())


def _candidate_quality(item: PackageCandidate) -> float:
    quality = item.raw_score + _source_score(item)
    if item.summary:
        quality += 0.15
    if item.repository_url:
        quality += 0.1
    return quality


def _source_score(item: PackageCandidate) -> float:
    return _SOURCE_WEIGHTS.get(item.source, 0.25)


def _candidate_overlap_score(query: str, item: PackageCandidate) -> float:
    query_tokens = set(tokenize_for_bm25(query))
    if not query_tokens:
        return 0.0
    fields = {
        "name": item.name,
        "summary": item.summary or "",
        "repository_url": item.repository_url or "",
        "homepage_url": item.homepage_url or "",
        "source": item.source,
    }
    score = 0.0
    for field, text in fields.items():
        tokens = set(tokenize_for_bm25(text))
        if tokens:
            score += _PACKAGE_CANDIDATE_FIELD_WEIGHTS[field] * len(query_tokens & tokens)
    return score / max(1, len(query_tokens))

