from __future__ import annotations

from typing import List, Optional

from chat.application.tools.services.software_ecosystem.packages.hydration.models import (
    PackageProfile,
)

from .models import PackageComparisonResult
from .ranking import score_dimension

_DEFAULT_DIMENSIONS = [
    "relevance",
    "maturity",
    "maintenance",
    "popularity",
    "dependency_complexity",
    "release_freshness",
    "ecosystem_fit",
]


class PackageComparisonService:
    async def compare(
        self,
        *,
        query: str,
        candidates: List[PackageProfile],
        dimensions: Optional[List[str]],
    ) -> PackageComparisonResult:
        selected_dimensions = dimensions or _DEFAULT_DIMENSIONS
        winner_by_dimension: dict[str, str] = {}
        for dimension in selected_dimensions:
            if not candidates:
                continue
            winner = max(candidates, key=lambda item: score_dimension(item, dimension))
            winner_by_dimension[dimension] = f"{winner.ecosystem}:{winner.name}"

        recommendation = "No package candidates were hydrated."
        if candidates:
            best = max(
                candidates,
                key=lambda item: (
                    item.maintenance_score
                    + item.popularity_score
                    + item.dependency_complexity_score
                    + item.ecosystem_score
                ),
            )
            recommendation = f"Prefer {best.name} for {query} based on available ecosystem signals."

        return PackageComparisonResult(
            query=query,
            candidates=candidates,
            winner_by_dimension=winner_by_dimension,
            recommendation=recommendation,
            tradeoffs=_tradeoffs(candidates),
            evidence=[evidence for item in candidates for evidence in item.evidence[:2]],
        )


def _tradeoffs(candidates: List[PackageProfile]) -> List[str]:
    tradeoffs: List[str] = []
    for item in candidates[:5]:
        if item.deprecated:
            tradeoffs.append(f"{item.name} is deprecated")
        if item.direct_dependencies_count is not None and item.direct_dependencies_count > 50:
            tradeoffs.append(f"{item.name} has a large direct dependency surface")
        if item.repository_archived:
            tradeoffs.append(f"{item.name} repository is archived")
    return tradeoffs

