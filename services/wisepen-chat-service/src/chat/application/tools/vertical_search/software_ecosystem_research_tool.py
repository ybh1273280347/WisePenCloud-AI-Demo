from __future__ import annotations

from typing import Any, Dict, List, Optional

from chat.application.tools.services.software_ecosystem import config
from chat.application.tools.services.software_ecosystem.common.errors import (
    InvalidSoftwareEcosystemQueryError,
    SoftwareEcosystemHttpError,
    UnsupportedEcosystemError,
)
from chat.application.tools.services.software_ecosystem.common.formatting import (
    append_key_values,
    format_scalar,
    truncate_result,
)
from chat.application.tools.services.software_ecosystem.open_source.hydration.models import (
    OpenSourceProjectProfile,
)
from chat.application.tools.services.software_ecosystem.packages.hydration.models import (
    PackageProfile,
)
from chat.application.tools.services.software_ecosystem.research.models import (
    SoftwareEcosystemResearchResult,
)
from chat.application.tools.services.software_ecosystem.research.service import (
    SoftwareEcosystemResearchService,
)
from chat.application.tools.services.software_ecosystem.research.types import (
    PACKAGE_HYDRATION_DEPTHS,
    SOFTWARE_ECOSYSTEM_SORTS,
    SOFTWARE_ECOSYSTEM_TARGETS,
)
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_error, log_event

_TOOL_DESCRIPTION = (
    "Search and analyze software development ecosystem signals, including open-source projects, packages, "
    "and developer community discussions."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "description": "Software ecosystem research question.",
        },
        "targets": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": list(SOFTWARE_ECOSYSTEM_TARGETS),
            },
            "minItems": 1,
            "description": "Entity types to search.",
        },
        "ecosystems": {
            "type": ["array", "null"],
            "items": {"type": "string", "enum": ["npm", "pypi"]},
            "description": "Package ecosystems. Only applies to package target.",
        },
        "languages": {
            "type": ["array", "null"],
            "items": {"type": "string"},
            "description": "Open-source project language filters. Only applies to open_source_project target.",
        },
        "sort": {
            "type": "string",
            "enum": list(SOFTWARE_ECOSYSTEM_SORTS),
            "description": "Ranking preference.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": config.SOFTWARE_ECOSYSTEM_MAX_LIMIT,
        },
        "min_stars": {
            "type": ["integer", "null"],
            "minimum": 0,
            "description": (
                "Minimum GitHub stars for open-source project search. Only applies when targets includes "
                "open_source_project. Suggested model defaults: null for general project discovery, 100 for "
                "mature projects, 500 for high-star projects, and 5000 for top/headline projects."
            ),
        },
        "package_hydration_depth": {
            "type": "string",
            "enum": list(PACKAGE_HYDRATION_DEPTHS),
            "description": (
                "Package hydration depth. light loads basic package metadata; "
                "standard adds registry/deps.dev version/recent versions/requirements; "
                "deep adds dependency graph. Only applies to package target."
            ),
        },
    },
    "required": ["query", "targets", "sort", "limit", "package_hydration_depth"],
    "additionalProperties": False,
}


