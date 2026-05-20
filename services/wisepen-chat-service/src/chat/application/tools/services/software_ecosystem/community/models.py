from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True, slots=True)
class CommunityDiscussionSignal:
    source: str
    title: str
    url: str
    published_at: Optional[str]
    points: Optional[int]
    comments_count: Optional[int]
    summary: Optional[str]
    matched_terms: List[str]

