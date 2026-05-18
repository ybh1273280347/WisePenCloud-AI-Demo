from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from recognizers_date_time import recognize_datetime
from recognizers_text import Culture

from .models import (
    FreshnessPolicy,
    ResolvedTimeRange,
    TemporalMention,
    TemporalMentionSource,
    TimeResolutionMode,
    TimeResolveError,
)


_TYPE_PRIORITY = {
    "datetimeV2.daterange": 100,
    "daterange": 100,
    "datetimeV2.datetimerange": 100,
    "datetimerange": 100,
    "datetimeV2.date": 90,
    "date": 90,
    "datetimeV2.datetime": 90,
    "datetime": 90,
    "datetimeV2.time": 80,
    "time": 80,
    "datetimeV2.duration": 70,
    "duration": 70,
}


def resolve_time_text(
    *,
    text: str,
    timezone_name: str,
    locale: Optional[str] = None,
    default_recent_days: int = 30,
    domain_sensitivity: Optional[str] = None,
) -> ResolvedTimeRange:
    del default_recent_days, domain_sensitivity

    input_text = _validate_text(text)

    tz = _load_timezone(timezone_name)
    now_local = datetime.now(tz)

    mentions = _extract_mentions(input_text, locale)
    if not mentions:
        return _build_current_time_anchor(
            input_text=input_text,
            timezone_name=timezone_name,
            now_local=now_local,
            reason=(
                "No explicit temporal expression was detected. Returned the server "
                "current time as the authoritative time anchor."
            ),
        )

    primary = _choose_primary_mention(mentions)

    try:
        return _normalize_mention(
            input_text=input_text,
            mention=primary,
            all_mentions=mentions,
            timezone_name=timezone_name,
            now_local=now_local,
        )
    except TimeResolveError:
        return _build_current_time_anchor(
            input_text=input_text,
            timezone_name=timezone_name,
            now_local=now_local,
            reason=(
                "A temporal mention was detected, but its recognizer resolution could not be "
                "mapped to a deterministic range. Returned the server current time as the "
                "authoritative time anchor."
            ),
            detected_text=primary.text,
            mention_source=primary.source.value,
        )


