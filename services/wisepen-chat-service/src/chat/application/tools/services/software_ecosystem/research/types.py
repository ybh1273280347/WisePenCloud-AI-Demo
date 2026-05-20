from __future__ import annotations

from typing import Final, Literal

SoftwareEcosystemTarget = Literal[
    "open_source_project",
    "package",
    "community_discussion",
]

SoftwareEcosystemSort = Literal[
    "relevance",
    "stars",
    "recent_activity",
    "maintenance",
    "popularity",
]

PackageHydrationDepth = Literal[
    "light",
    "standard",
    "deep",
]

SOFTWARE_ECOSYSTEM_TARGETS: Final[tuple[str, ...]] = (
    "open_source_project",
    "package",
    "community_discussion",
)

SOFTWARE_ECOSYSTEM_SORTS: Final[tuple[str, ...]] = (
    "relevance",
    "stars",
    "recent_activity",
    "maintenance",
    "popularity",
)

PACKAGE_HYDRATION_DEPTHS: Final[tuple[str, ...]] = (
    "light",
    "standard",
    "deep",
)
