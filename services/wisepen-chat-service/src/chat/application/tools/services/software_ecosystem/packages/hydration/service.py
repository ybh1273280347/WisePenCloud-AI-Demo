from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from common.logger import log_event

from chat.application.tools.services.software_ecosystem import config
from chat.application.tools.services.software_ecosystem.common.ecosystems import (
    ECOSYSTEM_TO_DEPS_DEV_SYSTEM,
    validate_ecosystem,
)
from chat.application.tools.services.software_ecosystem.common.errors import (
    InvalidSoftwareEcosystemQueryError,
    PackageNotFoundError,
    PackageVersionNotFoundError,
    SoftwareEcosystemHttpError,
)
from chat.application.tools.services.software_ecosystem.common.normalization import (
    extract_github_repo,
    normalize_package_name,
)
from chat.application.tools.services.software_ecosystem.common.scoring import (
    bounded_log_score,
    iso_datetime_recency_score,
)
from chat.application.tools.services.software_ecosystem.open_source.github.service import (
    GitHubOpenSourceService,
)
from chat.application.tools.services.software_ecosystem.providers.deps_dev import (
    DepsDevClient,
    extract_advisories,
    extract_licenses,
    extract_versions,
    summarize_dependency_graph,
)
from chat.application.tools.services.software_ecosystem.providers.npm import (
    NpmRegistryClient,
    map_npm_metadata,
)
from chat.application.tools.services.software_ecosystem.providers.pypi import (
    PyPIRegistryClient,
    map_pypi_metadata,
)
from chat.application.tools.services.software_ecosystem.research.types import (
    PACKAGE_HYDRATION_DEPTHS,
)

from .cache import latest_pointer_cache, package_profile_cache
from .mapper import (
    find_default_version,
    find_version_summary,
    latest_version,
    recent_version_strings,
    select_version,
)
from .models import (
    DependencyGraphSummary,
    PackageHydrationSignals,
    PackageProfile,
    RegistryMetadata,
)


