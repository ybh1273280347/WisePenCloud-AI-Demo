from __future__ import annotations

from typing import Any, Dict, List, Optional

from chat.application.tools.services.software_ecosystem.common.formatting import compact_text
from chat.application.tools.services.software_ecosystem.common.scoring import (
    bounded_log_score,
    iso_datetime_recency_score,
)
from chat.application.tools.services.software_ecosystem.open_source.github.models import (
    GitHubIssueResult,
    GitHubReleaseResult,
    GitHubRepositoryResult,
)

from .models import OpenSourceProjectProfile


def build_open_source_project_profile(
    *,
    repository: GitHubRepositoryResult,
    readme: Optional[Dict[str, Any]],
    releases: List[GitHubReleaseResult],
    issues: List[GitHubIssueResult],
) -> OpenSourceProjectProfile:
    readme_preview = None
    if readme is not None:
        readme_preview = compact_text(readme.get("content_preview"), max_chars=1200)
    recent_releases = [
        item.tag_name
        for item in releases
        if item.tag_name
    ][:5]
    activity_score = max(
        iso_datetime_recency_score(repository.pushed_at),
        iso_datetime_recency_score(repository.updated_at),
        max((iso_datetime_recency_score(item.published_at) for item in releases), default=0.0),
    )
    popularity_score = min(
        1.0,
        bounded_log_score(repository.stars)
        + 0.2 * bounded_log_score(repository.forks),
    )
    maintenance_score = activity_score
    if repository.license_name:
        maintenance_score += 0.1
    if repository.archived:
        maintenance_score -= 0.75
    maintenance_score = max(0.0, min(1.0, maintenance_score))
    return OpenSourceProjectProfile(
        full_name=repository.full_name,
        html_url=repository.html_url,
        description=repository.description,
        language=repository.language,
        stars=repository.stars,
        forks=repository.forks,
        open_issues=repository.open_issues,
        license_name=repository.license_name,
        archived=repository.archived,
        default_branch=repository.default_branch,
        updated_at=repository.updated_at,
        pushed_at=repository.pushed_at,
        readme_preview=readme_preview,
        recent_releases=recent_releases,
        issue_discussion_count=sum(item.comments for item in issues),
        maintenance_score=maintenance_score,
        popularity_score=popularity_score,
        activity_score=activity_score,
        relevance_score=0.0,
        evidence=_evidence(repository, readme, releases, issues),
    )


def _evidence(
    repository: GitHubRepositoryResult,
    readme: Optional[Dict[str, Any]],
    releases: List[GitHubReleaseResult],
    issues: List[GitHubIssueResult],
) -> List[str]:
    evidence = [
        f"GitHub repository loaded: stars={repository.stars}, forks={repository.forks}",
    ]
    if readme is not None and readme.get("content_preview"):
        evidence.append("README preview loaded")
    if releases:
        evidence.append(f"recent releases loaded: {len(releases)}")
    if issues:
        evidence.append(f"issue discussion signals loaded: {len(issues)}")
    if repository.archived:
        evidence.append("repository is archived")
    return evidence

