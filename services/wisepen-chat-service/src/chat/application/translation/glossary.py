from __future__ import annotations

from typing import Any, List

from .models import GlossaryTerm, TerminologyIssue, TranslationAssistError


def parse_glossary(value: Any) -> List[GlossaryTerm]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TranslationAssistError("glossary must be an array of {source, target}.")

    terms: List[GlossaryTerm] = []
    for item in value:
        if not isinstance(item, dict):
            raise TranslationAssistError("glossary items must be objects.")
        source = item.get("source")
        target = item.get("target")
        if not isinstance(source, str) or not source.strip():
            raise TranslationAssistError("glossary source must be a non-empty string.")
        if not isinstance(target, str) or not target.strip():
            raise TranslationAssistError("glossary target must be a non-empty string.")
        terms.append(GlossaryTerm(source=source.strip(), target=target.strip()))
    return terms


def check_glossary(
    *,
    source_text: str,
    translated_text: str,
    glossary: List[GlossaryTerm],
) -> List[TerminologyIssue]:
    issues: List[TerminologyIssue] = []

    for term in glossary:
        if term.source not in source_text:
            continue

        if term.target in translated_text:
            issues.append(
                TerminologyIssue(
                    source=term.source,
                    expected_target=term.target,
                    status="matched",
                    message="Expected target term was found.",
                )
            )
        else:
            issues.append(
                TerminologyIssue(
                    source=term.source,
                    expected_target=term.target,
                    status="missing",
                    message="Expected target term was not found in baseline translation.",
                )
            )

    return issues
