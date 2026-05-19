from __future__ import annotations

from datetime import date
from typing import Optional

from .models import PaperEntity, PaperSearchFreshness

_RECENCY_HALF_LIFE_DAYS = {
    PaperSearchFreshness.LATEST: 30,
    PaperSearchFreshness.BALANCED: 365,
    PaperSearchFreshness.STABLE: 1825,
}


def compute_recency_score(
    entity: PaperEntity,
    freshness: PaperSearchFreshness,
    reference_date: date,
) -> float:
    pub_date = _parse_publication_date(entity.publication_date, entity.year)
    if pub_date is None:
        return 0.5

    days_ago = max(0, (reference_date - pub_date).days)
    half_life = _RECENCY_HALF_LIFE_DAYS[freshness]

    return 0.5 ** (days_ago / half_life)


def _parse_publication_date(
    publication_date: Optional[str],
    year: Optional[int],
) -> Optional[date]:
    if publication_date:
        parsed = _parse_iso_date(publication_date)
        if parsed is not None:
            return parsed

    if year is not None:
        return date(year, 1, 1)

    return None


def _parse_iso_date(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
