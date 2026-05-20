from __future__ import annotations

from typing import Final, Iterable, Tuple

from .errors import UnsupportedEcosystemError

SUPPORTED_PACKAGE_ECOSYSTEMS: Final[Tuple[str, ...]] = (
    "npm",
    "pypi",
)

ECOSYSTEM_TO_DEPS_DEV_SYSTEM: Final[dict[str, str]] = {
    "npm": "NPM",
    "pypi": "PYPI",
}


def validate_ecosystem(value: str) -> str:
    if not isinstance(value, str):
        raise UnsupportedEcosystemError("ecosystem must be a string")
    ecosystem = value.strip().lower()
    if ecosystem not in SUPPORTED_PACKAGE_ECOSYSTEMS:
        supported = ", ".join(SUPPORTED_PACKAGE_ECOSYSTEMS)
        raise UnsupportedEcosystemError(f"unsupported ecosystem: {value}; supported: {supported}")
    return ecosystem


def validate_ecosystems(values: Iterable[str]) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise UnsupportedEcosystemError("ecosystems must be a non-empty list")
    ecosystems = [validate_ecosystem(item) for item in values]
    if not ecosystems:
        raise UnsupportedEcosystemError("ecosystems must not be empty")
    return list(dict.fromkeys(ecosystems))

