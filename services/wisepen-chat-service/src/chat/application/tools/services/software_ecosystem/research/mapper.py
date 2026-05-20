from __future__ import annotations

from chat.application.algorithms.url import stable_hash
from chat.application.tools.services.software_ecosystem.common.normalization import (
    package_entity_id,
)
from chat.application.tools.services.software_ecosystem.common.scoring import (
    bounded_log_score,
    iso_datetime_recency_score,
)
from chat.application.tools.services.software_ecosystem.community.models import (
    CommunityDiscussionSignal,
)
from chat.application.tools.services.software_ecosystem.open_source.discovery.models import (
    OpenSourceProjectCandidate,
)
from chat.application.tools.services.software_ecosystem.packages.discovery.models import (
    PackageCandidate,
)

from .models import SoftwareEcosystemCandidate


def map_open_source_project_candidate(
    item: OpenSourceProjectCandidate,
) -> SoftwareEcosystemCandidate:
    full_name = item.full_name.lower()
    return SoftwareEcosystemCandidate(
        id=f"repo:github:{full_name}",
        candidate_type="open_source_project",
        source=item.source,
        title=item.full_name,
        url=item.html_url,
        summary=item.description,
        ecosystem=None,
        package_name=None,
        repository=item.full_name,
        language=item.language,
        raw_score=item.raw_score,
        matched_terms=item.matched_terms,
        metrics={
            "stars": float(item.stars),
            "forks": float(item.forks),
            "open_issues": float(item.open_issues),
            "popularity": bounded_log_score(item.stars) + 0.2 * bounded_log_score(item.forks),
            "maintenance": _project_maintenance(item),
            "recent_activity": max(
                iso_datetime_recency_score(item.pushed_at),
                iso_datetime_recency_score(item.updated_at),
            ),
            "archived": 1.0 if item.archived else 0.0,
        },
    )


def map_package_candidate(item: PackageCandidate) -> SoftwareEcosystemCandidate:
    return SoftwareEcosystemCandidate(
        id=package_entity_id(item.ecosystem, item.normalized_name),
        candidate_type="package",
        source=item.source,
        title=item.name,
        url=item.homepage_url or item.repository_url or "",
        summary=item.summary,
        ecosystem=item.ecosystem,
        package_name=item.name,
        repository=item.repository_url,
        language=None,
        raw_score=item.raw_score,
        matched_terms=item.matched_terms,
        metrics={
            "raw_score": float(item.raw_score),
            "popularity": min(1.0, float(item.raw_score) / 100.0),
            "maintenance": 0.5,
            "recent_activity": 0.5,
        },
    )


def map_community_discussion_candidate(
    item: CommunityDiscussionSignal,
) -> SoftwareEcosystemCandidate:
    return SoftwareEcosystemCandidate(
        id=f"community:{item.source}:{stable_hash(item.url)}",
        candidate_type="community_discussion",
        source=item.source,
        title=item.title,
        url=item.url,
        summary=item.summary,
        ecosystem=None,
        package_name=None,
        repository=None,
        language=None,
        raw_score=float(item.points or 0),
        matched_terms=item.matched_terms,
        metrics={
            "points": float(item.points or 0),
            "comments_count": float(item.comments_count or 0),
            "popularity": bounded_log_score(item.points) + 0.2 * bounded_log_score(item.comments_count),
            "maintenance": iso_datetime_recency_score(item.published_at),
            "recent_activity": iso_datetime_recency_score(item.published_at),
        },
    )


def _project_maintenance(item: OpenSourceProjectCandidate) -> float:
    score = max(
        iso_datetime_recency_score(item.pushed_at),
        iso_datetime_recency_score(item.updated_at),
    )
    if item.license_name:
        score += 0.1
    if item.archived:
        score -= 0.75
    return max(0.0, min(1.0, score))

