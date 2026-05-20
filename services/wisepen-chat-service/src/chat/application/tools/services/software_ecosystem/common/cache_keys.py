from __future__ import annotations

from typing import Iterable

from .normalization import normalize_package_name, normalize_query


def package_query_cache_key(query: str, ecosystems: Iterable[str], limit: int) -> str:
    ecosystem_key = ",".join(sorted(str(item).lower() for item in ecosystems))
    return f"package-query:{normalize_query(query)}:{ecosystem_key}:{limit}"


def package_profile_cache_key(ecosystem: str, package_name: str, version: str | None) -> str:
    normalized = normalize_package_name(ecosystem, package_name)
    return f"package-profile:{ecosystem}:{normalized}:{version or 'latest'}"


def latest_pointer_cache_key(ecosystem: str, package_name: str) -> str:
    normalized = normalize_package_name(ecosystem, package_name)
    return f"package-latest:{ecosystem}:{normalized}"


def github_repo_cache_key(owner: str, repo: str) -> str:
    return f"github-repo:{owner.lower()}/{repo.lower()}"


def community_query_cache_key(query: str, limit: int) -> str:
    return f"community-query:{normalize_query(query)}:{limit}"


def open_source_project_query_cache_key(
    query: str,
    languages: Iterable[str] | None,
    sort: str,
    limit: int,
    min_stars: int | None,
) -> str:
    language_key = ",".join(sorted(str(item).lower() for item in (languages or [])))
    return (
        f"open-source-query:{normalize_query(query)}:{language_key}:"
        f"{sort}:{limit}:{min_stars if min_stars is not None else 'none'}"
    )


def open_source_project_profile_cache_key(owner: str, repo: str) -> str:
    return f"open-source-profile:{owner.lower()}/{repo.lower()}"


def software_ecosystem_candidate_cache_key(
    query: str,
    targets: Iterable[str],
    sort: str,
    limit: int,
) -> str:
    target_key = ",".join(sorted(str(item).lower() for item in targets))
    return f"software-candidates:{normalize_query(query)}:{target_key}:{sort}:{limit}"
