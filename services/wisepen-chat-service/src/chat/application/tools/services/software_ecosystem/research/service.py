from __future__ import annotations

import asyncio
from typing import Awaitable, List, Optional

from chat.application.tools.services.software_ecosystem import config
from chat.application.tools.services.software_ecosystem.common.ecosystems import (
    SUPPORTED_PACKAGE_ECOSYSTEMS,
    validate_ecosystems,
)
from chat.application.tools.services.software_ecosystem.common.errors import (
    InvalidSoftwareEcosystemQueryError,
    PackageNotFoundError,
    PackageVersionNotFoundError,
    SoftwareEcosystemHttpError,
)
from chat.application.tools.services.software_ecosystem.common.normalization import normalize_query
from chat.application.tools.services.software_ecosystem.community.models import (
    CommunityDiscussionSignal,
)
from chat.application.tools.services.software_ecosystem.community.service import (
    CommunityDiscussionService,
)
from chat.application.tools.services.software_ecosystem.open_source.discovery.models import (
    OpenSourceProjectCandidate,
)
from chat.application.tools.services.software_ecosystem.open_source.discovery.service import (
    OpenSourceProjectDiscoveryService,
)
from chat.application.tools.services.software_ecosystem.open_source.hydration.models import (
    OpenSourceProjectProfile,
)
from chat.application.tools.services.software_ecosystem.open_source.hydration.service import (
    OpenSourceProjectHydrationService,
)
from chat.application.tools.services.software_ecosystem.packages.discovery.models import (
    PackageCandidate,
)
from chat.application.tools.services.software_ecosystem.packages.discovery.service import (
    PackageDiscoveryService,
)
from chat.application.tools.services.software_ecosystem.packages.hydration.models import (
    PackageProfile,
)
from chat.application.tools.services.software_ecosystem.packages.hydration.service import (
    PackageHydrationService,
)

from .mapper import (
    map_community_discussion_candidate,
    map_open_source_project_candidate,
    map_package_candidate,
)
from .models import SoftwareEcosystemCandidate, SoftwareEcosystemResearchResult
from .ranking import rank_software_ecosystem_candidates
from .types import (
    PACKAGE_HYDRATION_DEPTHS,
    SOFTWARE_ECOSYSTEM_SORTS,
    SOFTWARE_ECOSYSTEM_TARGETS,
)


