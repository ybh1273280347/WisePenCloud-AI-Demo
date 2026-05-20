from __future__ import annotations

import base64
from typing import Any, Dict

from chat.application.tools.services.software_ecosystem.common.formatting import compact_text

from .models import GitHubIssueResult, GitHubReleaseResult, GitHubRepositoryResult


def map_repository(item: Dict[str, Any]) -> GitHubRepositoryResult:
    license_info = item.get("license")
    license_name = None
    if isinstance(license_info, dict):
        license_name = license_info.get("spdx_id") or license_info.get("name")

    return GitHubRepositoryResult(
        full_name=str(item.get("full_name") or ""),
        html_url=str(item.get("html_url") or ""),
        description=compact_text(item.get("description"), max_chars=500),
        language=_as_optional_str(item.get("language")),
        stars=int(item.get("stargazers_count") or 0),
        forks=int(item.get("forks_count") or item.get("forks") or 0),
        open_issues=int(item.get("open_issues_count") or item.get("open_issues") or 0),
        default_branch=_as_optional_str(item.get("default_branch")),
        updated_at=_as_optional_str(item.get("updated_at")),
        pushed_at=_as_optional_str(item.get("pushed_at")),
        license_name=_as_optional_str(license_name),
        archived=bool(item.get("archived")),
    )


def map_issue(item: Dict[str, Any]) -> GitHubIssueResult:
    return GitHubIssueResult(
        title=str(item.get("title") or ""),
        html_url=str(item.get("html_url") or ""),
        repository_url=str(item.get("repository_url") or ""),
        state=str(item.get("state") or ""),
        comments=int(item.get("comments") or 0),
        created_at=_as_optional_str(item.get("created_at")),
        updated_at=_as_optional_str(item.get("updated_at")),
        is_pull_request=isinstance(item.get("pull_request"), dict),
        body_preview=compact_text(item.get("body"), max_chars=800),
    )


def map_release(item: Dict[str, Any]) -> GitHubReleaseResult:
    return GitHubReleaseResult(
        name=_as_optional_str(item.get("name")),
        tag_name=str(item.get("tag_name") or ""),
        html_url=str(item.get("html_url") or ""),
        published_at=_as_optional_str(item.get("published_at")),
        prerelease=bool(item.get("prerelease")),
        draft=bool(item.get("draft")),
        body_preview=compact_text(item.get("body"), max_chars=1000),
    )


def map_readme(owner: str, repo: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    content_preview = None
    content = payload.get("content")
    encoding = payload.get("encoding")
    if isinstance(content, str) and encoding == "base64":
        try:
            decoded = base64.b64decode(content, validate=False).decode(
                "utf-8",
                errors="replace",
            )
            content_preview = compact_text(decoded, max_chars=2000)
        except Exception:
            content_preview = None

    return {
        "repo": f"{owner}/{repo}",
        "encoding": encoding,
        "size": payload.get("size"),
        "download_url": payload.get("download_url"),
        "content_preview": content_preview,
    }


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

