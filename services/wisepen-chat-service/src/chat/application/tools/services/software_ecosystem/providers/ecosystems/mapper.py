from __future__ import annotations

from typing import Any, Dict, Optional

from chat.application.tools.services.software_ecosystem.common.formatting import compact_text
from chat.application.tools.services.software_ecosystem.common.normalization import (
    normalize_package_name,
)
from chat.application.tools.services.software_ecosystem.packages.discovery.models import (
    PackageCandidate,
)


def map_ecosystems_candidate(ecosystem: str, item: Dict[str, Any]) -> PackageCandidate | None:
    name = _as_str(item.get("name"))
    if not name:
        return None
    repository_url = (
        _as_str(item.get("repository_url"))
        or _as_str(item.get("repository"))
        or _as_str(item.get("repo_url"))
    )
    homepage_url = _as_str(item.get("homepage")) or _as_str(item.get("homepage_url"))
    return PackageCandidate(
        ecosystem=ecosystem,
        name=name,
        normalized_name=normalize_package_name(ecosystem, name),
        summary=compact_text(item.get("description") or item.get("summary"), max_chars=300),
        repository_url=repository_url,
        homepage_url=homepage_url,
        source="ecosystems",
        raw_score=_as_float(item.get("rank") or item.get("score") or item.get("downloads")),
        matched_terms=[],
    )


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

