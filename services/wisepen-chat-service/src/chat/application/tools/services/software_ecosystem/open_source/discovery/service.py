from __future__ import annotations

import asyncio
from typing import List, Optional

from chat.application.tools.services.software_ecosystem import config
from chat.application.tools.services.software_ecosystem.common.cache_keys import (
    open_source_project_query_cache_key,
)
from chat.application.tools.services.software_ecosystem.common.errors import (
    InvalidSoftwareEcosystemQueryError,
)
from chat.application.tools.services.software_ecosystem.common.normalization import normalize_query
from chat.application.tools.services.software_ecosystem.common.scoring import (
    iso_datetime_recency_score,
)
from chat.application.tools.services.software_ecosystem.open_source.github.models import (
    GitHubRepositoryResult,
)
from chat.application.tools.services.software_ecosystem.open_source.github.service import (
    GitHubOpenSourceService,
)
from chat.application.tools.services.software_ecosystem.research.types import (
    SOFTWARE_ECOSYSTEM_SORTS,
)

from .cache import open_source_project_query_cache
from .models import OpenSourceProjectCandidate
from .ranking import rank_open_source_project_candidates


class OpenSourceProjectDiscoveryService:
    def __init__(self, github_service: Optional[GitHubOpenSourceService] = None) -> None:
        self._github = github_service or GitHubOpenSourceService()

    async def search(
        self,
        *,
        query: str,
        languages: Optional[List[str]],
        sort: str,
        limit: int,
        min_stars: Optional[int],
    ) -> List[OpenSourceProjectCandidate]:
        query = _validate_query(query)
        languages = _validate_languages(languages)
        _validate_sort(sort)
        _validate_limit(limit)
        _validate_min_stars(min_stars)

        cache_key = open_source_project_query_cache_key(
            query,
            languages,
            sort,
            limit,
            min_stars,
        )
        cached = open_source_project_query_cache.get(cache_key)
        if cached is not None:
            return cached

        github_sort = _github_sort(sort)
        search_queries = _build_search_queries(query, languages, min_stars)
        search_limit = max(limit, min(config.SOFTWARE_ECOSYSTEM_MAX_LIMIT, limit * 2))
        results = await asyncio.gather(
            *[
                self._github.search_repositories(
                    query=search_query,
                    sort=github_sort,
                    order="desc",
                    limit=search_limit,
                )
                for search_query in search_queries
            ],
            return_exceptions=True,
        )

        candidates: List[OpenSourceProjectCandidate] = []
        for result in results:
            if isinstance(result, Exception):
                raise result
            _total, _incomplete, repositories = result
            candidates.extend(_map_repository(item) for item in repositories)

        candidates = _deduplicate(candidates)
        candidates = [
            item
            for item in candidates
            if min_stars is None or item.stars >= min_stars
        ]
        ranked = _apply_sort(
            sort,
            rank_open_source_project_candidates(query, candidates),
        )[:limit]
        open_source_project_query_cache[cache_key] = ranked
        return ranked

    async def close(self) -> None:
        await self._github.close()


def _validate_query(query: str) -> str:
    if not isinstance(query, str):
        raise InvalidSoftwareEcosystemQueryError("query must be a string")
    normalized = normalize_query(query)
    if not normalized:
        raise InvalidSoftwareEcosystemQueryError("query must not be empty")
    return normalized


def _validate_languages(languages: Optional[List[str]]) -> Optional[List[str]]:
    if languages is None:
        return None
    if (
        not isinstance(languages, list)
        or not languages
        or any(not isinstance(item, str) or not item.strip() for item in languages)
    ):
        raise InvalidSoftwareEcosystemQueryError(
            "languages must be null or a non-empty string list"
        )
    return [item.strip() for item in languages]


def _validate_sort(sort: str) -> None:
    if sort not in SOFTWARE_ECOSYSTEM_SORTS:
        raise InvalidSoftwareEcosystemQueryError("sort must be a supported value")


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise InvalidSoftwareEcosystemQueryError("limit must be an integer")
    if limit < 1 or limit > config.SOFTWARE_ECOSYSTEM_MAX_LIMIT:
        raise InvalidSoftwareEcosystemQueryError(
            f"limit must be between 1 and {config.SOFTWARE_ECOSYSTEM_MAX_LIMIT}"
        )


def _validate_min_stars(min_stars: Optional[int]) -> None:
    if min_stars is None:
        return
    if isinstance(min_stars, bool) or not isinstance(min_stars, int) or min_stars < 0:
        raise InvalidSoftwareEcosystemQueryError("min_stars must be null or a non-negative integer")


def _github_sort(sort: str) -> Optional[str]:
    if sort in {"stars", "popularity"}:
        return "stars"
    if sort in {"recent_activity", "maintenance"}:
        return "updated"
    return None


def _build_search_queries(
    query: str,
    languages: Optional[List[str]],
    min_stars: Optional[int],
) -> List[str]:
    qualifiers: List[str] = []
    if min_stars is not None:
        qualifiers.append(f"stars:>={min_stars}")
    if not languages:
        return [" ".join([query, *qualifiers]).strip()]
    return [
        " ".join([query, f"language:{language}", *qualifiers]).strip()
        for language in languages
    ]


def _map_repository(repo: GitHubRepositoryResult) -> OpenSourceProjectCandidate:
    return OpenSourceProjectCandidate(
        full_name=repo.full_name,
        html_url=repo.html_url,
        description=repo.description,
        language=repo.language,
        stars=repo.stars,
        forks=repo.forks,
        open_issues=repo.open_issues,
        default_branch=repo.default_branch,
        updated_at=repo.updated_at,
        pushed_at=repo.pushed_at,
        license_name=repo.license_name,
        archived=repo.archived,
        source="github",
        raw_score=float(repo.stars),
        matched_terms=[],
    )


def _deduplicate(candidates: List[OpenSourceProjectCandidate]) -> List[OpenSourceProjectCandidate]:
    best: dict[str, OpenSourceProjectCandidate] = {}
    for item in candidates:
        key = item.full_name.lower()
        previous = best.get(key)
        if previous is None or item.stars > previous.stars:
            best[key] = item
    return list(best.values())


def _apply_sort(
    sort: str,
    candidates: List[OpenSourceProjectCandidate],
) -> List[OpenSourceProjectCandidate]:
    if sort in {"stars", "popularity"}:
        return sorted(candidates, key=lambda item: (-item.stars, item.full_name.lower()))
    if sort in {"recent_activity", "maintenance"}:
        return sorted(
            candidates,
            key=lambda item: (
                item.archived,
                -max(
                    iso_datetime_recency_score(item.pushed_at),
                    iso_datetime_recency_score(item.updated_at),
                ),
                item.full_name.lower(),
            ),
        )
    return candidates
