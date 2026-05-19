from __future__ import annotations

from typing import List

from .config import QUERY_VARIANTS_LIMIT_DEEP, QUERY_VARIANTS_LIMIT_FAST
from .models import PaperSearchDepth, PaperSearchRequest
from .query import normalize_query


def build_query_variants(request: PaperSearchRequest) -> List[str]:
    limit = (
        QUERY_VARIANTS_LIMIT_DEEP
        if request.depth == PaperSearchDepth.DEEP
        else QUERY_VARIANTS_LIMIT_FAST
    )

    variants = [normalize_query(request.query)]

    for variant in request.query_variants:
        normalized = " ".join(variant.split()).strip()
        if normalized not in variants:
            variants.append(normalized)

    return variants[:limit]
