from dataclasses import dataclass
from typing import Any, List, Mapping, Dict

from chat.application.tools.web.services.web_search.enums import SearcherName
from chat.application.tools.web.services.web_search.models import SearchResponse, SearchResult
from chat.application.tools.web.services.web_search.utils.results import deduplicate_results_by_domain, is_valid_result


@dataclass(frozen=True, slots=True)
class FourGetSearchRequest:
    query: str
    scraper: str

    def to_params(self):
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

    raw_items = data.get("web") or []
    if not isinstance(raw_items, list):
        raw_items = []

    results: List[SearchResult] = []
    metadata: Dict[str, Any] = {
        "scraper": scraper,
    }

    for item in raw_items:

        if not isinstance(item, Mapping):
            continue

        result = SearchResult(
            title=str(item.get("title") or "").strip(),
            url=str(item.get("urls") or "").strip(),
            snippet=str(item.get("snippet") or "").strip(),
            metadata=metadata,
        )

        if is_valid_result(result):
            results.append(result)

    results = deduplicate_results_by_domain(results, max_per_domain=2)

    return SearchResponse(
        query=query,
        results=results[:max_results],
        source=SearcherName.FOURGET,
    )