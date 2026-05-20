from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from common.logger import log_event

from . import ranking
from .client import GitHubClient
from .mapper import map_issue, map_readme, map_release, map_repository
from .models import GitHubIssueResult, GitHubReleaseResult, GitHubRepositoryResult


class GitHubOpenSourceService:
    def __init__(self, client: Optional[GitHubClient] = None) -> None:
        self._client = client or GitHubClient()

    async def search_repositories(
        self,
        *,
        query: str,
        sort: Optional[str],
        order: str,
        limit: int,
    ) -> Tuple[int, bool, List[GitHubRepositoryResult]]:
        payload = await self._client.search_repositories(
            query=query,
            sort=sort,
            order=order,
            per_page=limit,
        )
        items = _require_list(payload.get("items"), "GitHub repository search items")
        return (
            int(payload.get("total_count") or 0),
            bool(payload.get("incomplete_results")),
            ranking.rank_repositories(
                query,
                [
                    map_repository(item)
                    for item in items[:limit]
                    if isinstance(item, dict)
                ],
            ),
        )

    async def search_issues(
        self,
        *,
        query: str,
        sort: Optional[str],
        order: str,
        limit: int,
    ) -> Tuple[int, bool, List[GitHubIssueResult]]:
        payload = await self._client.search_issues(
            query=query,
            sort=sort,
            order=order,
            per_page=limit,
        )
        items = _require_list(payload.get("items"), "GitHub issue search items")
        return (
            int(payload.get("total_count") or 0),
            bool(payload.get("incomplete_results")),
            ranking.rank_issues(
                query,
                [map_issue(item) for item in items[:limit] if isinstance(item, dict)],
            ),
        )

    async def get_repository(self, *, owner: str, repo: str) -> GitHubRepositoryResult:
        payload = await self._client.get_repository(owner=owner, repo=repo)
        if not isinstance(payload, dict):
            raise RuntimeError("GitHub repository endpoint returned non-object JSON.")
        return map_repository(payload)

    async def get_readme(self, *, owner: str, repo: str) -> Dict[str, Any]:
        payload = await self._client.get_readme(owner=owner, repo=repo)
        if not isinstance(payload, dict):
            raise RuntimeError("GitHub README endpoint returned non-object JSON.")
        return map_readme(owner, repo, payload)

    async def get_releases(
        self,
        *,
        owner: str,
        repo: str,
        limit: int,
    ) -> List[GitHubReleaseResult]:
        payload = await self._client.get_releases(
            owner=owner,
            repo=repo,
            per_page=limit,
        )
        return [
            map_release(item) for item in payload[:limit] if isinstance(item, dict)
        ]

    async def close(self) -> None:
        await self._client.close()
        log_event("GitHubOpenSourceService 关闭")


def _require_list(value: Any, label: str) -> List[Any]:
    if not isinstance(value, list):
        raise RuntimeError(f"{label} returned non-list JSON.")
    return value