class SoftwareEcosystemResearchTool(BaseTool):
    def __init__(self, service: Optional[SoftwareEcosystemResearchService] = None) -> None:
        self._service = service or SoftwareEcosystemResearchService()

    @property
    def name(self) -> str:
        return "software_ecosystem_research"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        query = kwargs.get("query")
        if not isinstance(query, str) or not query.strip():
            return "[Tool Error] query must be a non-empty string."

        targets = kwargs.get("targets")
        if (
            isinstance(targets, (str, bytes))
            or not isinstance(targets, list)
            or not targets
            or not all(isinstance(item, str) for item in targets)
        ):
            return "[Tool Error] targets must be a non-empty string array."

        ecosystems = kwargs.get("ecosystems")
        if ecosystems is not None and (
            isinstance(ecosystems, (str, bytes))
            or not isinstance(ecosystems, list)
            or not ecosystems
            or not all(isinstance(item, str) for item in ecosystems)
        ):
            return "[Tool Error] ecosystems must be null or a non-empty string array."

        languages = kwargs.get("languages")
        if languages is not None and (
            isinstance(languages, (str, bytes))
            or not isinstance(languages, list)
            or not languages
            or not all(isinstance(item, str) and item.strip() for item in languages)
        ):
            return "[Tool Error] languages must be null or a non-empty string array."

        sort = kwargs.get("sort")
        if sort not in SOFTWARE_ECOSYSTEM_SORTS:
            return "[Tool Error] sort must be a supported value."

        limit = kwargs.get("limit")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > config.SOFTWARE_ECOSYSTEM_MAX_LIMIT
        ):
            return f"[Tool Error] limit must be an integer between 1 and {config.SOFTWARE_ECOSYSTEM_MAX_LIMIT}."

        min_stars = kwargs.get("min_stars")
        if min_stars is not None and (
            isinstance(min_stars, bool) or not isinstance(min_stars, int) or min_stars < 0
        ):
            return "[Tool Error] min_stars must be null or a non-negative integer."

        package_hydration_depth = kwargs.get("package_hydration_depth")
        if package_hydration_depth not in PACKAGE_HYDRATION_DEPTHS:
            return "[Tool Error] package_hydration_depth must be light, standard, or deep."

        try:
            log_event(
                "software_ecosystem_research fetched",
                targets=targets,
                ecosystems=ecosystems,
                languages=languages,
                sort=sort,
                limit=limit,
                min_stars=min_stars,
                package_hydration_depth=package_hydration_depth,
            )
            result = await self._service.research(
                query=query,
                targets=targets,
                ecosystems=ecosystems,
                languages=languages,
                sort=sort,
                limit=limit,
                min_stars=min_stars,
                package_hydration_depth=package_hydration_depth,
            )
            return truncate_result(
                _format_research_result(result),
                max_chars=config.SOFTWARE_ECOSYSTEM_TOOL_RESULT_MAX_CHARS,
            )
        except (InvalidSoftwareEcosystemQueryError, UnsupportedEcosystemError) as e:
            return f"[Tool Error] {e}"
        except SoftwareEcosystemHttpError as e:
            log_error("software_ecosystem_research", e)
            return _map_provider_error(e)
        except Exception as e:
            log_error("software_ecosystem_research", e)
            return "[Tool Error] Software ecosystem provider returned an unexpected response."

    async def close(self) -> None:
        await self._service.close()


def _format_research_result(result: SoftwareEcosystemResearchResult) -> str:
    lines = [
        "[Tool Result] software_ecosystem_research",
        "",
        "Query:",
        result.query,
        "",
        "Scope:",
    ]
    append_key_values(lines, [("targets", result.targets)])
    lines.extend(["", "Summary:", result.summary])
    _append_recommendations(lines, result.recommendations)
    _append_projects(lines, result.recommended_projects)
    _append_packages(lines, result.recommended_packages)
    _append_community(lines, result)
    _append_caveats(lines, result.caveats)
    _append_evidence(lines, result.evidence)
    _append_instructions(lines)
    return "\n".join(lines)


def _append_recommendations(lines: List[str], recommendations: List[str]) -> None:
    lines.extend(["", "Recommendations:"])
    if not recommendations:
        lines.append("- none")
        return
    for item in recommendations:
        lines.append(f"- {item}")


