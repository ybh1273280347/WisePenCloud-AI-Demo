from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from chat.application.web_search.models.common import SearchResponse, SearchResult
from chat.application.web_search.models.helpers import is_valid_result


@dataclass(frozen=True, slots=True)
class SerpApiSearchRequest:
    query: str
    api_key: str
    num: int
    language: Optional[str] = None

    def to_params(self) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "engine": "google",
            "q": self.query,
            "api_key": self.api_key,
            "num": self.num,
        }
        params.update(serpapi_locale_params(self.language))
        return params


def serpapi_locale_params(language: Optional[str]) -> Dict[str, str]:
    if language and language.strip().lower() == "zh":
        return {"hl": "zh-cn", "gl": "cn"}

    return {"hl": "en", "gl": "us"}


def map_serpapi_response(
    data: Mapping[str, Any],
    *,
    query: str,
    source: str,
) -> SearchResponse:
    raw_items = data.get("organic_results") or []
    if not isinstance(raw_items, list):
        raw_items = []

    results = tuple(
        result
        for item in raw_items
        if isinstance(item, Mapping)
        for result in (_map_serpapi_organic_result(item),)
        if is_valid_result(result)
    )

    return SearchResponse(query=query, results=results, source=source)


def _map_serpapi_organic_result(item: Mapping[str, Any]) -> SearchResult:
    return SearchResult(
        title=str(item.get("title") or ""),
        url=str(item.get("link") or ""),
        snippet=str(item.get("snippet") or ""),
    )
