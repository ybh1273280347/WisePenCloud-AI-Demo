from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from chat.application.web_search.models.common import SearchResult, SearchResponse


def is_valid_result(result: SearchResult) -> bool:
    title = result.title.strip()
    url = result.url.strip()
    snippet = result.snippet.strip()

    if not url:
        return False

    if not title and not snippet:
        return False

    if len(title) + len(snippet) < 12:
        return False

    return True


def normalize_optional_str(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    value = value.strip()
    return value or None


def to_optional_str(value) -> Optional[str]:
    if value is None:
        return None

    return str(value)


def has_response_content(response: SearchResponse) -> bool:
    return bool(response.answer or response.results or response.images)