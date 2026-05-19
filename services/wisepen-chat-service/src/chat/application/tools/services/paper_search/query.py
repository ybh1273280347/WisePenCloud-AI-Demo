from __future__ import annotations

from .config import PAPER_SEARCH_MAX_RESULTS
from .models import PaperSearchDepth, PaperSearchFreshness, PaperSearchRequest


def normalize_query(query: str) -> str:
    if not isinstance(query, str):
        raise TypeError("query must be str.")

    normalized = " ".join(query.split()).strip()
    if not normalized:
        raise ValueError("query is required.")

    return normalized


def validate_request(request: PaperSearchRequest) -> None:
    if not isinstance(request.query, str):
        raise TypeError("query must be str.")

    if not isinstance(request.max_results, int) or isinstance(request.max_results, bool):
        raise TypeError("max_results must be int.")

    if request.max_results < 1 or request.max_results > PAPER_SEARCH_MAX_RESULTS:
        raise ValueError(
            f"max_results must be between 1 and {PAPER_SEARCH_MAX_RESULTS}."
        )

    if not isinstance(request.freshness, PaperSearchFreshness):
        raise TypeError("freshness must be PaperSearchFreshness.")

    if not isinstance(request.depth, PaperSearchDepth):
        raise TypeError("depth must be PaperSearchDepth.")

    for variant in request.query_variants:
        if not isinstance(variant, str):
            raise TypeError("query_variants must contain str.")
        if not variant.strip():
            raise ValueError("query_variants must not contain empty strings.")
