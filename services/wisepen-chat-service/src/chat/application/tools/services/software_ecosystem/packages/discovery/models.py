from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True, slots=True)
class PackageCandidate:
    ecosystem: str
    name: str
    normalized_name: str
    summary: Optional[str]
    repository_url: Optional[str]
    homepage_url: Optional[str]
    source: str
    raw_score: float
    matched_terms: List[str]

