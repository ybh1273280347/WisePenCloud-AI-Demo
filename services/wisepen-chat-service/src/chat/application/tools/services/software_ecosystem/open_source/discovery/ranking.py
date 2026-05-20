from __future__ import annotations

from typing import List

from chat.application.tools.services.software_ecosystem.open_source.github.ranking import (
    rank_repositories,
)
from chat.application.tools.services.software_ecosystem.open_source.github.models import (
    GitHubRepositoryResult,
)

from .models import OpenSourceProjectCandidate


def rank_open_source_project_candidates(
    query: str,
    candidates: List[OpenSourceProjectCandidate],
) -> List[OpenSourceProjectCandidate]:
    if len(candidates) < 2:
        return candidates
    repos = [
        GitHubRepositoryResult(
            full_name=item.full_name,
            html_url=item.html_url,
            description=item.description,
            language=item.language,
            stars=item.stars,
            forks=item.forks,
            open_issues=item.open_issues,
            default_branch=item.default_branch,
            updated_at=item.updated_at,
            pushed_at=item.pushed_at,
            license_name=item.license_name,
            archived=item.archived,
        )
        for item in candidates
    ]
    ranked_repos = rank_repositories(query, repos)
    candidate_by_name = {item.full_name: item for item in candidates}
    return [candidate_by_name[item.full_name] for item in ranked_repos]

