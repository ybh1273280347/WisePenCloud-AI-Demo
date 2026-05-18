from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


class TranslationAssistError(ValueError):
    """Raised when translation assistance cannot produce a baseline."""


@dataclass(frozen=True, slots=True)
class GlossaryTerm:
    source: str
    target: str


@dataclass(frozen=True, slots=True)
class TerminologyIssue:
    source: str
    expected_target: str
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class TranslationSegment:
    index: int
    source: str
    baseline_translation: str


@dataclass(frozen=True, slots=True)
class TranslationAssistResult:
    source_language: str
    target_language: str
    mode: str
    backend: str
    segments: List[TranslationSegment]
    terminology: List[TerminologyIssue] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
