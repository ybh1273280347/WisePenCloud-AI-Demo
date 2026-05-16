from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from chat.application.web_search.models.common import SearchResponse, SearchResult
from chat.application.web_search.models.helpers import is_valid_result


@dataclass(frozen=True, slots=True)
class BraveSearchRequest:
    query: str
    count: int
    language: Optional[str] = None

    def to_params(self) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "q": self.query,
            "count": self.count,
            "safesearch": "moderate",
        }
        params.update(brave_locale_params(self.language))
        return params


def brave_locale_params(language: Optional[str]) -> Dict[str, str]:
    if language and language.strip().lower() == "zh":
        return {"country": "cn", "search_lang": "zh-hans", "ui_lang": "zh-CN"}

    return {"country": "us", "search_lang": "en", "ui_lang": "en-US"}


def map_brave_response(
    data: Mapping[str, Any],
    *,
    query: str,
    source: str,
) -> SearchResponse:
    web = data.get("web") or {}
    raw_items = web.get("results") if isinstance(web, Mapping) else []
    if not isinstance(raw_items, list):
        raw_items = []

    results = tuple(
        result
        for item in raw_items
        if isinstance(item, Mapping)
        for result in (_map_brave_result(item),)
        if is_valid_result(result)
    )

    return SearchResponse(query=query, results=results, source=source)


def _map_brave_result(item: Mapping[str, Any]) -> SearchResult:
    return SearchResult(
        title=str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        snippet=str(item.get("description") or ""),
    )
