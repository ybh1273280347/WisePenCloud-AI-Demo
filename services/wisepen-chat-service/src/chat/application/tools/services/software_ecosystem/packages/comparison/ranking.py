from __future__ import annotations

from chat.application.tools.services.software_ecosystem.packages.hydration.models import (
    PackageProfile,
)


def score_dimension(profile: PackageProfile, dimension: str) -> float:
    if dimension == "maintenance":
        return profile.maintenance_score
    if dimension == "popularity":
        return profile.popularity_score
    if dimension == "dependency_complexity":
        return profile.dependency_complexity_score
    if dimension == "release_freshness":
        return profile.maintenance_score
    if dimension == "ecosystem_fit":
        return profile.ecosystem_score
    if dimension == "maturity":
        return (profile.popularity_score * 0.6) + (profile.maintenance_score * 0.4)
    return (
        profile.maintenance_score
        + profile.popularity_score
        + profile.dependency_complexity_score
        + profile.ecosystem_score
    ) / 4.0