class SoftwareEcosystemResearchService:
    def __init__(
        self,
        *,
        open_source_project_discovery: Optional[OpenSourceProjectDiscoveryService] = None,
        open_source_project_hydration: Optional[OpenSourceProjectHydrationService] = None,
        package_discovery: Optional[PackageDiscoveryService] = None,
        package_hydration: Optional[PackageHydrationService] = None,
        community_discussion: Optional[CommunityDiscussionService] = None,
    ) -> None:
        self._open_source_project_discovery = (
            open_source_project_discovery or OpenSourceProjectDiscoveryService()
        )
        self._open_source_project_hydration = (
            open_source_project_hydration or OpenSourceProjectHydrationService()
        )
        self._package_discovery = package_discovery or PackageDiscoveryService()
        self._package_hydration = package_hydration or PackageHydrationService()
        self._community_discussion = community_discussion or CommunityDiscussionService()

    async def research(
        self,
        *,
        query: str,
        targets: List[str],
        ecosystems: Optional[List[str]],
        languages: Optional[List[str]],
        sort: str,
        limit: int,
        min_stars: Optional[int],
        package_hydration_depth: str,
    ) -> SoftwareEcosystemResearchResult:
        query = _validate_query(query)
        targets = _validate_targets(targets)
        ecosystems = _validate_ecosystems(ecosystems, targets)
        languages = _validate_languages(languages)
        _validate_sort(sort)
        _validate_limit(limit)
        _validate_min_stars(min_stars)
        package_hydration_depth = _validate_package_hydration_depth(package_hydration_depth)

        discovery_tasks: list[tuple[str, Awaitable]] = []
        if "open_source_project" in targets:
            discovery_tasks.append(
                (
                    "open_source_project",
                    self._open_source_project_discovery.search(
                        query=query,
                        languages=languages,
                        sort=sort,
                        limit=limit,
                        min_stars=min_stars,
                    ),
                )
            )
        if "package" in targets:
            discovery_tasks.append(
                (
                    "package",
                    self._package_discovery.search(
                        query=query,
                        ecosystems=ecosystems or list(SUPPORTED_PACKAGE_ECOSYSTEMS),
                        limit=limit,
                    ),
                )
            )
        if "community_discussion" in targets:
            discovery_tasks.append(
                (
                    "community_discussion",
                    self._community_discussion.search(query=query, limit=limit),
                )
            )

        discovery_results = await asyncio.gather(
            *(task for _target, task in discovery_tasks),
            return_exceptions=True,
        )
        candidates: list[SoftwareEcosystemCandidate] = []
        community_by_id: dict[str, CommunityDiscussionSignal] = {}
        caveats: list[str] = []

        for (target, _task), result in zip(discovery_tasks, discovery_results):
            if isinstance(result, Exception):
                if isinstance(result, SoftwareEcosystemHttpError):
                    caveats.append(f"{target} discovery skipped: {result}")
                    continue
                raise result
            target_candidates, community_map = _normalize_candidates(target, result)
            candidates.extend(target_candidates)
            community_by_id.update(community_map)

        ranked_candidates = rank_software_ecosystem_candidates(
            query=query,
            targets=targets,
            sort=sort,
            candidates=candidates,
        )
        hydrated_projects, project_caveats = await self._hydrate_project_candidates(
            ranked_candidates=ranked_candidates,
            sort=sort,
            limit=limit,
        )
        hydrated_packages, package_caveats = await self._hydrate_package_candidates(
            ranked_candidates=ranked_candidates,
            package_hydration_depth=package_hydration_depth,
            limit=limit,
        )
        community_discussions = _extract_community_discussions(
            ranked_candidates=ranked_candidates,
            community_by_id=community_by_id,
            limit=limit,
        )
        caveats.extend(project_caveats)
        caveats.extend(package_caveats)

        return _build_result(
            query=query,
            targets=targets,
            projects=hydrated_projects,
            packages=hydrated_packages,
            community_discussions=community_discussions,
            caveats=caveats,
        )

    async def close(self) -> None:
        await self._open_source_project_discovery.close()
        await self._open_source_project_hydration.close()
        await self._package_discovery.close()
        await self._package_hydration.close()
        await self._community_discussion.close()

    async def _hydrate_project_candidates(
        self,
        *,
        ranked_candidates: List[SoftwareEcosystemCandidate],
        sort: str,
        limit: int,
    ) -> tuple[List[OpenSourceProjectProfile], List[str]]:
        project_candidates = [
            item
            for item in ranked_candidates
            if item.candidate_type == "open_source_project"
            and item.repository
            and "/" in item.repository
        ][:limit]
        results = await asyncio.gather(
            *[
                self._open_source_project_hydration.hydrate(
                    owner=item.repository.split("/", 1)[0],
                    repo=item.repository.split("/", 1)[1],
                    include_readme=True,
                    include_releases=sort in {"recent_activity", "maintenance"},
                    include_issues=sort in {"recent_activity", "maintenance"},
                )
                for item in project_candidates
            ],
            return_exceptions=True,
        )
        profiles: List[OpenSourceProjectProfile] = []
        caveats: List[str] = []
        for candidate, result in zip(project_candidates, results):
            if isinstance(result, Exception):
                if isinstance(result, SoftwareEcosystemHttpError):
                    caveats.append(f"{candidate.repository} hydration skipped: {result}")
                    continue
                raise result
            profiles.append(result)
        return profiles, caveats

    async def _hydrate_package_candidates(
        self,
        *,
        ranked_candidates: List[SoftwareEcosystemCandidate],
        package_hydration_depth: str,
        limit: int,
    ) -> tuple[List[PackageProfile], List[str]]:
        package_candidates = [
            item
            for item in ranked_candidates
            if item.candidate_type == "package"
            and item.ecosystem is not None
            and item.package_name is not None
        ][: min(limit, config.SOFTWARE_ECOSYSTEM_TOP_HYDRATION_LIMIT)]
        results = await asyncio.gather(
            *[
                self._package_hydration.hydrate(
                    ecosystem=item.ecosystem or "",
                    package_name=item.package_name or "",
                    version=None,
                    package_hydration_depth=package_hydration_depth,
                )
                for item in package_candidates
            ],
            return_exceptions=True,
        )
        profiles: List[PackageProfile] = []
        caveats: List[str] = []
        for candidate, result in zip(package_candidates, results):
            if isinstance(result, Exception):
                if isinstance(
                    result,
                    (PackageNotFoundError, PackageVersionNotFoundError, SoftwareEcosystemHttpError),
                ):
                    caveats.append(f"{candidate.ecosystem}:{candidate.package_name} hydration skipped: {result}")
                    continue
                raise result
            profiles.append(result)
        return profiles, caveats


