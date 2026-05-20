from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Dict, List, Optional


class TimeResolutionMode(StrEnum):
    NO_CONSTRAINT = "no_constraint"
    EXACT = "exact"
    RANGE = "range"
    RECENCY = "recency"
    LATEST = "latest"
    CURRENT = "current"
    UNKNOWN = "unknown"


class FreshnessPolicy(StrEnum):
    ANY = "any"
    PREFER_RECENT = "prefer_recent"
    MUST_BE_RECENT = "must_be_recent"
    LATEST_ONLY = "latest_only"
    TIME_SENSITIVE = "time_sensitive"


class TemporalMentionSource(StrEnum):
    RECOGNIZERS = "recognizers"


@dataclass(frozen=True, slots=True)
class TemporalMention:
    text: str
    source: TemporalMentionSource
    start_index: int
    end_index: int
    type_name: str
    confidence: float
    raw: Dict[str, Any]


@dataclass(frozen=True, slots=True)
class ResolvedTimeRange:
    input_text: str
    detected_text: Optional[str]
    mention_source: Optional[str]
    mode: TimeResolutionMode
    freshness_policy: FreshnessPolicy
    timezone: str
    as_of: str
    start: Optional[str]
    end: Optional[str]
    confidence: float
    explanation: str
    order_by_time_desc: bool = False
    limit: Optional[int] = None
    ambiguities: List[str] = field(default_factory=list)
    alternatives: List[dict[str, Any]] = field(default_factory=list)


class TimeResolveError(ValueError):
    pass
