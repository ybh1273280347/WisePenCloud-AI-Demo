from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from chat.application.web_search.internal.models.helpers import is_valid_result
from chat.application.web_search.models.common import SearchResponse, SearchResult

ANYSEARCH_PROVIDER = "anysearch"
ANYSEARCH_ALLOWED_ZONES = frozenset({"cn", "intl"})


@dataclass(frozen=True, slots=True)
class AnySearchRequest:
    query: str
    max_results: int
    language: Optional[str] = None
    zone: Optional[str] = None

    def __post_init__(self) -> None:
        if type(self.query) is not str or not self.query.strip():
            raise ValueError("query must be a non-empty string")

        if type(self.max_results) is not int:
            raise ValueError("max_results must be an int")

        if not 1 <= self.max_results <= 100:
            raise ValueError("max_results must be between 1 and 100")

        if self.language is not None and (
            type(self.language) is not str or not self.language.strip()
        ):
            raise ValueError("language must be None or a non-empty string")

        if self.zone is not None and (
            type(self.zone) is not str or self.zone not in ANYSEARCH_ALLOWED_ZONES
        ):
            raise ValueError("zone must be None, 'cn', or 'intl'")

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "query": self.query,
            "max_results": self.max_results,
        }

        if self.language:
            payload["language"] = self.language

        if self.zone:
            payload["zone"] = self.zone

        return payload


def map_anysearch_response(
    data: Mapping[str, Any],
    *,
    query: str,
    source: str,
) -> SearchResponse:
    raw_items = data.get("results")
    if not isinstance(raw_items, list):
        raw_items = []

    results = []
    for raw_rank, item in enumerate(raw_items, 1):
        if not isinstance(item, Mapping):
            continue

        result = _map_anysearch_result(item, raw_rank=raw_rank, source=source)
        if is_valid_result(result):
            results.append(result)

    return SearchResponse(
        query=query,
        results=tuple(results),
        source=source,
        metadata={
            "provider": ANYSEARCH_PROVIDER,
            "raw_metadata": data.get("metadata"),
        },
    )


def _map_anysearch_result(
    item: Mapping[str, Any],
    *,
    raw_rank: int,
    source: str,
) -> SearchResult:
    url = item.get("url")
    title = item.get("title")
    description = item.get("description")

    url_text = url if isinstance(url, str) else ""
    title_text = title if isinstance(title, str) and title.strip() else url_text
    snippet = description if isinstance(description, str) else ""

    metadata: Dict[str, Any] = {
        "raw_rank": raw_rank,
        "provider": ANYSEARCH_PROVIDER,
    }
    _copy_metadata_field(metadata, item, "content")
    _copy_metadata_field(metadata, item, "source", target="provider_source")
    _copy_metadata_field(metadata, item, "score", target="provider_score")
    _copy_metadata_field(
        metadata,
        item,
        "quality_score",
        target="provider_quality_score",
    )
    _copy_metadata_field(metadata, item, "published_at")
    _copy_metadata_field(metadata, item, "raw_content")

    return SearchResult(
        title=title_text,
        url=url_text,
        snippet=snippet,
        metadata=metadata,
    )


def _copy_metadata_field(
    metadata: Dict[str, Any],
    item: Mapping[str, Any],
    key: str,
    *,
    target: Optional[str] = None,
) -> None:
    if key in item:
        metadata[target or key] = item.get(key)
