from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping

from chat.application.web_search.models.common import SearchResponse, SearchResult
from chat.application.web_search.internal.models.helpers import is_valid_result


@dataclass(frozen=True, slots=True)
class PerplexitySearchRequest:
    query: str
    max_results: int

    def to_payload(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "max_results": self.max_results,
        }


def map_perplexity_response(
    data: Mapping[str, Any],
    *,
    query: str,
    source: str,
) -> SearchResponse:
    raw_items = data.get("results") or []
    if not isinstance(raw_items, list):
        raw_items = []

    results = tuple(
        result
        for item in raw_items
        if isinstance(item, Mapping)
        for result in (_map_perplexity_result(item),)
        if is_valid_result(result)
    )

    return SearchResponse(query=query, results=results, source=source)


def _map_perplexity_result(item: Mapping[str, Any]) -> SearchResult:
    return SearchResult(
        title=str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        snippet=str(item.get("snippet") or ""),
    )
