from __future__ import annotations

from typing import Optional

from cachetools import TTLCache

from ..config import (
    DOI_HYDRATION_NEGATIVE_TTL_SECONDS,
    DOI_HYDRATION_POSITIVE_TTL_SECONDS,
)
from ..models import DOIMetadataRecord


class DOIHydrationCache:
    def __init__(self, *, max_items: int = 512) -> None:
        self._positive: TTLCache[str, DOIMetadataRecord] = TTLCache(
            maxsize=max_items,
            ttl=DOI_HYDRATION_POSITIVE_TTL_SECONDS,
        )
        self._negative: TTLCache[str, str] = TTLCache(
            maxsize=max_items,
            ttl=DOI_HYDRATION_NEGATIVE_TTL_SECONDS,
        )

    def get(self, doi: str) -> Optional[DOIMetadataRecord]:
        return self._positive.get(doi)

    def set(self, doi: str, record: DOIMetadataRecord) -> None:
        self._positive[doi] = record

    def get_negative(self, doi: str) -> Optional[str]:
        return self._negative.get(doi)

    def set_negative(self, doi: str, error_code: str) -> None:
        self._negative[doi] = error_code

    def clear(self) -> None:
        self._positive.clear()
        self._negative.clear()
