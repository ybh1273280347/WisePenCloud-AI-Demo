from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from chat.application.code_search.common.errors import VerticalSearchHttpError
from chat.application.code_search.common.formatting import (
    append_key_values,
    format_scalar,
    truncate_result,
)
from chat.application.code_search.github_search import (
    GitHubIssueResult,
    GitHubReleaseResult,
    GitHubRepositoryResult,
    GitHubSearchService,
)
from chat.application.code_search.github_search.config import (
    GITHUB_SEARCH_DEFAULT_LIMIT,
    GITHUB_SEARCH_TOOL_RESULT_MAX_CHARS,
)
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_error, log_event


_TOOL_DESCRIPTION = (
    "Searches GitHub repositories and public code-related metadata to find official repositories, README examples, "
    "implementation references, issues, discussions, releases, and community practices.\n\n"
    "Use this tool when the user asks about coding implementation, library usage, framework conventions, "
    "repository examples, official examples, or community best practices.\n\n"
    "For coding questions involving third-party libraries, unfamiliar APIs, framework behavior, or community best "
    "practices, prefer using this tool before giving implementation advice.\n\n"
    "Prioritize official repositories, well-maintained projects, recent examples, and source-backed patterns. "
    "Do not treat random code snippets as authoritative without considering repository quality, recency, and "
    "maintenance status.\n\n"
    "This tool is for discovery and evidence gathering. The assistant should synthesize the result into "
    "project-specific guidance instead of copying code blindly."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": [
                "search_repositories",
                "search_issues",
                "get_repository",
                "get_readme",
                "get_releases",
            ],
            "description": "GitHub operation.",
        },
        "query": {
            "type": "string",
            "description": "GitHub search query. Use qualifiers when useful.",
        },
        "owner": {
            "type": "string",
            "description": "Repository owner for get operations.",
        },
        "repo": {
            "type": "string",
            "description": "Repository name for get operations.",
        },
        "sort": {
            "type": "string",
            "enum": [
                "stars",
                "forks",
                "updated",
                "comments",
                "created",
                "interactions",
            ],
            "description": "Optional GitHub-supported sort field.",
        },
        "order": {
            "type": "string",
            "enum": ["desc", "asc"],
            "default": "desc",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "default": GITHUB_SEARCH_DEFAULT_LIMIT,
        },
    },
    "required": ["operation"],
    "additionalProperties": False,
}


class GitHubSearchTool(BaseTool):
    def __init__(self, service: Optional[GitHubSearchService] = None) -> None:
        self._service = service or GitHubSearchService()

    @property
    def name(self) -> str:
        return "github_search"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        operation = kwargs.get("operation")
        if operation not in _TOOL_SCHEMA["properties"]["operation"]["enum"]:
            return "[Tool Error] operation must be a supported GitHub operation."

        limit = _coerce_limit(kwargs.get("limit", GITHUB_SEARCH_DEFAULT_LIMIT))
        if limit is None:
            return "[Tool Error] limit must be an integer between 1 and 20."

        sort = kwargs.get("sort")
        if sort is not None and sort not in _TOOL_SCHEMA["properties"]["sort"]["enum"]:
            return "[Tool Error] sort must be a supported GitHub sort field."

        order = kwargs.get("order", "desc")
        if order not in {"desc", "asc"}:
            return "[Tool Error] order must be desc or asc."

        try:
            log_event("github_search fetched", operation=operation)
            if operation == "search_repositories":
                query = _require_text(kwargs.get("query"), "query")
                if query is None:
                    return "[Tool Error] query is required for search_repositories."
                total, incomplete, results = await self._service.search_repositories(
                    query=query,
                    sort=sort,
                    order=order,
                    limit=limit,
                )
                return _fit(_format_repository_search(query, total, incomplete, results))

            if operation == "search_issues":
                query = _require_text(kwargs.get("query"), "query")
                if query is None:
                    return "[Tool Error] query is required for search_issues."
                total, incomplete, results = await self._service.search_issues(
                    query=query,
                    sort=sort,
                    order=order,
                    limit=limit,
                )
                return _fit(_format_issue_search(query, total, incomplete, results))

            owner, repo = _owner_repo(kwargs)
            if owner is None or repo is None:
                return f"[Tool Error] owner and repo are required for {operation}."

            if operation == "get_repository":
                result = await self._service.get_repository(owner=owner, repo=repo)
                return _fit(_format_repository_get(owner, repo, result))
            if operation == "get_readme":
                result = await self._service.get_readme(owner=owner, repo=repo)
                return _fit(_format_readme(result))
            if operation == "get_releases":
                results = await self._service.get_releases(owner=owner, repo=repo, limit=limit)
                return _fit(_format_releases(owner, repo, results))

            return "[Tool Error] Unsupported GitHub operation."
        except VerticalSearchHttpError as e:
            log_error("github_search", e, operation=operation)
            return _map_github_http_error(e)
        except Exception as e:
            log_error("github_search", e, operation=operation)
            return "[Tool Error] GitHub API returned an unexpected response."

    async def close(self) -> None:
        await self._service.close()


