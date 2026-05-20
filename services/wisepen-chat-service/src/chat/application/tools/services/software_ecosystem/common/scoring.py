from __future__ import annotations

import math
from datetime import datetime, timezone


def bounded_log_score(value: int | None, *, scale: float = 5.0) -> float:
    if value is None or value <= 0:
        return 0.0
    return min(1.0, math.log10(value + 1) / scale)


def iso_datetime_recency_score(value: str | None, *, half_life_days: float = 365.0) -> float:
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return 0.0
    age_days = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0)
    return 1.0 / (1.0 + (age_days / half_life_days))


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

