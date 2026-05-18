from __future__ import annotations

from dataclasses import dataclass, field
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
    homepage: Optional[str]
    repository: Optional[str]
    license: Optional[str]
    requires_python: Optional[str]
    engines: Optional[Dict[str, str]]
    declared_dependencies: Dict[str, str] | List[str]
    vulnerabilities: List[Dict[str, Any]]
    deprecated: Optional[str]
    unavailable_reason: Optional[str] = None


@dataclass(frozen=True, slots=True)
class DepsDevIntelligence:
    available_versions_count: int
    default_version: Optional[str]
    selected_version: str
    published_at: Optional[str]
    licenses: List[str]
    advisory_count: int
    advisories: List[Dict[str, Any]]
    requirements_count: int
    resolved_dependencies_count: Optional[int]
    recent_versions: List[PackageVersionSummary]


@dataclass(frozen=True, slots=True)
class DependencyGraphSummary:
    direct_dependencies_count: int
    transitive_dependencies_count: int
    total_nodes: int
    sample_dependencies: List[str]


@dataclass(frozen=True, slots=True)
class ScorecardSummary:
    repo: str
    score: Optional[float]
    date: Optional[str]
    checks: List[Dict[str, Any]] = field(default_factory=list)
    unavailable_reason: Optional[str] = None


@dataclass(frozen=True, slots=True)
class PackageIntelligenceResult:
    ecosystem: str
    package_name: str
    selected_version: str
    default_version: Optional[str]
    published_at: Optional[str]
    deprecated: bool
    deprecated_reason: Optional[str]
    registry_metadata: Optional[RegistryMetadata]
    deps_dev: DepsDevIntelligence
    dependency_graph: Optional[DependencyGraphSummary]
    scorecard: Optional[ScorecardSummary]
    scorecard_requested: bool