def _normalize_candidates(target: str, result) -> tuple[list[SoftwareEcosystemCandidate], dict[str, CommunityDiscussionSignal]]:
    candidates: list[SoftwareEcosystemCandidate] = []
    community_by_id: dict[str, CommunityDiscussionSignal] = {}
    if target == "open_source_project":
        candidates = [
            map_open_source_project_candidate(item)
            for item in result
            if isinstance(item, OpenSourceProjectCandidate)
        ]
        return candidates, community_by_id
    if target == "package":
        candidates = [
            map_package_candidate(item)
            for item in result
            if isinstance(item, PackageCandidate)
        ]
        return candidates, community_by_id
    for item in result:
        if not isinstance(item, CommunityDiscussionSignal):
            continue
        candidate = map_community_discussion_candidate(item)
        candidates.append(candidate)
        community_by_id[candidate.id] = item
    return candidates, community_by_id


def _extract_community_discussions(
    *,
    ranked_candidates: List[SoftwareEcosystemCandidate],
    community_by_id: dict[str, CommunityDiscussionSignal],
    limit: int,
) -> List[CommunityDiscussionSignal]:
    discussions: List[CommunityDiscussionSignal] = []
    for candidate in ranked_candidates:
        if candidate.candidate_type != "community_discussion":
            continue
        signal = community_by_id.get(candidate.id)
        if signal is not None:
            discussions.append(signal)
        if len(discussions) >= limit:
            break
    return discussions


def _build_result(
    *,
    query: str,
    targets: List[str],
    projects: List[OpenSourceProjectProfile],
    packages: List[PackageProfile],
    community_discussions: List[CommunityDiscussionSignal],
    caveats: List[str],
) -> SoftwareEcosystemResearchResult:
    recommendations: List[str] = []
    if projects:
        recommendations.append(
            "Open-source projects: "
            + ", ".join(item.full_name for item in projects[:3])
        )
    if packages:
        recommendations.append(
            "Packages: "
            + ", ".join(f"{item.ecosystem}:{item.name}" for item in packages[:3])
        )
    if community_discussions:
        recommendations.append(
            f"Community discussions: {len(community_discussions)} signal(s) returned"
        )
    if not recommendations:
        recommendations.append("No software ecosystem candidates were fully resolved.")

    evidence = [
        evidence
        for item in projects
        for evidence in item.evidence[:3]
    ] + [
        evidence
        for item in packages
        for evidence in item.evidence[:3]
    ]
    return SoftwareEcosystemResearchResult(
        query=query,
        targets=targets,
        recommended_projects=projects,
        recommended_packages=packages,
        community_discussions=community_discussions,
        summary=(
            f"Found {len(projects)} open-source project(s), "
            f"{len(packages)} package(s), and "
            f"{len(community_discussions)} community discussion(s) for '{query}'."
        ),
        recommendations=recommendations,
        caveats=caveats or _default_caveats(projects, packages),
        evidence=evidence,
    )


def _validate_query(query: str) -> str:
    if not isinstance(query, str):
        raise InvalidSoftwareEcosystemQueryError("query must be a string")
    normalized = normalize_query(query)
    if not normalized:
        raise InvalidSoftwareEcosystemQueryError("query must not be empty")
    return normalized


def _validate_targets(targets: List[str]) -> List[str]:
    if (
        isinstance(targets, (str, bytes))
        or not isinstance(targets, list)
        or not targets
        or any(not isinstance(item, str) for item in targets)
    ):
        raise InvalidSoftwareEcosystemQueryError("targets must be a non-empty string list")
    invalid = [item for item in targets if item not in SOFTWARE_ECOSYSTEM_TARGETS]
    if invalid:
        raise InvalidSoftwareEcosystemQueryError(f"unsupported target: {invalid[0]}")
    return list(dict.fromkeys(targets))


def _validate_ecosystems(
    ecosystems: Optional[List[str]],
    targets: List[str],
) -> Optional[List[str]]:
    if ecosystems is None:
        return list(SUPPORTED_PACKAGE_ECOSYSTEMS) if "package" in targets else None
    return validate_ecosystems(ecosystems)


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


def _validate_package_hydration_depth(value: str) -> str:
    if not isinstance(value, str) or value not in PACKAGE_HYDRATION_DEPTHS:
        raise InvalidSoftwareEcosystemQueryError(
            "package_hydration_depth must be light, standard, or deep"
        )
    return value


def _default_caveats(
    projects: List[OpenSourceProjectProfile],
    packages: List[PackageProfile],
) -> List[str]:
    caveats: List[str] = []
    if any(item.archived for item in projects):
        caveats.append("At least one project is archived.")
    if any(item.deprecated for item in packages):
        caveats.append("At least one package is deprecated.")
    if any(item.signals.advisories_count for item in packages):
        caveats.append("At least one package has advisory signals; review security metadata before adopting.")
    return caveats
