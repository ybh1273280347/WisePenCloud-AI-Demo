from __future__ import annotations

import asyncio
from typing import List, Optional

from chat.application.tools.services.software_ecosystem import config
from chat.application.tools.services.software_ecosystem.common.ecosystems import (
    validate_ecosystems,
)
from chat.application.tools.services.software_ecosystem.common.errors import (
    InvalidSoftwareEcosystemQueryError,
    SoftwareEcosystemHttpError,
)
from chat.application.tools.services.software_ecosystem.common.formatting import compact_text
from chat.application.tools.services.software_ecosystem.common.normalization import (
    normalize_package_name,
    normalize_query,
)
from chat.application.tools.services.software_ecosystem.open_source.github.service import (
    GitHubOpenSourceService,
)
from chat.application.tools.services.software_ecosystem.providers.ecosystems import (
    EcosystemsPackagesClient,
    map_ecosystems_candidate,
)
from chat.application.tools.services.software_ecosystem.providers.npm import (
    NpmRegistryClient,
    map_npm_candidate,
)

from .models import PackageCandidate
from .ranking import rank_package_candidates


class PackageDiscoveryService:
    def __init__(
        self,
        *,
        ecosystems_client: Optional[EcosystemsPackagesClient] = None,
        npm_client: Optional[NpmRegistryClient] = None,
        github_service: Optional[GitHubOpenSourceService] = None,
    ) -> None:
        self._ecosystems = ecosystems_client or EcosystemsPackagesClient()
        self._npm = npm_client or NpmRegistryClient()
        self._github = github_service or GitHubOpenSourceService()

    async def search(
        self,
        *,
        query: str,
        ecosystems: List[str],
        limit: int,
    ) -> List[PackageCandidate]:
        normalized_query = _validate_query(query)
        ecosystems = validate_ecosystems(ecosystems)
        _validate_limit(limit)

        tasks = [
            self._search_ecosystems(normalized_query, ecosystem, limit)
            for ecosystem in ecosystems
        ]
        if "npm" in ecosystems:
            tasks.append(self._search_npm(normalized_query, limit))
        tasks.append(self._search_github(normalized_query, ecosystems, limit))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        candidates: List[PackageCandidate] = []
        for result in results:
            if isinstance(result, Exception):
                if isinstance(result, SoftwareEcosystemHttpError):
                    continue
                raise result
            candidates.extend(result)

        return rank_package_candidates(normalized_query, candidates)[:limit]

    async def close(self) -> None:
        await self._ecosystems.close()
        await self._npm.close()
        await self._github.close()

    async def _search_ecosystems(
        self,
        query: str,
        ecosystem: str,
        limit: int,
    ) -> List[PackageCandidate]:
        payload = await self._ecosystems.search_packages(
            query=query,
            ecosystem=ecosystem,
            limit=limit,
        )
        return [
            candidate
            for item in payload
            if isinstance(item, dict)
            for candidate in [map_ecosystems_candidate(ecosystem, item)]
            if candidate is not None
        ]

    async def _search_npm(self, query: str, limit: int) -> List[PackageCandidate]:
        payload = await self._npm.search_packages(query=query, size=limit)
        objects = payload.get("objects") if isinstance(payload, dict) else None
        if not isinstance(objects, list):
            return []
        return [
            candidate
            for item in objects
            if isinstance(item, dict)
            for candidate in [map_npm_candidate(item)]
            if candidate is not None
        ]

    async def _search_github(
        self,
        query: str,
        ecosystems: List[str],
        limit: int,
    ) -> List[PackageCandidate]:
        _total, _incomplete, repos = await self._github.search_repositories(
            query=f"{query} package library",
            sort="stars",
            order="desc",
            limit=limit,
        )
        candidates: List[PackageCandidate] = []
        for repo in repos:
            ecosystem = _infer_ecosystem(repo.language, ecosystems)
            if ecosystem is None:
                continue
            name = _repo_name(repo.full_name)
            candidates.append(
                PackageCandidate(
                    ecosystem=ecosystem,
                    name=name,
                    normalized_name=normalize_package_name(ecosystem, name),
                    summary=compact_text(repo.description, max_chars=300),
                    repository_url=repo.html_url,
                    homepage_url=None,
                    source="github",
                    raw_score=float(repo.stars),
                    matched_terms=[],
                )
            )
        return candidates


def _validate_query(query: str) -> str:
    if not isinstance(query, str):
        raise InvalidSoftwareEcosystemQueryError("query must be a string")
    normalized = normalize_query(query)
    if not normalized:
        raise InvalidSoftwareEcosystemQueryError("query must not be empty")
    return normalized


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise InvalidSoftwareEcosystemQueryError("limit must be an integer")
    if limit < 1 or limit > config.SOFTWARE_ECOSYSTEM_MAX_LIMIT:
        raise InvalidSoftwareEcosystemQueryError(
            f"limit must be between 1 and {config.SOFTWARE_ECOSYSTEM_MAX_LIMIT}"
        )


def _infer_ecosystem(language: Optional[str], ecosystems: List[str]) -> Optional[str]:
    if language == "Python" and "pypi" in ecosystems:
        return "pypi"
    if language in {"JavaScript", "TypeScript"} and "npm" in ecosystems:
        return "npm"
    if len(ecosystems) == 1:
        return ecosystems[0]
    return None


def _repo_name(full_name: str) -> str:
    return full_name.split("/", 1)[-1] if "/" in full_name else full_name