class PackageHydrationService:
    def __init__(
        self,
        *,
        deps_dev_client: Optional[DepsDevClient] = None,
        pypi_client: Optional[PyPIRegistryClient] = None,
        npm_client: Optional[NpmRegistryClient] = None,
        github_service: Optional[GitHubOpenSourceService] = None,
    ) -> None:
        self._deps_dev = deps_dev_client or DepsDevClient()
        self._pypi = pypi_client or PyPIRegistryClient()
        self._npm = npm_client or NpmRegistryClient()
        self._github = github_service or GitHubOpenSourceService()

    async def hydrate(
        self,
        *,
        ecosystem: str,
        package_name: str,
        version: Optional[str],
        package_hydration_depth: str,
    ) -> PackageProfile:
        started = time.monotonic()
        ecosystem = validate_ecosystem(ecosystem)
        package_name = _validate_package_name(package_name)
        _validate_version(version)
        package_hydration_depth = _validate_package_hydration_depth(package_hydration_depth)
        load_standard = package_hydration_depth in {"standard", "deep"}
        load_dependency_graph = package_hydration_depth == "deep"

        normalized_name = normalize_package_name(ecosystem, package_name)
        cache_key = (
            f"{ecosystem}:{normalized_name}:{version or 'latest'}:"
            f"{package_hydration_depth}"
        )
        cached = package_profile_cache.get(cache_key)
        if cached is not None:
            return cached

        system = ECOSYSTEM_TO_DEPS_DEV_SYSTEM[ecosystem]
        try:
            package_payload = await self._deps_dev.get_package(system=system, name=package_name)
        except SoftwareEcosystemHttpError as e:
            if e.status_code == 404:
                raise PackageNotFoundError(f"{ecosystem} package not found: {package_name}") from e
            raise
        if not isinstance(package_payload, dict):
            raise RuntimeError("deps.dev package endpoint returned non-object JSON.")

        versions = extract_versions(package_payload)
        if version is not None and find_version_summary(versions, version) is None:
            raise PackageVersionNotFoundError(f"{package_name}@{version} not found")

        try:
            selected_version = select_version(version, versions)
        except ValueError as e:
            raise PackageVersionNotFoundError(f"cannot determine version for {package_name}") from e

        latest_pointer_cache[f"{ecosystem}:{normalized_name}"] = latest_version(versions)
        version_payload: Dict[str, Any] = {}
        if load_standard:
            version_payload = await self._deps_dev.get_version(
                system=system,
                name=package_name,
                version=selected_version,
            )
            if not isinstance(version_payload, dict):
                raise RuntimeError("deps.dev version endpoint returned non-object JSON.")

        registry_task = None
        requirements_task = None
        if load_standard:
            registry_task = asyncio.create_task(
                self._load_registry_metadata(
                    ecosystem=ecosystem,
                    package_name=package_name,
                    selected_version=selected_version,
                )
            )
            requirements_task = asyncio.create_task(
                self._load_requirements_count(
                    system=system,
                    package_name=package_name,
                    version=selected_version,
                )
            )
        dependency_task = None
        if load_dependency_graph:
            dependency_task = asyncio.create_task(
                self._load_dependency_graph(
                    system=system,
                    package_name=package_name,
                    version=selected_version,
                )
            )

        registry_metadata = await registry_task if registry_task is not None else None
        requirements_count = await requirements_task if requirements_task is not None else 0
        dependency_graph = await dependency_task if dependency_task is not None else None

        selected_summary = find_version_summary(versions, selected_version)
        published_at = (
            _as_str(version_payload.get("publishedAt"))
            or _as_str(version_payload.get("published_at"))
            or (selected_summary.published_at if selected_summary else None)
        )
        deprecated = bool(
            version_payload.get("isDeprecated")
            or version_payload.get("deprecated")
            or (selected_summary.is_deprecated if selected_summary else False)
            or (registry_metadata.deprecated if registry_metadata else False)
        )
        deprecated_reason = (
            _as_str(version_payload.get("deprecatedReason"))
            or _string_reason(version_payload.get("deprecated"))
            or (selected_summary.deprecated_reason if selected_summary else None)
            or (registry_metadata.deprecated if registry_metadata else None)
        )
        advisories = extract_advisories(version_payload)
        repo_metadata = (
            await self._load_repository_metadata(registry_metadata, package_payload, version_payload)
            if load_standard
            else {}
        )

        evidence = _build_evidence(
            registry_metadata=registry_metadata,
            dependency_graph=dependency_graph,
            advisories_count=len(advisories),
            repo_metadata=repo_metadata,
        )
        profile = PackageProfile(
            ecosystem=ecosystem,
            name=package_name,
            normalized_name=normalized_name,
            selected_version=selected_version,
            latest_version=find_default_version(versions) or latest_version(versions),
            published_at=published_at,
            summary=registry_metadata.summary if registry_metadata else None,
            description_preview=registry_metadata.description_preview if registry_metadata else None,
            homepage_url=registry_metadata.homepage_url if registry_metadata else None,
            repository_url=registry_metadata.repository_url if registry_metadata else None,
            license=registry_metadata.license if registry_metadata else None,
            deprecated=deprecated,
            deprecated_reason=deprecated_reason,
            direct_dependencies_count=(
                dependency_graph.direct_dependencies_count
                if dependency_graph is not None
                else _declared_dependency_count(registry_metadata)
            ),
            transitive_dependencies_count=(
                dependency_graph.transitive_dependencies_count if dependency_graph is not None else None
            ),
            recent_versions=recent_version_strings(
                versions,
                limit=config.SOFTWARE_ECOSYSTEM_RECENT_VERSION_LIMIT,
            ),
            repository_stars=repo_metadata.get("stars"),
            repository_forks=repo_metadata.get("forks"),
            repository_open_issues=repo_metadata.get("open_issues"),
            repository_pushed_at=repo_metadata.get("pushed_at"),
            repository_archived=repo_metadata.get("archived"),
            maintenance_score=_maintenance_score(
                published_at=published_at,
                repository_pushed_at=repo_metadata.get("pushed_at"),
                deprecated=deprecated,
                archived=repo_metadata.get("archived"),
            ),
            popularity_score=_popularity_score(repo_metadata),
            dependency_complexity_score=_dependency_complexity_score(dependency_graph, registry_metadata),
            ecosystem_score=_ecosystem_score(registry_metadata, advisories_count=len(advisories)),
            evidence=evidence,
            signals=PackageHydrationSignals(
                available_versions_count=len(versions),
                advisories_count=len(advisories),
                requirements_count=requirements_count,
                licenses=extract_licenses(version_payload),
            ),
        )
        package_profile_cache[cache_key] = profile
        log_event(
            "software_ecosystem package hydration",
            ecosystem=ecosystem,
            package_name=package_name,
            selected_version=selected_version,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        return profile

    async def close(self) -> None:
        await self._deps_dev.close()
        await self._pypi.close()
        await self._npm.close()
        await self._github.close()

    async def _load_registry_metadata(
        self,
        *,
        ecosystem: str,
        package_name: str,
        selected_version: str,
    ) -> Optional[RegistryMetadata]:
        try:
            if ecosystem == "pypi":
                payload = await self._pypi.get_project(package_name)
                return map_pypi_metadata(payload)

            payload = await self._npm.get_package(package_name)
            return map_npm_metadata(payload, selected_version=selected_version)
        except SoftwareEcosystemHttpError as e:
            if e.status_code == 404:
                return RegistryMetadata(
                    summary=None,
                    description_preview=None,
                    homepage_url=None,
                    repository_url=None,
                    license=None,
                    requires_python=None,
                    engines=None,
                    declared_dependencies={},
                    vulnerabilities=[],
                    deprecated=None,
                    unavailable_reason="registry 404",
                )
            raise

    async def _load_requirements_count(
        self,
        *,
        system: str,
        package_name: str,
        version: str,
    ) -> int:
        try:
            payload = await self._deps_dev.get_requirements(
                system=system,
                name=package_name,
                version=version,
            )
        except SoftwareEcosystemHttpError as e:
            if e.status_code == 404:
                return 0
            raise
        if not isinstance(payload, dict):
            raise RuntimeError("deps.dev requirements endpoint returned non-object JSON.")
        requirements = payload.get("requirements") or payload.get("nodes") or []
        return len(requirements) if isinstance(requirements, list) else 0

    async def _load_dependency_graph(
        self,
        *,
        system: str,
        package_name: str,
        version: str,
    ) -> DependencyGraphSummary:
        payload = await self._deps_dev.get_dependencies(
            system=system,
            name=package_name,
            version=version,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("deps.dev dependencies endpoint returned non-object JSON.")
        return summarize_dependency_graph(payload)

    async def _load_repository_metadata(
        self,
        registry_metadata: Optional[RegistryMetadata],
        package_payload: Dict[str, Any],
        version_payload: Dict[str, Any],
    ) -> dict[str, Any]:
        repo_url = registry_metadata.repository_url if registry_metadata else None
        repo_url = repo_url or _find_github_url(package_payload) or _find_github_url(version_payload)
        repo_ref = extract_github_repo(repo_url)
        if repo_ref is None:
            return {}
        owner, repo_name = repo_ref
        try:
            repo = await self._github.get_repository(owner=owner, repo=repo_name)
        except SoftwareEcosystemHttpError as e:
            if e.status_code == 404:
                return {}
            raise
        return {
            "stars": repo.stars,
            "forks": repo.forks,
            "open_issues": repo.open_issues,
            "pushed_at": repo.pushed_at,
            "archived": repo.archived,
        }


def _validate_package_name(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidSoftwareEcosystemQueryError("package_name must be a string")
    text = value.strip()
    if not text:
        raise InvalidSoftwareEcosystemQueryError("package_name must not be empty")
    return text


def _validate_version(value: Optional[str]) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise InvalidSoftwareEcosystemQueryError("version must be a non-empty string when provided")


def _validate_package_hydration_depth(value: str) -> str:
    if not isinstance(value, str) or value not in PACKAGE_HYDRATION_DEPTHS:
        raise InvalidSoftwareEcosystemQueryError(
            "package_hydration_depth must be light, standard, or deep"
        )
    return value


def _declared_dependency_count(metadata: Optional[RegistryMetadata]) -> Optional[int]:
    if metadata is None:
        return None
    dependencies = metadata.declared_dependencies
    return len(dependencies) if isinstance(dependencies, (dict, list)) else None


def _maintenance_score(
    *,
    published_at: Optional[str],
    repository_pushed_at: Optional[str],
    deprecated: bool,
    archived: Optional[bool],
) -> float:
    score = max(
        iso_datetime_recency_score(repository_pushed_at),
        iso_datetime_recency_score(published_at),
    )
    if deprecated:
        score -= 0.5
    if archived:
        score -= 0.75
    return max(0.0, min(1.0, score))


def _popularity_score(repo_metadata: dict[str, Any]) -> float:
    stars = repo_metadata.get("stars")
    forks = repo_metadata.get("forks")
    return max(
        bounded_log_score(stars if isinstance(stars, int) else None),
        0.35 * bounded_log_score(forks if isinstance(forks, int) else None),
    )


def _dependency_complexity_score(
    graph: Optional[DependencyGraphSummary],
    metadata: Optional[RegistryMetadata],
) -> float:
    dependency_count = None
    if graph is not None:
        dependency_count = graph.total_nodes
    else:
        dependency_count = _declared_dependency_count(metadata)
    if dependency_count is None:
        return 0.5
    if dependency_count <= 5:
        return 1.0
    if dependency_count >= 100:
        return 0.0
    return max(0.0, 1.0 - (dependency_count / 100.0))


def _ecosystem_score(metadata: Optional[RegistryMetadata], *, advisories_count: int) -> float:
    score = 0.5
    if metadata is not None and metadata.license:
        score += 0.2
    if metadata is not None and metadata.repository_url:
        score += 0.2
    if advisories_count:
        score -= min(0.4, advisories_count * 0.08)
    return max(0.0, min(1.0, score))


def _build_evidence(
    *,
    registry_metadata: Optional[RegistryMetadata],
    dependency_graph: Optional[DependencyGraphSummary],
    advisories_count: int,
    repo_metadata: dict[str, Any],
) -> list[str]:
    evidence: list[str] = []
    if registry_metadata is None:
        evidence.append("registry metadata unavailable")
    elif registry_metadata.unavailable_reason:
        evidence.append(f"registry metadata unavailable: {registry_metadata.unavailable_reason}")
    else:
        evidence.append("registry metadata loaded")
    if dependency_graph is not None:
        evidence.append(
            f"dependency graph loaded: direct={dependency_graph.direct_dependencies_count}, "
            f"transitive={dependency_graph.transitive_dependencies_count}"
        )
    if advisories_count:
        evidence.append(f"deps.dev reported {advisories_count} advisory signal(s)")
    if repo_metadata:
        evidence.append(
            f"GitHub repository loaded: stars={repo_metadata.get('stars')}, "
            f"forks={repo_metadata.get('forks')}"
        )
    return evidence


def _find_github_url(payload: Dict[str, Any]) -> Optional[str]:
    links = payload.get("links")
    if isinstance(links, list):
        for link in links:
            if isinstance(link, dict):
                url = link.get("url") or link.get("label")
                if isinstance(url, str) and "github.com" in url:
                    return url
            elif isinstance(link, str) and "github.com" in link:
                return link

    projects = payload.get("relatedProjects")
    if isinstance(projects, list):
        for project in projects:
            if isinstance(project, dict):
                url = project.get("url")
                if isinstance(url, str) and "github.com" in url:
                    return url
    return None


def _as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_reason(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return _as_str(value)
