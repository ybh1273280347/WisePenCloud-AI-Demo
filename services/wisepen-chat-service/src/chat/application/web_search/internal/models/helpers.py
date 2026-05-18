from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from chat.application.web_search.models.common import SearchResponse, SearchResult


def is_valid_result(result: SearchResult) -> bool:
    title = result.title.strip()
    url = result.url.strip()
    snippet = result.snippet.strip()

    if not url or (not title and not snippet):
        return False

    return True


def normalize_optional_str(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    return value.strip() or None


def to_optional_str(value: object) -> Optional[str]:
    return None if value is None else str(value)


def has_response_content(response: SearchResponse) -> bool:
    return any(is_valid_result(result) for result in response.results)
