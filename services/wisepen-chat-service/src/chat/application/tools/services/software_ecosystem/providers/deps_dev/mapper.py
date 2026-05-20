from __future__ import annotations

from typing import Any, Dict, List, Optional

from chat.application.tools.services.software_ecosystem.common.formatting import compact_text
from chat.application.tools.services.software_ecosystem.packages.hydration.models import (
    DependencyGraphSummary,
    PackageVersionSummary,
)


def extract_versions(payload: Dict[str, Any]) -> List[PackageVersionSummary]:
    raw_versions = payload.get("versions")
    if not isinstance(raw_versions, list):
        return []
    versions = [map_deps_version(item) for item in raw_versions if isinstance(item, dict)]
    return [item for item in versions if item.version]


def map_deps_version(item: Dict[str, Any]) -> PackageVersionSummary:
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


def extract_licenses(payload: Dict[str, Any]) -> List[str]:
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


def extract_advisories(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
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


def summarize_dependency_graph(payload: Dict[str, Any]) -> DependencyGraphSummary:
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


def _as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

