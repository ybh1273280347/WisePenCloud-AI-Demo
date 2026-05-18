from __future__ import annotations

from .models import TranslationAssistResult


def format_translation_result(result: TranslationAssistResult) -> str:
    lines = [
        "[Tool Result] translation_assist",
        "",
        f"Source language: {result.source_language}",
        f"Target language: {result.target_language}",
        f"Backend: {result.backend}",
        f"Mode: {result.mode}",
        "",
        "Segments:",
    ]

    for segment in result.segments:
        lines.extend(
            [
                f"[{segment.index}]",
                f"source: {segment.source}",
                f"baseline_translation: {segment.baseline_translation}",
                "",
            ]
        )

    lines.append("Terminology:")
    if result.terminology:
        for issue in result.terminology:
            lines.extend(
                [
                    f"- source: {issue.source}",
                    f"  expected_target: {issue.expected_target}",
                    f"  status: {issue.status}",
                    f"  message: {issue.message}",
                ]
            )
    else:
        lines.append("- none")

    lines.append("")
    lines.append("Warnings:")
    if result.warnings:
        lines.extend(f"- {warning}" for warning in result.warnings)
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "Assistant instructions:",
            "- Use this as Chinese-English translation support, not necessarily the final answer.",
            "- Preserve the user's intended meaning, tone, domain terms, and formatting.",
            "- Enforce glossary terms when provided, unless doing so would make the translation unnatural; explain any exception.",
            "- If this tool fails or the requested language pair is unsupported, continue translating directly using the assistant's own language ability.",
        ]
    )

    return "\n".join(lines).strip()


def format_translation_error(reason: str) -> str:
    return "\n".join(
        [
            f"[Tool Error] translation_assist failed: {reason}",
            "",
            "Assistant fallback instructions:",
            "- The translation assistance tool failed, but the assistant can still translate using its own language ability.",
            "- Do not tell the user that translation cannot be done solely because this tool failed.",
            "- Continue to translate the user's text directly.",
            "- Preserve the requested language, tone, formatting, and glossary constraints as much as possible.",
            "- If the tool failed because of an unsupported language pair, translate directly without using the tool.",
        ]
    )
