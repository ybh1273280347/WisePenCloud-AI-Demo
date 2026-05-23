from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from chat.application.algorithms.ranking.models import RankedList, RrfRankedItem

RRF_K = 60


def weighted_rrf(
    ranked_lists: List[RankedList],
    *,
    k: int = RRF_K,
) -> List[RrfRankedItem]:
    if k <= 0:
        raise ValueError("k must be > 0")

    scores: Dict[str, float] = defaultdict(float)
    sources: Dict[str, List[str]] = defaultdict(list)
    first_seen_order: Dict[str, int] = {}

    for ranked_list in ranked_lists:
        if ranked_list.weight < 0:
            raise ValueError("RankedList.weight must be >= 0")

        seen_in_list = set()
        for rank_index, item_id in enumerate(ranked_list.ids):
            if item_id in seen_in_list:
                continue
            seen_in_list.add(item_id)

            if item_id not in first_seen_order:
                first_seen_order[item_id] = len(first_seen_order)

            scores[item_id] += ranked_list.weight / (k + rank_index + 1)
            if ranked_list.name not in sources[item_id]:
                sources[item_id].append(ranked_list.name)

    ordered_ids = sorted(
        scores,
        key=lambda item_id: (-scores[item_id], first_seen_order[item_id]),
    )

    return [
        RrfRankedItem(
            id=item_id,
            score=scores[item_id],
            rank=rank,
            sources=tuple(sources[item_id]),
        )
        for rank, item_id in enumerate(ordered_ids)
    ]
