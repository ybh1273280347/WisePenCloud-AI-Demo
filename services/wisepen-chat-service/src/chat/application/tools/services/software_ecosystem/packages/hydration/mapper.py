from __future__ import annotations

from typing import List, Optional

from .models import PackageVersionSummary


def select_version(
    requested_version: Optional[str],
    versions: List[PackageVersionSummary],
) -> str:
    if requested_version:
        return requested_version

    default_version = find_default_version(versions)
    if default_version:
        return default_version

    candidates = [item for item in versions if item.published_at and not item.is_deprecated]
    candidates.sort(key=lambda item: item.published_at or "", reverse=True)
    if candidates:
        return candidates[0].version

    latest = latest_version(versions)
    if latest:
        return latest
    raise ValueError("Cannot determine package version")


def find_default_version(versions: List[PackageVersionSummary]) -> Optional[str]:
    for item in versions:
        if item.is_default:
            return item.version
    return None


def find_version_summary(
    versions: List[PackageVersionSummary],
    version: str,
) -> Optional[PackageVersionSummary]:
    for item in versions:
        if item.version == version:
            return item
    return None


def latest_version(versions: List[PackageVersionSummary]) -> Optional[str]:
    with_dates = [item for item in versions if item.published_at]
    with_dates.sort(key=lambda item: item.published_at or "", reverse=True)
    if with_dates:
        return with_dates[0].version
    return versions[0].version if versions else None


def recent_version_strings(
    versions: List[PackageVersionSummary],
    *,
    limit: int,
) -> List[str]:
    with_dates = [item for item in versions if item.published_at]
    with_dates.sort(key=lambda item: item.published_at or "", reverse=True)
    selected = with_dates[:limit] if with_dates else versions[:limit]
    return [item.version for item in selected]

