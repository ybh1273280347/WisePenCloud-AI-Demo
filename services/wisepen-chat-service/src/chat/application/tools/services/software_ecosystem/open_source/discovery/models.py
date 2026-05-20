from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True, slots=True)
class OpenSourceProjectCandidate:
    full_name: str
    html_url: str
    description: Optional[str]
    language: Optional[str]
    stars: int
    forks: int
    open_issues: int
    default_branch: Optional[str]
    updated_at: Optional[str]
    pushed_at: Optional[str]
    license_name: Optional[str]
    archived: bool
    source: str
    raw_score: float
    matched_terms: List[str]

