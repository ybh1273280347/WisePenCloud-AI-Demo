from __future__ import annotations

from typing import List

from chat.application.algorithms.ranking import (
    FieldedDocument,
    RankedList,
    rank_fielded_bm25,
    tokenize_for_bm25,
    weighted_rrf,
)
from chat.application.tools.services.software_ecosystem.common.scoring import (
    bounded_log_score,
    iso_datetime_recency_score,
)

from .models import CommunityDiscussionSignal

_FIELD_WEIGHTS = {
    "title": 3.0,
    "summary": 1.0,
}


def rank_community_discussions(
    query: str,
    signals: List[CommunityDiscussionSignal],
) -> List[CommunityDiscussionSignal]:
    if len(signals) < 2:
        return signals
    ids = [f"discussion:{index}" for index, _item in enumerate(signals)]
    by_id = dict(zip(ids, signals))
    position = {item_id: index for index, item_id in enumerate(ids)}
    docs = [
        FieldedDocument(
            id=item_id,
            fields={"title": item.title, "summary": item.summary or ""},
        )
        for item_id, item in zip(ids, signals)
    ]
    overlap = {
        item_id: _overlap_score(query, item)
        for item_id, item in zip(ids, signals)
    }
    fused = weighted_rrf(
        [
            RankedList(name="source_original", ids=ids, weight=0.25),
            RankedList(
                name="title_bm25",
                ids=rank_fielded_bm25(query, docs, _FIELD_WEIGHTS),
                weight=2.0,
            ),
            RankedList(
                name="points",
                ids=sorted(
                    ids,
                    key=lambda item_id: (-bounded_log_score(by_id[item_id].points), position[item_id]),
                ),
                weight=0.6,
            ),
            RankedList(
                name="comments",
                ids=sorted(
                    ids,
                    key=lambda item_id: (
                        -bounded_log_score(by_id[item_id].comments_count),
                        position[item_id],
                    ),
                ),
                weight=0.6,
            ),
            RankedList(
                name="recency",
                ids=sorted(
                    ids,
                    key=lambda item_id: (
                        -iso_datetime_recency_score(by_id[item_id].published_at),
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
        key=lambda item_id: (-overlap[item_id], -rrf_score[item_id], rrf_rank[item_id]),
    )
    return [by_id[item_id] for item_id in ordered_ids]


def _overlap_score(query: str, signal: CommunityDiscussionSignal) -> float:
    query_tokens = set(tokenize_for_bm25(query))
    if not query_tokens:
        return 0.0
    text_tokens = set(tokenize_for_bm25(f"{signal.title} {signal.summary or ''}"))
    return len(query_tokens & text_tokens) / max(1, len(query_tokens))

