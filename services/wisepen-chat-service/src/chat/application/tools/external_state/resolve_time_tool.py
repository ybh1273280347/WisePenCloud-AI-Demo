from typing import Any, Dict, Optional

from chat.application.tools.services.temporal import (
    ResolvedTimeRange,
    TimeResolveError,
    resolve_time_text,
)
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_event

_TOOL_DESCRIPTION = (
    "This tool resolves current clock time, timezone-specific current time, precise "
    "time conversion, and structured natural-language temporal expressions.\n\n"
    "The system prompt already provides the current date as a lightweight date anchor. "
    "Use this tool when the user asks for the current clock time, cross-timezone current "
    "time, precise time conversion, or a structured time expression.\n\n"
    "timezone is optional and must be an IANA timezone.\n"
    "Omit timezone when the user does not specify one; the backend default is Asia/Shanghai.\n"
    "Provide timezone only when the user explicitly specifies an unambiguous timezone, city, or region.\n"
    "Never pass non-IANA aliases such as \"北京时间\", \"Shanghai\", or \"PST\"."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "minLength": 1,
            "description": (
                "The user request or time expression to analyze, such as "
                "最近 Python typing 最佳实践, 今天上海会下雨吗, 当前 OpenAI API 怎么用, "
                "or simply 最近 / 今天 / 上周."
            ),
        },
        "timezone": {
            "type": "string",
            "description": (
                "Optional IANA timezone, such as Asia/Shanghai or America/New_York."
            ),
        },
        "recent_days": {
            "type": "integer",
            "minimum": 1,
            "maximum": 365,
            "default": 30,
            "description": "Default window for ambiguous recency expressions.",
        },
        "domain_sensitivity": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "description": "Optional freshness sensitivity hint.",
        },
    },
    "required": ["text"],
    "additionalProperties": False,
}


class ResolveTimeTool(BaseTool):
    @property
    def name(self) -> str:
        return "resolve_time"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        text = kwargs.get("text")
        if not isinstance(text, str) or not text.strip():
            return "[Tool Error] Missing required text parameter."

        timezone_arg = kwargs.get("timezone")
        timezone_name: Optional[str]
        if timezone_arg is None:
            timezone_name = None
        elif type(timezone_arg) is str:
            timezone_name = timezone_arg
        else:
            return "[Tool Error] timezone must be a valid IANA timezone string."

        default_recent_days = kwargs.get("recent_days", 30)
        domain_sensitivity: Optional[str] = kwargs.get("domain_sensitivity")

        try:
            resolved = resolve_time_text(
                text=text,
                timezone_name=timezone_name,
                default_recent_days=default_recent_days,
                domain_sensitivity=domain_sensitivity,
            )
        except TimeResolveError as e:
            return f"[Tool Error] Failed to resolve time: {e}"

        log_event(
            "resolve_time resolved text",
            input_text=text,
            detected_text=resolved.detected_text,
            mode=resolved.mode.value,
            freshness_policy=resolved.freshness_policy.value,
            timezone=resolved.timezone,
            as_of=resolved.as_of,
            start=resolved.start,
            end=resolved.end,
            domain_sensitivity=domain_sensitivity,
            confidence=resolved.confidence,
        )

        return _format_resolved_time_result(resolved)


def _format_resolved_time_result(resolved: ResolvedTimeRange) -> str:
    start = resolved.start if resolved.start is not None else "none"
    end = resolved.end if resolved.end is not None else "none"
    limit = str(resolved.limit) if resolved.limit is not None else "none"
    detected_text = (
        resolved.detected_text if resolved.detected_text is not None else "none"
    )
    mention_source = (
        resolved.mention_source if resolved.mention_source is not None else "none"
    )

    lines = [
        "[Tool Result] resolve_time",
        "",
        f"Input text: {resolved.input_text}",
        f"Detected temporal mention: {detected_text}",
        f"Mention source: {mention_source}",
        f"As of: {resolved.as_of}",
        f"Timezone: {resolved.timezone}",
        f"Mode: {resolved.mode.value}",
        f"Freshness policy: {resolved.freshness_policy.value}",
        f"Resolved range: [{start}, {end})",
        f"Order by time desc: {str(resolved.order_by_time_desc).lower()}",
        f"Limit: {limit}",
        f"Confidence: {resolved.confidence:.2f}",
        f"Explanation: {resolved.explanation}",
    ]

    lines.append("")
    lines.append("Ambiguities:")
    if resolved.ambiguities:
        for item in resolved.ambiguities:
            lines.append(f"- {item}")
    else:
        lines.append("- none")

    if resolved.alternatives:
        lines.append("")
        lines.append("Alternatives:")
        for alt in resolved.alternatives:
            lines.append(f"- {alt}")

    lines.extend(
        [
            "",
            "Next-step guidance:",
            "- Use this result as the authoritative time basis.",
            "- Continue according to the user's request.",
            "- When using another tool, apply this time information to its query, filters, ordering, or freshness judgment when relevant.",
            "- Do not reinterpret the original time expression from model memory or stale context.",
            "- The resolved end time is exclusive.",
        ]
    )

    return "\n".join(lines)
