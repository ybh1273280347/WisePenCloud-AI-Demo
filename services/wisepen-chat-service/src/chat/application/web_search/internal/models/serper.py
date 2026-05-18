from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from chat.application.web_search.models.common import SearchResponse, SearchResult
from chat.application.web_search.internal.models.helpers import is_valid_result


@dataclass(frozen=True, slots=True)
class SerperSearchRequest:
    query: str
    num: int
    language: Optional[str] = None

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "q": self.query,
            "num": self.num,
        }
        payload.update(serper_locale_params(self.language))
        return payload


def serper_locale_params(language: Optional[str]) -> Dict[str, str]:
    if language and language.strip().lower() == "zh":
        return {"hl": "zh-cn", "gl": "cn"}

    return {"hl": "en", "gl": "us"}


def map_serper_response(
    data: Mapping[str, Any],
    *,
    query: str,
    source: str,
) -> SearchResponse:
    raw_items = data.get("organic") or []
    if not isinstance(raw_items, list):
        raw_items = []

    results = tuple(
        result
        for item in raw_items
        if isinstance(item, Mapping)
        for result in (_map_serper_organic_result(item),)
        if is_valid_result(result)
    )

    return SearchResponse(query=query, results=results, source=source)


def _map_serper_organic_result(item: Mapping[str, Any]) -> SearchResult:
    return SearchResult(
        title=str(item.get("title") or ""),
        url=str(item.get("link") or ""),
        snippet=str(item.get("snippet") or ""),
    )
