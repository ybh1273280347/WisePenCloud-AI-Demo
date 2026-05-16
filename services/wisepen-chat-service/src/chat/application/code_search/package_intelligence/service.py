from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from chat.application.code_search.common.errors import VerticalSearchHttpError
from chat.application.code_search.common.formatting import compact_text

from . import config
from .deps_dev_client import DepsDevClient
from .models import (
    DependencyGraphSummary,
    DepsDevIntelligence,
    PackageIntelligenceResult,
    PackageVersionSummary,
    RegistryMetadata,
    ScorecardSummary,
)
from .registry_clients import NpmRegistryClient, PyPIRegistryClient
from .scorecard_client import ScorecardClient
from common.logger import log_event, log_fail


_ECOSYSTEM_TO_DEPSDEV_SYSTEM = {
    "pypi": "PYPI",
    "npm": "NPM",
}

_GITHUB_RE = re.compile(r"github\.com[:/](?P<owner>[^/\s]+)/(?P<repo>[^/\s#?]+)")


class CannotDeterminePackageVersion(RuntimeError):
    pass


class PackageIntelligenceService:
    def __init__(
        self,
        *,
        deps_dev_client: Optional[DepsDevClient] = None,
        pypi_client: Optional[PyPIRegistryClient] = None,
        npm_client: Optional[NpmRegistryClient] = None,
        scorecard_client: Optional[ScorecardClient] = None,
    ) -> None:
        self._deps_dev = deps_dev_client or DepsDevClient()
        self._pypi = pypi_client or PyPIRegistryClient()
        self._npm = npm_client or NpmRegistryClient()
        self._scorecard = scorecard_client or ScorecardClient()

    async def lookup(
        self,
        *,
        ecosystem: str,
        package_name: str,
        version: Optional[str],
        include_dependencies: bool,
        include_scorecard: bool,
    ) -> PackageIntelligenceResult:
        total_started = time.monotonic()
        system = _ECOSYSTEM_TO_DEPSDEV_SYSTEM[ecosystem]
        package_payload = await self._deps_dev.get_package(system=system, name=package_name)
        if not isinstance(package_payload, dict):
            raise RuntimeError("deps.dev package endpoint returned non-object JSON.")

        versions = _extract_versions(package_payload)
        selected_version = _select_version(version, versions)
        version_payload = await self._deps_dev.get_version(
            system=system,
            name=package_name,
            version=selected_version,
        )
        if not isinstance(version_payload, dict):
            raise RuntimeError("deps.dev version endpoint returned non-object JSON.")

        registry_task = asyncio.create_task(
            self._timed(
                "registry",
                self._load_registry_metadata(
                    ecosystem=ecosystem,
                    package_name=package_name,
                    selected_version=selected_version,
                ),
            )
        )
        requirements_task = asyncio.create_task(
            self._timed(
                "requirements",
                self._load_requirements_count(
                    system=system,
                    package_name=package_name,
                    version=selected_version,
                ),
            )
        )
        dependency_task = None
        if include_dependencies:
            dependency_task = asyncio.create_task(
                self._timed(
                    "dependencies",
                    self._load_dependency_graph(
                        system=system,
                        package_name=package_name,
                        version=selected_version,
                    ),
                )
            )

        try:
            registry_result = await registry_task
        except Exception:
            await asyncio.gather(
                requirements_task,
                *((dependency_task,) if dependency_task is not None else ()),
                return_exceptions=True,
            )
            raise

        registry_metadata, registry_elapsed_ms = registry_result

        scorecard_task = None
        if include_scorecard:
            scorecard_task = asyncio.create_task(
                self._timed(
                    "scorecard",
                    self._load_scorecard(
                        registry_metadata=registry_metadata,
                        package_payload=package_payload,
                        version_payload=version_payload,
                    ),
                )
            )

        dependency_graph = None
        dependencies_elapsed_ms = 0
        remaining_results = await asyncio.gather(
            requirements_task,
            *((dependency_task,) if dependency_task is not None else ()),
            return_exceptions=True,
        )
        for item in remaining_results:
            if isinstance(item, Exception):
                if scorecard_task is not None:
                    scorecard_task.cancel()
                    await asyncio.gather(scorecard_task, return_exceptions=True)
                raise item

        requirements_count, requirements_elapsed_ms = remaining_results[0]
        if dependency_task is not None:
            dependency_graph, dependencies_elapsed_ms = remaining_results[1]

        default_version = _find_default_version(versions)
        recent_versions = _recent_versions(versions)
        selected_summary = _find_version_summary(versions, selected_version)
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

        deps_dev = DepsDevIntelligence(
            available_versions_count=len(versions),
            default_version=default_version,
            selected_version=selected_version,
            published_at=published_at,
            licenses=_extract_licenses(version_payload),
            advisory_count=len(_extract_advisories(version_payload)),
            advisories=_extract_advisories(version_payload)[:5],
            requirements_count=requirements_count,
            resolved_dependencies_count=(
                dependency_graph.total_nodes if dependency_graph is not None else None
            ),
            recent_versions=recent_versions,
        )

        scorecard = None
        scorecard_elapsed_ms = 0
        if scorecard_task is not None:
            try:
                scorecard, scorecard_elapsed_ms = await scorecard_task
            except Exception as e:
                log_fail(
                    "package_intelligence scorecard",
                    repr(e),
                    ecosystem=ecosystem,
                    package_name=package_name,
                    selected_version=selected_version,
                )
                scorecard = None

        total_elapsed_ms = int((time.monotonic() - total_started) * 1000)
        log_event(
            "package_intelligence 耗时",
            ecosystem=ecosystem,
            package_name=package_name,
            selected_version=selected_version,
            registry_elapsed_ms=registry_elapsed_ms,
            requirements_elapsed_ms=requirements_elapsed_ms,
            dependencies_elapsed_ms=dependencies_elapsed_ms,
            scorecard_elapsed_ms=scorecard_elapsed_ms,
            total_elapsed_ms=total_elapsed_ms,
        )

        return PackageIntelligenceResult(
            ecosystem=ecosystem,
            package_name=package_name,
            selected_version=selected_version,
            default_version=default_version,
            published_at=published_at,
            deprecated=deprecated,
            deprecated_reason=deprecated_reason,
            registry_metadata=registry_metadata,
            deps_dev=deps_dev,
            dependency_graph=dependency_graph,
            scorecard=scorecard,
            scorecard_requested=include_scorecard,
        )

    async def close(self) -> None:
        await self._deps_dev.close()
        await self._pypi.close()
        await self._npm.close()
        await self._scorecard.close()
        log_event("PackageIntelligenceService 关闭")

    async def _timed(self, name: str, coro):
        started = time.monotonic()
        try:
            result = await coro
        finally:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            log_event(
                "package_intelligence 子请求耗时",
                request=name,
                elapsed_ms=elapsed_ms,
            )
        return result, elapsed_ms

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
                return _map_pypi_metadata(payload)

            payload = await self._npm.get_package(package_name)
            return _map_npm_metadata(payload, selected_version=selected_version)
        except VerticalSearchHttpError as e:
            if e.status_code == 404:
                return RegistryMetadata(
                    summary=None,
                    description_preview=None,
                    homepage=None,
                    repository=None,
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
        except VerticalSearchHttpError as e:
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
        return _summarize_dependency_graph(payload)

    async def _load_scorecard(
        self,
        *,
        registry_metadata: Optional[RegistryMetadata],
        package_payload: Dict[str, Any],
        version_payload: Dict[str, Any],
    ) -> Optional[ScorecardSummary]:
        repo_url = None
        if registry_metadata is not None:
            repo_url = registry_metadata.repository
        repo_url = repo_url or _find_github_url(package_payload) or _find_github_url(version_payload)
        repo = _extract_github_repo(repo_url)
        if repo is None:
            return None

        owner, name = repo
        try:
            payload = await self._scorecard.get_project(owner=owner, repo=name)
        except VerticalSearchHttpError as e:
            if e.status_code == 404:
                return ScorecardSummary(
                    repo=f"{owner}/{name}",
                    score=None,
                    date=None,
                    checks=[],
                    unavailable_reason="scorecard 404",
                )
            raise

        if not isinstance(payload, dict):
            raise RuntimeError("OpenSSF Scorecard API returned non-object JSON.")

        checks = payload.get("checks")
        if not isinstance(checks, list):
            checks = []
        key_checks = [
            {
                "name": check.get("name"),
                "score": check.get("score"),
                "reason": compact_text(check.get("reason"), max_chars=200),
            }
            for check in checks[:8]
            if isinstance(check, dict)
        ]
        return ScorecardSummary(
            repo=f"{owner}/{name}",
            score=_as_float(payload.get("score")),
            date=_as_str(payload.get("date")),
            checks=key_checks,
        )


def _extract_versions(payload: Dict[str, Any]) -> List[PackageVersionSummary]:
    raw_versions = payload.get("versions")
    if not isinstance(raw_versions, list):
        return []
    versions = [_map_deps_version(item) for item in raw_versions if isinstance(item, dict)]
    return [item for item in versions if item.version]


def _map_deps_version(item: Dict[str, Any]) -> PackageVersionSummary:
    version_key = item.get("versionKey")
    version = None
    if isinstance(version_key, dict):
        version = version_key.get("version")
    version = version or item.get("version")
    deprecated_value = item.get("isDeprecated") or item.get("deprecated")
    deprecated_reason = None
    if isinstance(item.get("deprecated"), str):
        deprecated_reason = item.get("deprecated")
    deprecated_reason = deprecated_reason or _as_str(item.get("deprecatedReason"))
    return PackageVersionSummary(
        version=str(version or ""),
        published_at=_as_str(item.get("publishedAt") or item.get("published_at")),
        is_default=bool(item.get("isDefault")),
        is_deprecated=bool(deprecated_value),
        deprecated_reason=deprecated_reason,
    )


def _select_version(
    requested_version: Optional[str],
    versions: List[PackageVersionSummary],
) -> str:
    if requested_version:
        return requested_version

    default_version = _find_default_version(versions)
    if default_version:
        return default_version

    candidates = [item for item in versions if item.published_at and not item.is_deprecated]
    candidates.sort(key=lambda item: item.published_at or "", reverse=True)
    if candidates:
        return candidates[0].version

    raise CannotDeterminePackageVersion("Cannot determine package version.")


def _find_default_version(versions: List[PackageVersionSummary]) -> Optional[str]:
    for item in versions:
        if item.is_default:
            return item.version
    return None


def _find_version_summary(
    versions: List[PackageVersionSummary],
    version: str,
) -> Optional[PackageVersionSummary]:
    for item in versions:
        if item.version == version:
            return item
    return None


def _recent_versions(versions: List[PackageVersionSummary]) -> List[PackageVersionSummary]:
    with_dates = [item for item in versions if item.published_at]
    with_dates.sort(key=lambda item: item.published_at or "", reverse=True)
    return with_dates[: config.PACKAGE_INTELLIGENCE_RECENT_VERSION_LIMIT]


def _extract_licenses(payload: Dict[str, Any]) -> List[str]:
    values: List[str] = []
    for key in ("licenses", "licenseDetails"):
        raw = payload.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, dict):
                license_name = item.get("spdxId") or item.get("license") or item.get("name")
                if license_name:
                    values.append(str(license_name))
    return sorted(set(values))


