from __future__ import annotations

from typing import List

from chat.application.algorithms.ranking import (
    FieldedDocument,
    RankedList,
    rank_fielded_bm25,
    tokenize_for_bm25,
    weighted_rrf,
)

from .models import SoftwareEcosystemCandidate

_FIELD_WEIGHTS = {
    "title": 3.0,
    "summary": 2.0,
    "repository": 0.8,
    "language": 0.5,
    "ecosystem": 0.5,
}


def rank_software_ecosystem_candidates(
    *,
    query: str,
    targets: List[str],
    sort: str,
    candidates: List[SoftwareEcosystemCandidate],
) -> List[SoftwareEcosystemCandidate]:
    candidates = _deduplicate(candidates)
    if len(candidates) < 2:
        return candidates

    ids = [item.id for item in candidates]
    by_id = dict(zip(ids, candidates))
    position = {item_id: index for index, item_id in enumerate(ids)}
    docs = [
        FieldedDocument(
            id=item_id,
            fields={
                "title": item.title,
                "summary": item.summary or "",
                "repository": item.repository or "",
                "language": item.language or "",
                "ecosystem": item.ecosystem or "",
            },
        )
        for item_id, item in zip(ids, candidates)
    ]
    field_weight = _sort_weight(sort, "metadata_bm25")
    fused = weighted_rrf(
        [
            RankedList(
                name="metadata_bm25",
                ids=rank_fielded_bm25(query, docs, _FIELD_WEIGHTS),
                weight=field_weight,
            ),
            RankedList(name="source_original", ids=ids, weight=0.35),
            RankedList(
                name="target_priority",
                ids=sorted(ids, key=lambda item_id: (-_target_priority(by_id[item_id], targets), position[item_id])),
                weight=0.8,
            ),
            RankedList(
                name="popularity",
                ids=sorted(ids, key=lambda item_id: (-_popularity_score(by_id[item_id], sort), position[item_id])),
                weight=_sort_weight(sort, "popularity"),
            ),
            RankedList(
                name="maintenance",
                ids=sorted(ids, key=lambda item_id: (_is_archived(by_id[item_id]), -_metric(by_id[item_id], "maintenance"), position[item_id])),
                weight=_sort_weight(sort, "maintenance"),
            ),
            RankedList(
                name="recent_activity",
                ids=sorted(ids, key=lambda item_id: (-_metric(by_id[item_id], "recent_activity"), position[item_id])),
                weight=_sort_weight(sort, "recent_activity"),
            ),
            RankedList(
                name="community_attention",
                ids=sorted(ids, key=lambda item_id: (-_community_attention(by_id[item_id]), position[item_id])),
                weight=0.25,
            ),
        ]
    )
    rrf_rank = {item.id: item.rank for item in fused}
    rrf_score = {item.id: item.score for item in fused}
    overlap = {item_id: _metadata_overlap_score(query, by_id[item_id]) for item_id in ids}
    ordered_ids = sorted(
        [item.id for item in fused],
        key=lambda item_id: (
            _is_archived(by_id[item_id]),
            -overlap[item_id],
            -_sort_tiebreaker(by_id[item_id], sort),
            -rrf_score[item_id],
            rrf_rank[item_id],
        ),
    )
    return [by_id[item_id] for item_id in ordered_ids]


def _deduplicate(
    candidates: List[SoftwareEcosystemCandidate],
) -> List[SoftwareEcosystemCandidate]:
    best: dict[str, SoftwareEcosystemCandidate] = {}
    for item in candidates:
        previous = best.get(item.id)
        if previous is None or _candidate_quality(item) > _candidate_quality(previous):
            best[item.id] = item
    return list(best.values())


def _candidate_quality(item: SoftwareEcosystemCandidate) -> float:
    return (
        item.raw_score
        + _metric(item, "popularity")
        + _metric(item, "maintenance")
        + _metric(item, "recent_activity")
    )


def _target_priority(item: SoftwareEcosystemCandidate, targets: List[str]) -> float:
    if len(targets) == 1:
        return 1.0 if item.candidate_type == targets[0] else 0.0
    if item.candidate_type in {"open_source_project", "package"}:
        return 1.0
    return 0.45


def _sort_weight(sort: str, signal: str) -> float:
    if sort == "relevance" and signal == "metadata_bm25":
        return 3.4
    if sort == "stars" and signal == "popularity":
        return 2.2
    if sort == "recent_activity" and signal == "recent_activity":
        return 2.0
    if sort == "maintenance" and signal == "maintenance":
        return 2.0
    if sort == "popularity" and signal == "popularity":
        return 2.0
    return {
        "metadata_bm25": 2.4,
        "popularity": 1.0,
        "maintenance": 1.0,
        "recent_activity": 0.8,
    }.get(signal, 0.5)


def _popularity_score(item: SoftwareEcosystemCandidate, sort: str) -> float:
    if sort == "stars" and item.candidate_type == "open_source_project":
        return _metric(item, "stars")
    return _metric(item, "popularity") + 0.0001 * item.raw_score


def _sort_tiebreaker(item: SoftwareEcosystemCandidate, sort: str) -> float:
    if sort == "stars":
        return _metric(item, "stars")
    if sort == "recent_activity":
        return _metric(item, "recent_activity")
    if sort == "maintenance":
        return _metric(item, "maintenance")
    if sort == "popularity":
        return _metric(item, "popularity")
    return _metadata_overlap_score("", item)


def _metric(item: SoftwareEcosystemCandidate, name: str) -> float:
    return float(item.metrics.get(name, 0.0))


def _is_archived(item: SoftwareEcosystemCandidate) -> bool:
    return item.metrics.get("archived") == 1.0


def _community_attention(item: SoftwareEcosystemCandidate) -> float:
    if item.candidate_type != "community_discussion":
        return 0.0
    return _metric(item, "points") + 0.5 * _metric(item, "comments_count")


def _metadata_overlap_score(query: str, item: SoftwareEcosystemCandidate) -> float:
    query_tokens = set(tokenize_for_bm25(query))
    if not query_tokens:
        return 0.0
    text_tokens = set(
        tokenize_for_bm25(
            f"{item.title} {item.summary or ''} {item.repository or ''} {item.language or ''}"
        )
    )
    return len(query_tokens & text_tokens) / max(1, len(query_tokens))

