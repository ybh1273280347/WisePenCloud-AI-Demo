"""Public web search application API."""

from chat.application.web_search.errors import (
    CustomSearchProviderUnavailableError,
    EmptySearchResultError,
    SearchProviderError,
    SearchRateLimitError,
    SearchTimeoutError,
    WebSearchError,
)
from chat.application.web_search.models.common import (
    ImageResult,
    SearchResponse,
    SearchResult,
)
from chat.application.web_search.internal.planning.models import WikipediaGroundingResult

__all__ = [
    "CustomSearchProviderUnavailableError",
    "EmptySearchResultError",
    "ImageResult",
    "SearchCoordinator",
    "SearchManyRequest",
    "SearchManyResult",
    "SearchProviderError",
    "SearchRateLimitError",
    "SearchResponse",
    "SearchResult",
    "SearchTimeoutError",
    "WebSearchError",
    "WikipediaGroundingResult",
]


def __getattr__(name: str):
    if name in {"SearchCoordinator", "SearchManyRequest", "SearchManyResult"}:
        from chat.application.web_search.internal.search_coordinator import (
            SearchCoordinator,
            SearchManyRequest,
            SearchManyResult,
        )

        exports = {
            "SearchCoordinator": SearchCoordinator,
            "SearchManyRequest": SearchManyRequest,
            "SearchManyResult": SearchManyResult,
        }
        globals().update(exports)
        return exports[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
