from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class GitHubRepositoryResult:
    full_name: str
    html_url: str
    description: Optional[str]
    language: Optional[str]
    stars: int
    forks: int
    open_issues: int
    default_branch: Optional[str]
    updated_at: Optional[str]
    pushed_at: Optional[str]
    license_name: Optional[str]
    archived: bool


@dataclass(frozen=True, slots=True)
class GitHubIssueResult:
    title: str
    html_url: str
    repository_url: str
    state: str
    comments: int
    created_at: Optional[str]
    updated_at: Optional[str]
    is_pull_request: bool
    body_preview: Optional[str]


@dataclass(frozen=True, slots=True)
class GitHubReleaseResult:
    name: Optional[str]
    tag_name: str
    html_url: str
    published_at: Optional[str]
    prerelease: bool
    draft: bool
    body_preview: Optional[str]

