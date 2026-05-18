from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True, slots=True)
class RankedEvidence:
    content_id: str
    chunk_index: int
    score: float
    rank: int
    title: str = ""
    source: str = ""
    url: str = ""
    excerpt: str = ""

    @property
    def display_title(self) -> str:
        return self.title or self.source or self.url or "(untitled)"


@dataclass(frozen=True, slots=True)
class EvidenceRankResult:
    query: str
    evidence: Tuple[RankedEvidence, ...] = ()
    total_chunks_scanned: int = 0
    content_ids_found: Tuple[str, ...] = ()
    content_ids_missing: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()
