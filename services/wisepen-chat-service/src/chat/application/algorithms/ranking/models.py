from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True, slots=True)
class RankedDocument:
    id: str
    score: float
    rank: int


@dataclass(frozen=True, slots=True)
class Bm25RankResult:
    ranked: Tuple[RankedDocument, ...]
    cache_hit: bool = False
    build_index_elapsed_ms: int = 0


@dataclass(frozen=True, slots=True)
class FieldedDocument:
    id: str
    fields: Dict[str, str]


@dataclass(frozen=True, slots=True)
class RankedList:
    name: str
    ids: List[str]
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class RrfRankedItem:
    id: str
    score: float
    rank: int
    sources: Tuple[str, ...]