def _append_projects(lines: List[str], projects: List[OpenSourceProjectProfile]) -> None:
    lines.extend(["", "Open-source projects:"])
    if not projects:
        lines.append("- none")
        return
    for index, project in enumerate(projects, 1):
        lines.append(f"[{index}] {project.full_name}")
        append_key_values(
            lines,
            [
                ("url", project.html_url),
                ("description", project.description),
                ("language", project.language),
                ("stars", project.stars),
                ("forks", project.forks),
                ("open_issues", project.open_issues),
                ("license", project.license_name),
                ("archived", project.archived),
                ("updated_at", project.updated_at),
                ("pushed_at", project.pushed_at),
                ("recent_releases", project.recent_releases),
                ("issue_discussion_count", project.issue_discussion_count),
                ("maintenance_score", f"{project.maintenance_score:.2f}"),
                ("popularity_score", f"{project.popularity_score:.2f}"),
                ("activity_score", f"{project.activity_score:.2f}"),
                ("readme_preview", project.readme_preview),
            ],
        )


def _append_packages(lines: List[str], profiles: List[PackageProfile]) -> None:
    lines.extend(["", "Packages:"])
    if not profiles:
        lines.append("- none")
        return
    for index, profile in enumerate(profiles, 1):
        lines.append(f"[{index}] {profile.ecosystem}:{profile.name}")
        append_key_values(
            lines,
            [
                ("selected_version", profile.selected_version),
                ("latest_version", profile.latest_version),
                ("published_at", profile.published_at),
                ("summary", profile.summary),
                ("repository", profile.repository_url),
                ("homepage", profile.homepage_url),
                ("license", profile.license),
                ("deprecated", profile.deprecated),
                ("direct_dependencies", profile.direct_dependencies_count),
                ("transitive_dependencies", profile.transitive_dependencies_count),
                ("recent_versions", profile.recent_versions),
                ("repository_stars", profile.repository_stars),
                ("maintenance_score", f"{profile.maintenance_score:.2f}"),
                ("popularity_score", f"{profile.popularity_score:.2f}"),
                ("dependency_complexity_score", f"{profile.dependency_complexity_score:.2f}"),
                ("ecosystem_score", f"{profile.ecosystem_score:.2f}"),
            ],
        )


def _append_community(lines: List[str], result: SoftwareEcosystemResearchResult) -> None:
    lines.extend(["", "Community discussions:"])
    if not result.community_discussions:
        lines.append("- none")
        return
    for index, signal in enumerate(result.community_discussions[:8], 1):
        lines.append(f"[{index}] {signal.title}")
        append_key_values(
            lines,
            [
                ("source", signal.source),
                ("url", signal.url),
                ("published_at", signal.published_at),
                ("points", signal.points),
                ("comments", signal.comments_count),
                ("summary", signal.summary),
            ],
        )


def _append_caveats(lines: List[str], caveats: List[str]) -> None:
    lines.extend(["", "Caveats:"])
    if not caveats:
        lines.append("- none")
        return
    for item in caveats:
        lines.append(f"- {item}")


def _append_evidence(lines: List[str], evidence: List[str]) -> None:
    lines.extend(["", "Evidence:"])
    if not evidence:
        lines.append("- none")
        return
    for item in evidence[:15]:
        lines.append(f"- {format_scalar(item)}")


def _append_instructions(lines: List[str]) -> None:
    lines.extend(
        [
            "",
            "Assistant instructions:",
            "- Treat open-source projects, packages, and community discussions as separate evidence classes.",
            "- Prefer maintained projects and packages with clear licenses, active releases, and relevant documentation.",
            "- Use community discussions as sentiment and adoption signals, not as authoritative implementation guidance.",
            "- Before changing user code, compare recommendations against dependencies already present in the repo.",
        ]
    )


def _map_provider_error(error: SoftwareEcosystemHttpError) -> str:
    if error.status_code == 404:
        return "[Tool Error] Software ecosystem provider resource not found."
    if error.status_code == 429:
        return "[Tool Error] Software ecosystem provider rate limited."
    if error.status_code is not None and error.status_code >= 500:
        return "[Tool Error] Software ecosystem provider unavailable."
    return "[Tool Error] Software ecosystem provider request failed."
