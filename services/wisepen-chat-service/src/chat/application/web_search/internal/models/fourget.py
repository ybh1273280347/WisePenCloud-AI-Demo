from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Tuple

from chat.application.web_search.internal.models.helpers import is_valid_result
from chat.application.web_search.models.common import SearchResponse, SearchResult

FOURGET_ALLOWED_ENDPOINTS = frozenset({"web"})
FOURGET_ALLOWED_WEB_SCRAPERS = frozenset({"ddg", "yandex"})
FOURGET_SOURCE = "fourget"


@dataclass(frozen=True, slots=True)
class FourGetSearchRequest:
    query: str
    scraper: str
    endpoint: str = "web"

    def __post_init__(self) -> None:
        if type(self.query) is not str or not self.query.strip():
            raise ValueError("query must be a non-empty string")

        if type(self.scraper) is not str or not self.scraper.strip():
            raise ValueError("scraper must be a non-empty string")

        if type(self.endpoint) is not str or not self.endpoint.strip():
            raise ValueError("endpoint must be a non-empty string")

        if self.endpoint not in FOURGET_ALLOWED_ENDPOINTS:
            raise ValueError("endpoint must be 'web'")

        if self.scraper not in FOURGET_ALLOWED_WEB_SCRAPERS:
            raise ValueError("scraper must be one of: ddg, yandex")

    def to_params(self) -> Dict[str, str]:
        return {
            "s": self.query,
            "scraper": self.scraper,
        }


def map_fourget_response(
    data: Mapping[str, Any],
    *,
    query: str,
    scraper: str,
    max_results: int,
) -> SearchResponse:
    if not isinstance(data, Mapping):
        raise ValueError("4get response data must be a mapping")

    raw_items = data.get("web")
    if not isinstance(raw_items, list):
        raise ValueError("4get response web field must be a list")

    results = []

    for raw_rank, item in enumerate(raw_items, 1):
        if len(results) >= max_results:
            break

        if not isinstance(item, Mapping):
            continue

        result = _map_fourget_web_item(
            item,
            query=query,
            scraper=scraper,
            raw_rank=raw_rank,
        )
        if not is_valid_result(result):
            continue

        results.append(result)

    return SearchResponse(
        query=query,
        results=tuple(results),
        source=FOURGET_SOURCE,
    )


def _map_fourget_web_item(
    item: Mapping[str, Any],
    *,
    query: str,
    scraper: str,
    raw_rank: int,
) -> SearchResult:
    title = item.get("title")
    url = item.get("url")
    description = item.get("description")

    metadata = _fourget_metadata(
        item,
        query=query,
        scraper=scraper,
        raw_rank=raw_rank,
    )

    return SearchResult(
        title=title if isinstance(title, str) else "",
        url=url if isinstance(url, str) else "",
        snippet=description if isinstance(description, str) else "",
        metadata=metadata,
    )


def _fourget_metadata(
    item: Mapping[str, Any],
    *,
    query: str,
    scraper: str,
    raw_rank: int,
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "raw_rank": raw_rank,
        "provider": FOURGET_SOURCE,
        "scraper": scraper,
        "query": query,
    }

    _copy_metadata_field(metadata, item, "date")
    _copy_metadata_field(metadata, item, "type")
    _copy_metadata_field(metadata, item, "thumb")
    _copy_metadata_field(metadata, item, "sublink")
    _copy_metadata_field(metadata, item, "table")

    return metadata


def _copy_metadata_field(
    metadata: Dict[str, Any],
    item: Mapping[str, Any],
    key: str,
) -> None:
    if key in item:
        metadata[key] = item.get(key)
