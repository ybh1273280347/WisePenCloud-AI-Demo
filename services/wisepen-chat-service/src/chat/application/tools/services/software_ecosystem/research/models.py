from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from chat.application.tools.services.software_ecosystem.community.models import (
    CommunityDiscussionSignal,
)
from chat.application.tools.services.software_ecosystem.open_source.hydration.models import (
    OpenSourceProjectProfile,
)
from chat.application.tools.services.software_ecosystem.packages.hydration.models import (
    PackageProfile,
)


@dataclass(frozen=True, slots=True)
class SoftwareEcosystemCandidate:
    id: str
    candidate_type: str
    source: str
    title: str
    url: str
    summary: Optional[str]
    ecosystem: Optional[str]
    package_name: Optional[str]
    repository: Optional[str]
    language: Optional[str]
    raw_score: float
    matched_terms: List[str]
    metrics: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SoftwareEcosystemResearchResult:
    query: str
    targets: List[str]
    recommended_projects: List[OpenSourceProjectProfile]
    recommended_packages: List[PackageProfile]
    community_discussions: List[CommunityDiscussionSignal]
    summary: str
    recommendations: List[str]
    caveats: List[str]
    evidence: List[str]

