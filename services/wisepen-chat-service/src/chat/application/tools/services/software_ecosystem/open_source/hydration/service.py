from __future__ import annotations

import asyncio
from typing import Optional

from chat.application.tools.services.software_ecosystem.common.cache_keys import (
    open_source_project_profile_cache_key,
)
from chat.application.tools.services.software_ecosystem.common.errors import (
    InvalidSoftwareEcosystemQueryError,
)
from chat.application.tools.services.software_ecosystem.open_source.github.models import (
    GitHubIssueResult,
    GitHubReleaseResult,
)
from chat.application.tools.services.software_ecosystem.open_source.github.service import (
    GitHubOpenSourceService,
)

from .cache import open_source_project_profile_cache
from .mapper import build_open_source_project_profile
from .models import OpenSourceProjectProfile


class OpenSourceProjectHydrationService:
    def __init__(self, github_service: Optional[GitHubOpenSourceService] = None) -> None:
        self._github = github_service or GitHubOpenSourceService()

    async def hydrate(
        self,
        *,
        owner: str,
        repo: str,
        include_readme: bool,
        include_releases: bool,
        include_issues: bool,
    ) -> OpenSourceProjectProfile:
        owner = _validate_repo_part(owner, "owner")
        repo = _validate_repo_part(repo, "repo")
        _validate_bool(include_readme, "include_readme")
        _validate_bool(include_releases, "include_releases")
        _validate_bool(include_issues, "include_issues")

        cache_key = (
            f"{open_source_project_profile_cache_key(owner, repo)}:"
            f"{include_readme}:{include_releases}:{include_issues}"
        )
        cached = open_source_project_profile_cache.get(cache_key)
        if cached is not None:
            return cached

        repository = await self._github.get_repository(owner=owner, repo=repo)
        readme_task = (
            asyncio.create_task(self._github.get_readme(owner=owner, repo=repo))
            if include_readme
            else None
        )
        releases_task = (
            asyncio.create_task(self._github.get_releases(owner=owner, repo=repo, limit=5))
            if include_releases
            else None
        )
        issues_task = (
            asyncio.create_task(
                self._github.search_issues(
                    query=f"repo:{owner}/{repo} is:issue",
                    sort="comments",
                    order="desc",
                    limit=10,
                )
            )
            if include_issues
            else None
        )

        readme = await readme_task if readme_task is not None else None
        releases: list[GitHubReleaseResult] = (
            await releases_task if releases_task is not None else []
        )
        issues: list[GitHubIssueResult] = []
        if issues_task is not None:
            _total, _incomplete, issues = await issues_task

        profile = build_open_source_project_profile(
            repository=repository,
            readme=readme,
            releases=releases,
            issues=issues,
        )
        open_source_project_profile_cache[cache_key] = profile
        return profile

    async def close(self) -> None:
        await self._github.close()


def _validate_repo_part(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidSoftwareEcosystemQueryError(f"{label} must be a non-empty string")
    return value.strip()


def _validate_bool(value: bool, label: str) -> None:
    if not isinstance(value, bool):
        raise InvalidSoftwareEcosystemQueryError(f"{label} must be bool")

