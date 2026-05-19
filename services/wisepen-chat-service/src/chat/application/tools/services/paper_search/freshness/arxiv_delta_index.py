from __future__ import annotations

from typing import Dict, List

from ..models import ArxivDeltaRecord, PaperPointer
from ..title_fingerprint import title_fingerprint


class ArxivDeltaIndex:
    def __init__(self) -> None:
        self._records: Dict[str, ArxivDeltaRecord] = {}

    def upsert_many(self, records: List[ArxivDeltaRecord]) -> None:
        for record in records:
            self._records[record.arxiv_id] = record

    def search(
        self,
        query: str,
        *,
        max_results: int,
    ) -> List[PaperPointer]:
        tokens = _query_tokens(query)
        scored: list[tuple[float, ArxivDeltaRecord]] = []

        for record in self._records.values():
            score = _score_record(record, tokens)
            if score <= 0.0:
                continue
            scored.append((score, record))

        scored.sort(key=lambda item: item[0], reverse=True)

        return [
            _record_to_pointer(record, rank=rank, discovery_score=score)
            for rank, (score, record) in enumerate(scored[:max_results])
        ]


def _record_to_pointer(
    record: ArxivDeltaRecord,
    *,
    rank: int,
    discovery_score: float,
) -> PaperPointer:
    return PaperPointer(
        title=record.title,
        url=record.abs_url,
        source_name="arxiv_delta_index",
        rank=rank,
        rewrite_query="arxiv_delta_index",
        pointer_type="arxiv",
        snippet=record.abstract,
        published_date=record.published_date,
        highlights=[record.abstract] if record.abstract else [],
        extracted_arxiv_id=record.arxiv_id,
        title_fingerprint=title_fingerprint(record.title),
        discovery_score=discovery_score,
    )


def _query_tokens(query: str) -> List[str]:
    return [token for token in query.lower().split() if len(token) >= 2]


def _score_record(record: ArxivDeltaRecord, tokens: List[str]) -> float:
    haystack = " ".join(
        part
        for part in [record.title, record.abstract or "", " ".join(record.categories)]
        if part
    ).lower()

    if not tokens:
        return 0.0

    matched = sum(1 for token in tokens if token in haystack)
    return matched / len(tokens)
