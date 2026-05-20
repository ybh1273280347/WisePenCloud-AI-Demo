from __future__ import annotations

from typing import Any, Dict, Optional

from chat.application.tools.services.software_ecosystem.common.formatting import compact_text
from chat.application.tools.services.software_ecosystem.common.normalization import (
    normalize_package_name,
)
from chat.application.tools.services.software_ecosystem.packages.discovery.models import (
    PackageCandidate,
)
from chat.application.tools.services.software_ecosystem.packages.hydration.models import (
    RegistryMetadata,
)


def map_npm_candidate(item: Dict[str, Any]) -> PackageCandidate | None:
    package = item.get("package")
    if not isinstance(package, dict):
        return None
    name = _as_str(package.get("name"))
    if not name:
        return None
    return PackageCandidate(
        ecosystem="npm",
        name=name,
        normalized_name=normalize_package_name("npm", name),
        summary=compact_text(package.get("description"), max_chars=300),
        repository_url=_normalize_repository(package.get("links", {}).get("repository"))
        if isinstance(package.get("links"), dict)
        else None,
        homepage_url=_as_str(package.get("links", {}).get("homepage"))
        if isinstance(package.get("links"), dict)
        else None,
        source="npm",
        raw_score=_as_float(item.get("score", {}).get("final")) if isinstance(item.get("score"), dict) else 0.0,
        matched_terms=[],
    )


def map_npm_metadata(payload: Dict[str, Any], *, selected_version: str) -> RegistryMetadata:
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
        homepage_url=_as_str(selected.get("homepage") or payload.get("homepage")),
        repository_url=_normalize_repository(selected.get("repository") or payload.get("repository")),
        license=_normalize_license(selected.get("license") or payload.get("license")),
        requires_python=None,
        engines={str(key): str(value) for key, value in engines.items()} if engines else None,
        declared_dependencies={str(key): str(value) for key, value in dependencies.items()},
        vulnerabilities=[],
        deprecated=_as_str(selected.get("deprecated")),
        unavailable_reason=None,
    )


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


def _as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

