from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional, Tuple

from chat.application.tools.services.code_search.common.formatting import compact_text
from common.logger import log_event

from . import ranking
from .client import GitHubClient
from .models import GitHubIssueResult, GitHubReleaseResult, GitHubRepositoryResult


class GitHubSearchService:
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
                    _map_repository(item)
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
                [_map_issue(item) for item in items[:limit] if isinstance(item, dict)],
            ),
        )

    async def get_repository(self, *, owner: str, repo: str) -> GitHubRepositoryResult:
        payload = await self._client.get_repository(owner=owner, repo=repo)
        if not isinstance(payload, dict):
            raise RuntimeError("GitHub repository endpoint returned non-object JSON.")
        return _map_repository(payload)

    async def get_readme(self, *, owner: str, repo: str) -> Dict[str, Any]:
        payload = await self._client.get_readme(owner=owner, repo=repo)
        if not isinstance(payload, dict):
            raise RuntimeError("GitHub README endpoint returned non-object JSON.")

        content_preview = None
        content = payload.get("content")
        encoding = payload.get("encoding")
        if isinstance(content, str) and encoding == "base64":
            try:
                decoded = base64.b64decode(content, validate=False).decode(
                    "utf-8",
                    errors="replace",
                )
                content_preview = compact_text(decoded, max_chars=2000)
            except Exception:
                content_preview = None

        return {
            "repo": f"{owner}/{repo}",
            "encoding": encoding,
            "size": payload.get("size"),
            "download_url": payload.get("download_url"),
            "content_preview": content_preview,
        }

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
            _map_release(item) for item in payload[:limit] if isinstance(item, dict)
        ]

    async def close(self) -> None:
        await self._client.close()
        log_event("GitHubSearchService 关闭")


def _require_list(value: Any, label: str) -> List[Any]:
    if not isinstance(value, List):
        raise RuntimeError(f"{label} returned non-list JSON.")
    return value


def _map_repository(item: Dict[str, Any]) -> GitHubRepositoryResult:
    license_info = item.get("license")
    license_name = None
    if isinstance(license_info, dict):
        license_name = license_info.get("spdx_id") or license_info.get("name")

    return GitHubRepositoryResult(
        full_name=str(item.get("full_name") or ""),
        html_url=str(item.get("html_url") or ""),
        description=compact_text(item.get("description"), max_chars=500),
        language=item.get("language"),
        stars=int(item.get("stargazers_count") or 0),
        forks=int(item.get("forks_count") or item.get("forks") or 0),
        open_issues=int(item.get("open_issues_count") or item.get("open_issues") or 0),
        default_branch=item.get("default_branch"),
        updated_at=item.get("updated_at"),
        pushed_at=item.get("pushed_at"),
        license_name=license_name,
        archived=bool(item.get("archived")),
    )


def _map_issue(item: Dict[str, Any]) -> GitHubIssueResult:
    return GitHubIssueResult(
        title=str(item.get("title") or ""),
        html_url=str(item.get("html_url") or ""),
        repository_url=str(item.get("repository_url") or ""),
        state=str(item.get("state") or ""),
        comments=int(item.get("comments") or 0),
        created_at=item.get("created_at"),
        updated_at=item.get("updated_at"),
        is_pull_request=isinstance(item.get("pull_request"), dict),
        body_preview=compact_text(item.get("body"), max_chars=800),
    )


def _map_release(item: Dict[str, Any]) -> GitHubReleaseResult:
    return GitHubReleaseResult(
        name=item.get("name"),
        tag_name=str(item.get("tag_name") or ""),
        html_url=str(item.get("html_url") or ""),
        published_at=item.get("published_at"),
        prerelease=bool(item.get("prerelease")),
        draft=bool(item.get("draft")),
        body_preview=compact_text(item.get("body"), max_chars=1000),
    )
