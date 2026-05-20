from __future__ import annotations

import re
from urllib.parse import urlparse

_GITHUB_RE = re.compile(r"github\.com[:/](?P<owner>[^/\s]+)/(?P<repo>[^/\s#?]+)")


def normalize_query(query: str) -> str:
    return " ".join(query.strip().lower().split())


def normalize_package_name(ecosystem: str, package_name: str) -> str:
    name = package_name.strip().lower()
    if ecosystem == "pypi":
        return name.replace("_", "-")
    return name


def package_entity_id(ecosystem: str, package_name: str) -> str:
    return f"pkg:{ecosystem}:{normalize_package_name(ecosystem, package_name)}"


def repository_entity_id(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.strip("/").lower()
    if host == "github.com":
        parts = path.split("/")
        if len(parts) >= 2:
            path = "/".join(parts[:2])
    return f"repo:{host}:{path}"


def extract_github_repo(url: str | None) -> tuple[str, str] | None:
    if not url:
        return None
    cleaned = url.removeprefix("git+").removesuffix(".git")
    parsed = urlparse(cleaned)
    target = parsed.netloc + parsed.path if parsed.netloc else cleaned
    match = _GITHUB_RE.search(target)
    if not match:
        return None
    owner = match.group("owner").strip()
    repo = match.group("repo").strip().removesuffix(".git")
    if not owner or not repo:
        return None
    return owner, repo

