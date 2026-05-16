from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple


@dataclass(frozen=True, slots=True)
class SearchUrlCandidate:
    id: str
    url: str
    canonical_url: str
    title: str
    snippet: str
    provider: str
    source_query: str
    query_language: str
    query_role: str
    original_rank: int
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RankedUrlCandidate:
    candidate: SearchUrlCandidate
    rrf_score: float
    rank: int
    rrf_sources: Tuple[str, ...]
