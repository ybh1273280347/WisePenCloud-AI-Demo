from __future__ import annotations

from typing import Any, Dict, List, Optional

from chat.application.tools.services.code_search.common.errors import VerticalSearchHttpError
from chat.application.tools.services.code_search.common.formatting import (
    append_key_values,
    format_scalar,
    truncate_result,
)
from chat.application.tools.services.code_search.package_intelligence import (
    PackageIntelligenceResult,
    PackageIntelligenceService,
    PackageVersionSummary,
    RegistryMetadata,
    ScorecardSummary,
)
from chat.application.tools.services.code_search.package_intelligence.config import (
    PACKAGE_INTELLIGENCE_TOOL_RESULT_MAX_CHARS,
)
from chat.application.tools.services.code_search.package_intelligence.models import (
    DependencyGraphSummary,
)
from chat.application.tools.services.code_search.package_intelligence.service import (
    CannotDeterminePackageVersion,
)
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_error, log_event


_TOOL_DESCRIPTION = (
    "Queries package registry metadata for Python and npm packages, including latest version, release time, "
    "summary, homepage, repository, license, runtime requirements, dependencies, recent versions, deprecation "
    "status, and vulnerability signals when available.\n\n"
    "Use this tool before recommending new dependencies, upgrading packages, comparing libraries, or assessing "
    "whether a package is suitable for production use.\n\n"
    "Prefer mature, actively maintained, permissively licensed packages with clear documentation and stable "
    "release history.\n"
    "This tool provides dependency evidence only; project-specific constraints still come from the user and codebase."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "ecosystem": {
            "type": "string",
            "enum": ["pypi", "npm"],
            "description": "Package ecosystem.",
        },
        "package_name": {
            "type": "string",
            "minLength": 1,
            "description": "Exact package name, such as fastapi, pydantic, react, or @vitejs/plugin-react.",
        },
        "version": {
            "type": "string",
            "description": "Optional package version. If omitted, use deps.dev default version.",
        },
        "include_dependencies": {
            "type": "boolean",
            "default": False,
            "description": "Include resolved dependency graph summary.",
        },
        "include_scorecard": {
            "type": "boolean",
            "default": False,
            "description": "Include OpenSSF Scorecard when a linked GitHub repository is available.",
        },
    },
    "required": ["ecosystem", "package_name"],
    "additionalProperties": False,
}


class PackageIntelligenceTool(BaseTool):
    def __init__(self, service: Optional[PackageIntelligenceService] = None) -> None:
        self._service = service or PackageIntelligenceService()

    @property
    def name(self) -> str:
        return "package_intelligence"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        ecosystem = kwargs.get("ecosystem")
        if ecosystem not in {"pypi", "npm"}:
            return "[Tool Error] ecosystem must be pypi or npm."

        package_name = kwargs.get("package_name")
        if not isinstance(package_name, str) or not package_name.strip():
            return "[Tool Error] package_name is required."
        package_name = package_name.strip()

        version = kwargs.get("version")
        if version is not None:
            if not isinstance(version, str) or not version.strip():
                return "[Tool Error] version must be a non-empty string when provided."
            version = version.strip()

        include_dependencies = bool(kwargs.get("include_dependencies", False))
        include_scorecard = bool(kwargs.get("include_scorecard", False))

        try:
            log_event(
                "package_intelligence fetched",
                ecosystem=ecosystem,
                package_name=package_name,
                include_dependencies=include_dependencies,
                include_scorecard=include_scorecard,
            )
            result = await self._service.lookup(
                ecosystem=ecosystem,
                package_name=package_name,
                version=version,
                include_dependencies=include_dependencies,
                include_scorecard=include_scorecard,
            )
            return _truncate_preserving_assistant_instructions(
                _format_package_intelligence(result),
                max_chars=PACKAGE_INTELLIGENCE_TOOL_RESULT_MAX_CHARS,
            )
        except CannotDeterminePackageVersion:
            return "[Tool Error] Cannot determine package version."
        except VerticalSearchHttpError as e:
            log_error(
                "package_intelligence",
                e,
                ecosystem=ecosystem,
                package_name=package_name,
            )
            return _map_package_http_error(e)
        except Exception as e:
            log_error(
                "package_intelligence",
                e,
                ecosystem=ecosystem,
                package_name=package_name,
            )
            return "[Tool Error] Package intelligence API returned an unexpected response."

    async def close(self) -> None:
        await self._service.close()


def _format_package_intelligence(result: PackageIntelligenceResult) -> str:
    lines = [
        "[Tool Result] package_intelligence",
        "",
        "Package:",
    ]
    append_key_values(
        lines,
        [
            ("ecosystem", result.ecosystem),
            ("name", result.package_name),
            ("selected_version", result.selected_version),
            ("default_version", result.default_version),
            ("published_at", result.published_at),
            ("deprecated", result.deprecated),
            ("deprecated_reason", result.deprecated_reason),
        ],
    )

    _append_registry_metadata(lines, result.registry_metadata)
    _append_deps_dev(lines, result)
    _append_dependency_graph(lines, result.dependency_graph)
    _append_scorecard(lines, result.scorecard, requested=result.scorecard_requested)
    _append_package_instructions(lines)
    return "\n".join(lines)