def _extract_advisories(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = payload.get("advisoryKeys") or payload.get("advisories") or []
    if not isinstance(raw, list):
        return []
    advisories: List[Dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            advisories.append(
                {
                    "id": item.get("id") or item.get("ghsaId") or item.get("osvId"),
                    "url": item.get("url"),
                    "title": compact_text(item.get("title"), max_chars=200),
                }
            )
        elif isinstance(item, str):
            advisories.append({"id": item})
    return advisories


def _map_pypi_metadata(payload: Dict[str, Any]) -> RegistryMetadata:
    if not isinstance(payload, dict):
        raise RuntimeError("PyPI registry returned non-object JSON.")
    info = payload.get("info") or {}
    if not isinstance(info, dict):
        raise RuntimeError("PyPI registry info returned non-object JSON.")

    project_urls = info.get("project_urls")
    repository = _pick_project_url(project_urls, ("source", "repository", "code", "github"))
    homepage = _as_str(info.get("home_page")) or _pick_project_url(
        project_urls,
        ("homepage", "home", "documentation", "docs"),
    )
    declared_dependencies = info.get("requires_dist")
    if not isinstance(declared_dependencies, list):
        declared_dependencies = []

    vulnerabilities = payload.get("vulnerabilities")
    if not isinstance(vulnerabilities, list):
        vulnerabilities = []

    return RegistryMetadata(
        summary=compact_text(info.get("summary"), max_chars=300),
        description_preview=compact_text(info.get("description"), max_chars=1000),
        homepage=homepage,
        repository=repository,
        license=_as_str(info.get("license_expression")) or _as_str(info.get("license")),
        requires_python=_as_str(info.get("requires_python")),
        engines=None,
        declared_dependencies=[str(item) for item in declared_dependencies[:20]],
        vulnerabilities=[item for item in vulnerabilities[:10] if isinstance(item, dict)],
        deprecated=None,
    )


def _map_npm_metadata(payload: Dict[str, Any], *, selected_version: str) -> RegistryMetadata:
    if not isinstance(payload, dict):
        raise RuntimeError("npm registry returned non-object JSON.")
    versions = payload.get("versions")
    if not isinstance(versions, dict):
        raise RuntimeError("npm registry versions returned non-object JSON.")
    selected = versions.get(selected_version)
    if not isinstance(selected, dict):
        latest = (payload.get("dist-tags") or {}).get("latest")
        selected = versions.get(latest) if latest else None
    if not isinstance(selected, dict):
        selected = {}

    dependencies = selected.get("dependencies")
    if not isinstance(dependencies, dict):
        dependencies = {}
    engines = selected.get("engines")
    if not isinstance(engines, dict):
        engines = None

    return RegistryMetadata(
        summary=compact_text(selected.get("description") or payload.get("description"), max_chars=300),
        description_preview=compact_text(
            selected.get("readme") or payload.get("readme") or selected.get("description"),
            max_chars=1000,
        ),
        homepage=_as_str(selected.get("homepage") or payload.get("homepage")),
        repository=_normalize_repository(selected.get("repository") or payload.get("repository")),
        license=_normalize_license(selected.get("license") or payload.get("license")),
        requires_python=None,
        engines={str(key): str(value) for key, value in engines.items()} if engines else None,
        declared_dependencies={str(key): str(value) for key, value in dependencies.items()},
        vulnerabilities=[],
        deprecated=_as_str(selected.get("deprecated")),
    )


def _pick_project_url(value: Any, labels: tuple[str, ...]) -> Optional[str]:
    if not isinstance(value, dict):
        return None
    lowered = {str(key).lower(): val for key, val in value.items()}
    for label in labels:
        for key, val in lowered.items():
            if label in key and isinstance(val, str) and val.strip():
                return val.strip()
    return None


def _normalize_repository(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        url = value.get("url") or value.get("web")
        if isinstance(url, str):
            return url
    return None


def _normalize_license(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        license_type = value.get("type") or value.get("name")
        if isinstance(license_type, str):
            return license_type
    return None


def _summarize_dependency_graph(payload: Dict[str, Any]) -> DependencyGraphSummary:
    nodes = payload.get("nodes")
    edges = payload.get("edges")
    if not isinstance(nodes, list):
        nodes = []
    if not isinstance(edges, list):
        edges = []

    direct_indices: set[int] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        from_node = edge.get("fromNode")
        to_node = edge.get("toNode")
        if from_node in {0, "0"} and isinstance(to_node, int):
            direct_indices.add(to_node)

    sample_dependencies: List[str] = []
    for index, node in enumerate(nodes):
        if index == 0 or not isinstance(node, dict):
            continue
        sample = _node_label(node)
        if sample:
            sample_dependencies.append(sample)
        if len(sample_dependencies) >= 10:
            break

    total_nodes = max(len(nodes) - 1, 0)
    direct_count = len(direct_indices)
    return DependencyGraphSummary(
        direct_dependencies_count=direct_count,
        transitive_dependencies_count=max(total_nodes - direct_count, 0),
        total_nodes=total_nodes,
        sample_dependencies=sample_dependencies,
    )


def _node_label(node: Dict[str, Any]) -> Optional[str]:
    version_key = node.get("versionKey")
    if isinstance(version_key, dict):
        name = version_key.get("name")
        version = version_key.get("version")
        if name and version:
            return f"{name}@{version}"
        if name:
            return str(name)
    package_key = node.get("packageKey")
    if isinstance(package_key, dict) and package_key.get("name"):
        return str(package_key.get("name"))
    return None


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


def _extract_github_repo(url: Optional[str]) -> Optional[tuple[str, str]]:
    if not url:
        return None
    cleaned = url.removeprefix("git+").removesuffix(".git")
    parsed = urlparse(cleaned)
    target = parsed.netloc + parsed.path if parsed.netloc else cleaned
    match = _GITHUB_RE.search(target)
    if not match:
        return None
    owner = match.group("owner").strip()
    repo = match.group("repo").strip().removesuffix(".git")
    if not owner or not repo:
        return None
    return owner, repo


def _as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_reason(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return _as_str(value)
