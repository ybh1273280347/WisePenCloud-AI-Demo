from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True, slots=True)
class OpenSourceProjectProfile:
    full_name: str
    html_url: str
    description: Optional[str]
    language: Optional[str]
    stars: int
    forks: int
    open_issues: int
    license_name: Optional[str]
    archived: bool
    default_branch: Optional[str]
    updated_at: Optional[str]
    pushed_at: Optional[str]
    readme_preview: Optional[str]
    recent_releases: List[str]
    issue_discussion_count: int
    maintenance_score: float
    popularity_score: float
    activity_score: float
    relevance_score: float
    evidence: List[str]

