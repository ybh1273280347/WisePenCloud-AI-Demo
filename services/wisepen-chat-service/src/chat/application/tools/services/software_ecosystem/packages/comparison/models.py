from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from chat.application.tools.services.software_ecosystem.packages.hydration.models import (
    PackageProfile,
)


@dataclass(frozen=True, slots=True)
class PackageComparisonResult:
    query: str
    candidates: List[PackageProfile]
    winner_by_dimension: Dict[str, str]
    recommendation: str
    tradeoffs: List[str]
    evidence: List[str]

