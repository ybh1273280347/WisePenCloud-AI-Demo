from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True, slots=True)
class PackageVersionSummary:
    version: str
    published_at: Optional[str]
    is_default: bool
    is_deprecated: bool
    deprecated_reason: Optional[str]


@dataclass(frozen=True, slots=True)
class RegistryMetadata:
    summary: Optional[str]
    description_preview: Optional[str]
    homepage_url: Optional[str]
    repository_url: Optional[str]
    license: Optional[str]
    requires_python: Optional[str]
    engines: Optional[Dict[str, str]]
    declared_dependencies: Dict[str, str] | List[str]
    vulnerabilities: List[Dict[str, Any]]
    deprecated: Optional[str]
    unavailable_reason: Optional[str] = None


@dataclass(frozen=True, slots=True)
class DependencyGraphSummary:
    direct_dependencies_count: int
    transitive_dependencies_count: int
    total_nodes: int
    sample_dependencies: List[str]


@dataclass(frozen=True, slots=True)
class PackageHydrationSignals:
    available_versions_count: int
    advisories_count: int
    requirements_count: int
    licenses: List[str]


@dataclass(frozen=True, slots=True)
class PackageProfile:
    ecosystem: str
    name: str
    normalized_name: str
    selected_version: Optional[str]
    latest_version: Optional[str]
    published_at: Optional[str]
    summary: Optional[str]
    description_preview: Optional[str]
    homepage_url: Optional[str]
    repository_url: Optional[str]
    license: Optional[str]
    deprecated: bool
    deprecated_reason: Optional[str]
    direct_dependencies_count: Optional[int]
    transitive_dependencies_count: Optional[int]
    recent_versions: List[str]
    repository_stars: Optional[int]
    repository_forks: Optional[int]
    repository_open_issues: Optional[int]
    repository_pushed_at: Optional[str]
    repository_archived: Optional[bool]
    maintenance_score: float
    popularity_score: float
    dependency_complexity_score: float
    ecosystem_score: float
    evidence: List[str]
    signals: PackageHydrationSignals

