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
    scores: Dict[str, float] = defaultdict(float)
    sources: Dict[str, List[str]] = defaultdict(list)

    for ranked_list in ranked_lists:
        for rank_index, item_id in enumerate(ranked_list.ids):
            scores[item_id] += ranked_list.weight / (k + rank_index + 1)
            sources[item_id].append(ranked_list.name)

    ordered_ids = sorted(scores, key=scores.get, reverse=True)

    return [
        RrfRankedItem(
            id=item_id,
            score=scores[item_id],
            rank=rank,
            sources=tuple(sources[item_id]),
        )
        for rank, item_id in enumerate(ordered_ids)
    ]
