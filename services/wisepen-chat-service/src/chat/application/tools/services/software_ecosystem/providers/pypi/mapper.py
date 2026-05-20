from __future__ import annotations

from typing import Any, Dict, Optional

from chat.application.tools.services.software_ecosystem.common.formatting import compact_text
from chat.application.tools.services.software_ecosystem.packages.hydration.models import (
    RegistryMetadata,
)


def map_pypi_metadata(payload: Dict[str, Any]) -> RegistryMetadata:
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
        homepage_url=homepage,
        repository_url=repository,
        license=_as_str(info.get("license_expression")) or _as_str(info.get("license")),
        requires_python=_as_str(info.get("requires_python")),
        engines=None,
        declared_dependencies=[str(item) for item in declared_dependencies[:20]],
        vulnerabilities=[item for item in vulnerabilities[:10] if isinstance(item, dict)],
        deprecated=None,
        unavailable_reason=None,
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


def _as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

