from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

from chat.application.tools.services.software_ecosystem import config
from chat.application.tools.services.software_ecosystem.common.http_client import (
    SoftwareEcosystemHttpClient,
)
from chat.core.config.app_settings import settings


class GitHubClient:
    def __init__(
        self,
        http: Optional[SoftwareEcosystemHttpClient] = None,
        *,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        api_version: Optional[str] = None,
    ) -> None:
        self._http = http or SoftwareEcosystemHttpClient(
            timeout=config.SOFTWARE_ECOSYSTEM_TIMEOUT_SECONDS
        )
        self._base_url = (base_url or settings.GITHUB_API_BASE_URL).rstrip("/")
        self._token = token if token is not None else settings.GITHUB_TOKEN
        self._api_version = api_version or settings.GITHUB_API_VERSION

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self._api_version,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def search_repositories(
        self,
        *,
        query: str,
        sort: Optional[str],
        order: str,
        per_page: int,
        page: int = 1,
    ) -> Dict[str, Any]:
        return await self._http.get_json(
            f"{self._base_url}/search/repositories",
            params={
                "q": query,
                "sort": sort,
                "order": order,
                "per_page": per_page,
                "page": page,
            },
            headers=self._headers(),
        )

    async def search_issues(
        self,
        *,
        query: str,
        sort: Optional[str],
        order: str,
        per_page: int,
        page: int = 1,
    ) -> Dict[str, Any]:
        return await self._http.get_json(
            f"{self._base_url}/search/issues",
            params={
                "q": query,
                "sort": sort,
                "order": order,
                "per_page": per_page,
                "page": page,
            },
            headers=self._headers(),
        )

    async def get_repository(self, *, owner: str, repo: str) -> Dict[str, Any]:
        return await self._http.get_json(
            f"{self._base_url}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}",
            headers=self._headers(),
        )

    async def get_readme(self, *, owner: str, repo: str) -> Dict[str, Any]:
        return await self._http.get_json(
            f"{self._base_url}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/readme",
            headers=self._headers(),
        )

    async def get_releases(
        self,
        *,
        owner: str,
        repo: str,
        per_page: int,
        page: int = 1,
    ) -> List[Any]:
        payload = await self._http.get_json(
            f"{self._base_url}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/releases",
            params={"per_page": per_page, "page": page},
            headers=self._headers(),
        )
        if not isinstance(payload, list):
            raise RuntimeError("GitHub releases endpoint returned non-list JSON.")
        return payload

    async def close(self) -> None:
        await self._http.close()