def _coerce_limit(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return None
    if limit < 1 or limit > 20:
        return None
    return limit


def _require_text(value: Any, name: str) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text


def _owner_repo(kwargs: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    return _require_text(kwargs.get("owner"), "owner"), _require_text(kwargs.get("repo"), "repo")


def _format_repository_search(
    query: str,
    total_count: int,
    incomplete_results: bool,
    results: List[GitHubRepositoryResult],
) -> str:
    lines = [
        "[Tool Result] github_search",
        "",
        "Operation: search_repositories",
        f"Query: {query}",
        f"Total count: {total_count}",
        f"Incomplete results: {format_scalar(incomplete_results)}",
        "",
        "Repositories:",
    ]
    _append_repositories(lines, results)
    _append_github_instructions(lines)
    return "\n".join(lines)


def _format_repository_get(owner: str, repo: str, result: GitHubRepositoryResult) -> str:
    lines = [
        "[Tool Result] github_search",
        "",
        "Operation: get_repository",
        f"Repo: {owner}/{repo}",
        "",
        "Repository:",
    ]
    _append_repository(lines, result, index=None)
    _append_github_instructions(lines)
    return "\n".join(lines)


def _append_repositories(lines: List[str], results: List[GitHubRepositoryResult]) -> None:
    if not results:
        lines.append("- none")
        return
    for index, item in enumerate(results, 1):
        _append_repository(lines, item, index=index)


def _append_repository(
    lines: List[str],
    item: GitHubRepositoryResult,
    *,
    index: Optional[int],
) -> None:
    prefix = f"[{index}] " if index is not None else ""
    lines.append(f"{prefix}{item.full_name}")
    append_key_values(
        lines,
        [
            ("url", item.html_url),
            ("description", item.description),
            ("language", item.language),
            ("stars", item.stars),
            ("forks", item.forks),
            ("open_issues", item.open_issues),
            ("default_branch", item.default_branch),
            ("updated_at", item.updated_at),
            ("pushed_at", item.pushed_at),
            ("license", item.license_name),
            ("archived", item.archived),
        ],
    )


def _format_issue_search(
    query: str,
    total_count: int,
    incomplete_results: bool,
    results: List[GitHubIssueResult],
) -> str:
    lines = [
        "[Tool Result] github_search",
        "",
        "Operation: search_issues",
        f"Query: {query}",
        f"Total count: {total_count}",
        f"Incomplete results: {format_scalar(incomplete_results)}",
        "",
        "Issues and pull requests:",
    ]
    if not results:
        lines.append("- none")
    for index, item in enumerate(results, 1):
        lines.append(f"[{index}] {item.title}")
        append_key_values(
            lines,
            [
                ("url", item.html_url),
                ("repository_url", item.repository_url),
                ("state", item.state),
                ("type", "pull_request" if item.is_pull_request else "issue"),
                ("comments", item.comments),
                ("created_at", item.created_at),
                ("updated_at", item.updated_at),
                ("body_preview", item.body_preview),
            ],
        )
    _append_github_instructions(lines)
    return "\n".join(lines)


def _format_readme(result: Dict[str, Any]) -> str:
    lines = [
        "[Tool Result] github_search",
        "",
        "Operation: get_readme",
        "",
        "README:",
    ]
    append_key_values(
        lines,
        [
            ("repo", result.get("repo")),
            ("encoding", result.get("encoding")),
            ("size", result.get("size")),
            ("download_url", result.get("download_url")),
            ("content_preview", result.get("content_preview")),
        ],
    )
    _append_github_instructions(lines)
    return "\n".join(lines)


def _format_releases(owner: str, repo: str, results: List[GitHubReleaseResult]) -> str:
    lines = [
        "[Tool Result] github_search",
        "",
        "Operation: get_releases",
        f"Repo: {owner}/{repo}",
        "",
        "Releases:",
    ]
    if not results:
        lines.append("- none")
    for index, item in enumerate(results, 1):
        lines.append(f"[{index}] {item.tag_name}")
        append_key_values(
            lines,
            [
                ("name", item.name),
                ("published_at", item.published_at),
                ("prerelease", item.prerelease),
                ("draft", item.draft),
                ("url", item.html_url),
                ("body_preview", item.body_preview),
            ],
        )
    _append_github_instructions(lines)
    return "\n".join(lines)


def _append_github_instructions(lines: List[str]) -> None:
    lines.extend(
        [
            "",
            "Assistant instructions:",
            "- Prefer official repositories and well-maintained projects.",
            "- Treat README, docs, examples, and recent issues as stronger evidence than random snippets.",
            "- Compare multiple results when possible before recommending an implementation.",
            "- Do not copy code blindly; adapt patterns to the current project architecture.",
            "- If results are weak, say that the evidence is limited and avoid overconfident claims.",
            "- For user code changes, keep recommendations scoped to the current task.",
            "- If the user asks for multi-language coding guidance, avoid assuming Python-only practices apply universally.",
        ]
    )


def _map_github_http_error(error: VerticalSearchHttpError) -> str:
    status_code = error.status_code
    headers = {key.lower(): value for key, value in error.headers.items()}
    body_preview = (error.body_preview or "").lower()

    if status_code == 403 and (
        headers.get("x-ratelimit-remaining") == "0" or "rate limit" in body_preview
    ):
        return "[Tool Error] GitHub API rate limit exceeded."
    if status_code in {401, 403}:
        return "[Tool Error] GitHub API authentication or permission failed."
    if status_code == 404:
        return "[Tool Error] GitHub resource not found."
    if status_code == 422:
        return "[Tool Error] GitHub query validation failed. Check query qualifiers."
    if status_code is not None and status_code >= 500:
        return "[Tool Error] GitHub API unavailable."
    return "[Tool Error] GitHub API request failed."


def _fit(text: str) -> str:
    return _truncate_preserving_assistant_instructions(
        text,
        max_chars=GITHUB_SEARCH_TOOL_RESULT_MAX_CHARS,
    )


def _truncate_preserving_assistant_instructions(text: str, *, max_chars: int) -> str:
    marker = "\n\nAssistant instructions:"
    marker_index = text.find(marker)
    if marker_index < 0 or len(text) <= max_chars:
        return truncate_result(text, max_chars=max_chars)

    body = text[:marker_index]
    instructions = text[marker_index:]
    if len(instructions) >= max_chars:
        return truncate_result(text, max_chars=max_chars)

    notice = "\n\n[Tool Notice] Result truncated because it exceeded the tool output budget."
    body_budget = max_chars - len(instructions) - len(notice)
    if body_budget <= 0:
        return truncate_result(text, max_chars=max_chars)
    return body[:body_budget].rstrip() + notice + instructions