def _validate_text(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise TimeResolveError("text must be a non-empty string.")
    return text.strip()


def _load_timezone(timezone_name: str) -> ZoneInfo:
    if not isinstance(timezone_name, str) or not timezone_name:
        raise TimeResolveError("timezone_name must be a non-empty IANA timezone.")
    if timezone_name != timezone_name.strip():
        raise TimeResolveError("timezone_name must be a valid IANA timezone.")

    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as e:
        raise TimeResolveError(f"Unknown IANA timezone: {timezone_name}") from e


def _extract_mentions(
    text: str,
    locale: Optional[str] = None,
) -> List[TemporalMention]:
    mentions: List[TemporalMention] = []

    for culture in _culture_order(locale):
        for item in recognize_datetime(text, culture):
            mentions.append(
                TemporalMention(
                    text=item.text,
                    source=TemporalMentionSource.RECOGNIZERS,
                    start_index=item.start,
                    end_index=item.end + 1,
                    type_name=item.type_name,
                    confidence=0.85,
                    raw={
                        "culture": culture,
                        "type_name": item.type_name,
                        "resolution": item.resolution,
                    },
                )
            )

    return _deduplicate_mentions(mentions)


def _culture_order(locale: Optional[str]) -> Tuple[str, ...]:
    if locale in {"en-US", "en-GB"}:
        return (Culture.English, Culture.Chinese)
    return (Culture.Chinese, Culture.English)


def _deduplicate_mentions(mentions: List[TemporalMention]) -> List[TemporalMention]:
    best_by_key: Dict[tuple[int, int, str, str], TemporalMention] = {}

    for mention in mentions:
        key = (
            mention.start_index,
            mention.end_index,
            mention.text,
            mention.type_name,
        )
        existing = best_by_key.get(key)
        if existing is None or mention.confidence > existing.confidence:
            best_by_key[key] = mention

    return sorted(
        best_by_key.values(),
        key=lambda item: (item.start_index, item.end_index),
    )


def _choose_primary_mention(mentions: List[TemporalMention]) -> TemporalMention:
    return max(
        mentions,
        key=lambda item: (
            _TYPE_PRIORITY.get(item.type_name, 0),
            item.confidence,
            item.end_index - item.start_index,
        ),
    )


def _normalize_mention(
    *,
    input_text: str,
    mention: TemporalMention,
    all_mentions: List[TemporalMention],
    timezone_name: str,
    now_local: datetime,
) -> ResolvedTimeRange:
    values = _get_resolution_values(mention.raw)
    if not values:
        raise TimeResolveError(
            f"Recognizer result has no resolution values: {mention.text}"
        )

    selected = _pick_resolution_value(values)

    start_raw = selected.get("start")
    end_raw = selected.get("end")
    value_raw = selected.get("value")

    mode = _infer_mode(mention, selected)
    freshness_policy = _infer_freshness_policy(mode)

    start = _parse_resolution_datetime(start_raw, now_local) if start_raw else None
    end = _parse_resolution_datetime(end_raw, now_local) if end_raw else None

    if start is None and value_raw:
        start = _parse_resolution_datetime(value_raw, now_local)

    if start is None and end is None:
        raise TimeResolveError(
            f"Recognizer result cannot be mapped to a deterministic time result: {mention.text}"
        )

    order_by_time_desc = mode in {
        TimeResolutionMode.RECENCY,
        TimeResolutionMode.LATEST,
        TimeResolutionMode.CURRENT,
    }
    limit = 1 if mode in {TimeResolutionMode.LATEST, TimeResolutionMode.CURRENT} else None

    return ResolvedTimeRange(
        input_text=input_text,
        detected_text=mention.text,
        mention_source=mention.source.value,
        mode=mode,
        freshness_policy=freshness_policy,
        timezone=timezone_name,
        as_of=_format_dt(now_local),
        start=_format_dt(start) if start is not None else None,
        end=_format_dt(end) if end is not None else _format_dt(now_local),
        confidence=mention.confidence,
        explanation=(
            f"Resolved temporal mention {mention.text!r} using Microsoft Recognizers Text "
            "as of server system time."
        ),
        order_by_time_desc=order_by_time_desc,
        limit=limit,
        ambiguities=_format_ambiguities(mention, all_mentions),
        alternatives=_format_alternatives(mention, all_mentions),
    )


def _get_resolution_values(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    resolution = raw.get("resolution") or {}

    values = resolution.get("values")
    if isinstance(values, list):
        return [item for item in values if isinstance(item, dict)]

    if isinstance(resolution, dict):
        return [resolution]

    return []


def _pick_resolution_value(values: List[Dict[str, Any]]) -> Dict[str, Any]:
    for value in values:
        if "start" in value and "end" in value:
            return value

    for value in values:
        if "value" in value:
            return value

    return values[0]


def _infer_mode(
    mention: TemporalMention,
    resolution_value: Dict[str, Any],
) -> TimeResolutionMode:
    type_name = mention.type_name.lower()
    text = mention.text.lower()

    if "latest" in text or "最新" in text or "最近一次" in text:
        return TimeResolutionMode.LATEST

    if "current" in text or "当前" in text or "现在" in text:
        return TimeResolutionMode.CURRENT

    if "daterange" in type_name or "datetimerange" in type_name:
        return TimeResolutionMode.RANGE

    if "duration" in type_name:
        return TimeResolutionMode.RECENCY

    if "start" in resolution_value and "end" in resolution_value:
        return TimeResolutionMode.RANGE

    if "date" in type_name or "time" in type_name:
        return TimeResolutionMode.EXACT

    return TimeResolutionMode.UNKNOWN


def _infer_freshness_policy(mode: TimeResolutionMode) -> FreshnessPolicy:
    if mode == TimeResolutionMode.LATEST:
        return FreshnessPolicy.LATEST_ONLY
    if mode == TimeResolutionMode.CURRENT:
        return FreshnessPolicy.TIME_SENSITIVE
    if mode == TimeResolutionMode.RECENCY:
        return FreshnessPolicy.PREFER_RECENT
    if mode == TimeResolutionMode.RANGE:
        return FreshnessPolicy.MUST_BE_RECENT
    return FreshnessPolicy.ANY


def _build_current_time_anchor(
    *,
    input_text: str,
    timezone_name: str,
    now_local: datetime,
    reason: str,
    detected_text: Optional[str] = None,
    mention_source: Optional[str] = None,
) -> ResolvedTimeRange:
    ambiguity = (
        "No explicit time range was detected; only the server current time anchor is available."
        if detected_text is None
        else "A temporal mention was detected but could not be mapped to a deterministic range; only the server current time anchor is available."
    )

    return ResolvedTimeRange(
        input_text=input_text,
        detected_text=detected_text,
        mention_source=mention_source,
        mode=TimeResolutionMode.CURRENT,
        freshness_policy=FreshnessPolicy.TIME_SENSITIVE,
        timezone=timezone_name,
        as_of=_format_dt(now_local),
        start=None,
        end=_format_dt(now_local),
        confidence=0.5,
        explanation=reason,
        order_by_time_desc=True,
        limit=None,
        ambiguities=[ambiguity],
        alternatives=[],
    )


def _parse_resolution_datetime(
    value: str,
    now_local: datetime,
) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as e:
        raise TimeResolveError(f"Invalid datetime from recognizer resolution: {value}") from e

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=now_local.tzinfo)

    return parsed.astimezone(now_local.tzinfo)


def _format_dt(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _format_ambiguities(
    primary: TemporalMention,
    mentions: List[TemporalMention],
) -> List[str]:
    if len(mentions) <= 1:
        return []

    detected = ", ".join(mention.text for mention in mentions)
    return [
        f"Multiple temporal mentions were detected: {detected}. "
        f"Selected primary mention: {primary.text}."
    ]


def _format_alternatives(
    primary: TemporalMention,
    mentions: List[TemporalMention],
) -> List[Dict[str, Any]]:
    alternatives: List[Dict[str, Any]] = []

    for mention in mentions:
        if mention is primary:
            continue

        alternatives.append(
            {
                "text": mention.text,
                "type_name": mention.type_name,
                "source": mention.source.value,
                "confidence": mention.confidence,
            }
        )

    return alternatives