def _append_registry_metadata(
    lines: List[str],
    metadata: Optional[RegistryMetadata],
) -> None:
    lines.extend(["", "Registry metadata:"])
    if metadata is None:
        lines.append("- unavailable")
        return
    if metadata.unavailable_reason:
        lines.append(f"- unavailable: {metadata.unavailable_reason}")
        return
    append_key_values(
        lines,
        [
            ("summary", metadata.summary),
            ("description_preview", metadata.description_preview),
            ("homepage", metadata.homepage),
            ("repository", metadata.repository),
            ("license", metadata.license),
            ("requires_python", metadata.requires_python),
            ("engines", metadata.engines),
            ("declared_dependencies", _dependency_preview(metadata.declared_dependencies)),
            ("vulnerabilities", _vulnerability_preview(metadata.vulnerabilities)),
            ("deprecated", metadata.deprecated),
        ],
    )


def _append_deps_dev(lines: List[str], result: PackageIntelligenceResult) -> None:
    deps = result.deps_dev
    lines.extend(["", "deps.dev intelligence:"])
    append_key_values(
        lines,
        [
            ("available_versions_count", deps.available_versions_count),
            ("recent_versions", _format_recent_versions(deps.recent_versions)),
            ("licenses", deps.licenses),
            ("advisory_count", deps.advisory_count),
            ("advisories", _vulnerability_preview(deps.advisories)),
            ("requirements_count", deps.requirements_count),
            ("resolved_dependencies_count", deps.resolved_dependencies_count),
            ("dependency_graph_included", result.dependency_graph is not None),
        ],
    )


def _append_dependency_graph(
    lines: List[str],
    graph: Optional[DependencyGraphSummary],
) -> None:
    if graph is None:
        return
    lines.extend(["", "Resolved dependency graph:"])
    append_key_values(
        lines,
        [
            ("direct_dependencies_count", graph.direct_dependencies_count),
            ("transitive_dependencies_count", graph.transitive_dependencies_count),
            ("total_nodes", graph.total_nodes),
            ("sample_dependencies", graph.sample_dependencies),
        ],
    )


def _append_scorecard(
    lines: List[str],
    scorecard: Optional[ScorecardSummary],
    *,
    requested: bool,
) -> None:
    lines.extend(["", "OpenSSF Scorecard:"])
    if not requested:
        lines.append("- included: false")
        return
    if scorecard is None:
        lines.extend(["- included: false", "- reason: no linked GitHub repository found"])
        return
    if scorecard.unavailable_reason:
        lines.extend(["- included: false", f"- repo: {scorecard.repo}", f"- reason: {scorecard.unavailable_reason}"])
        return
    append_key_values(
        lines,
        [
            ("included", True),
            ("repo", scorecard.repo),
            ("score", scorecard.score),
            ("date", scorecard.date),
            ("key_checks", _vulnerability_preview(scorecard.checks)),
        ],
    )


def _append_package_instructions(lines: List[str]) -> None:
    lines.extend(
        [
            "",
            "Assistant instructions:",
            "- Check license, maintenance status, release recency, runtime requirements, and dependency weight before recommending a package.",
            "- Prefer mature libraries and official packages over small unmaintained alternatives.",
            "- Do not recommend adding a dependency only because it exists.",
            "- If the project already has a mature dependency that solves the problem, prefer reusing it.",
            "- If metadata is incomplete, state the uncertainty.",
            "- For this project, avoid increasing deployment complexity unless the capability gain is significant.",
            "- Consider the target language ecosystem before recommending a package or pattern.",
        ]
    )


def _dependency_preview(value: Dict[str, str] | List[str]) -> str:
    if isinstance(value, dict):
        if not value:
            return "none"
        items = list(value.items())[:20]
        suffix = "" if len(value) <= 20 else f"; ... +{len(value) - 20} more"
        return "; ".join(f"{key}: {val}" for key, val in items) + suffix
    if not value:
        return "none"
    suffix = "" if len(value) <= 20 else f"; ... +{len(value) - 20} more"
    return "; ".join(str(item) for item in value[:20]) + suffix


def _format_recent_versions(versions: List[PackageVersionSummary]) -> str:
    if not versions:
        return "none"
    return "; ".join(
        (
            f"{item.version}"
            f" (published_at={format_scalar(item.published_at)}, "
            f"default={format_scalar(item.is_default)}, "
            f"deprecated={format_scalar(item.is_deprecated)})"
        )
        for item in versions
    )


def _vulnerability_preview(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "none"
    chunks: List[str] = []
    for item in items[:10]:
        identity = item.get("id") or item.get("name") or item.get("title") or item.get("url")
        score = item.get("score")
        reason = item.get("reason")
        details = [str(identity)] if identity else [str(item)]
        if score is not None:
            details.append(f"score={score}")
        if reason:
            details.append(f"reason={reason}")
        chunks.append(" ".join(details))
    if len(items) > 10:
        chunks.append(f"... +{len(items) - 10} more")
    return "; ".join(chunks)


def _map_package_http_error(error: VerticalSearchHttpError) -> str:
    status_code = error.status_code
    if status_code == 404:
        return "[Tool Error] Package not found in package intelligence source."
    if status_code == 429:
        return "[Tool Error] Package intelligence API rate limited."
    if status_code is not None and status_code >= 500:
        return "[Tool Error] Package intelligence API unavailable."
    return "[Tool Error] Package intelligence API request failed."


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
