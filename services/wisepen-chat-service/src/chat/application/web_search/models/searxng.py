from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from chat.application.web_search.models.common import (
    ImageResult,
    SearchResponse,
    SearchResult,
)
from chat.application.web_search.models.helpers import is_valid_result, to_optional_str
from chat.application.web_search.utils.domains import deduplicate_results_by_domain
from chat.application.web_search.utils.images import deduplicate_images
from common.logger import log_event


def _map_searxng_result(item: Mapping[str, Any]) -> SearchResult:
    return SearchResult(
        title=str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        snippet=str(item.get("content") or item.get("snippet") or ""),
        images=_map_result_images(item),
    )


def _as_sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value

    return ()


def _map_result_images(item: Mapping[str, Any]) -> Tuple[ImageResult, ...]:
    img_url = item.get("img_src")
    thumbnail_url = item.get("thumbnail_src") or item.get("thumbnail")
    source_url = item.get("url")

    if not img_url and not thumbnail_url:
        return ()

    return (
        ImageResult(
            url=str(img_url or thumbnail_url),
            desc=to_optional_str(item.get("title")),
            source_url=str(source_url) if source_url else None,
            thumbnail_url=str(thumbnail_url) if thumbnail_url else None,
            resolution=to_optional_str(item.get("resolution")),
        ),
    )


@dataclass(frozen=True, slots=True)
class SearXNGSearchRequest:
    query: str
    category: Optional[str] = None
    engines: Optional[List[str]] = None
    language: Optional[str] = None
    safesearch: Optional[int] = None

    def to_params(self) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "q": self.query,
            "format": "json",
        }

        if self.engines:
            params["engines"] = ",".join(self.engines)
        elif self.category:
            params["categories"] = self.category

        if self.language is not None:
            params["language"] = self.language

        if self.safesearch is not None:
            params["safesearch"] = self.safesearch

        return params


def _log_empty_mapped_results(
    *,
    query: str,
    raw_count: int,
    raw_results: Sequence[Any],
) -> None:
    first_item = next((item for item in raw_results if isinstance(item, Mapping)), None)
    first_item_keys = list(first_item.keys()) if first_item is not None else []
    first_title = str(first_item.get("title") or "") if first_item is not None else ""
    first_url = str(first_item.get("url") or "") if first_item is not None else ""
    first_content = (
        str(first_item.get("content") or first_item.get("snippet") or "")[:200]
        if first_item is not None
        else ""
    )

    log_event(
        "SearXNG 搜索",
        reason="mapped_results_empty",
        query=query,
        raw_count=raw_count,
        first_item_keys=first_item_keys,
        first_title=first_title,
        first_url=first_url,
        first_content=first_content,
    )


def map_searxng_response(
    data: Mapping[str, Any],
    *,
    query: str,
    max_results: int,
    images_only: bool = False,
) -> SearchResponse:
    raw_results = data.get("results") or ()

    if not isinstance(raw_results, Sequence) or isinstance(raw_results, str):
        raw_results = ()

    raw_count = len(raw_results)

    if images_only:
        images = tuple(
            image
            for item in raw_results
            if isinstance(item, Mapping)
            for image in _map_result_images(item)
            if image.url
        )
        images = deduplicate_images(images)

        return SearchResponse(
            query=query,
            results=(),
            images=images[:max_results],
        )

    mapped_results: List[SearchResult] = []

    for item in raw_results:
        if not isinstance(item, Mapping):
            continue

        result = _map_searxng_result(item)
        if not is_valid_result(result):
            continue

        mapped_results.append(result)

    if raw_count > 0 and not mapped_results:
        _log_empty_mapped_results(
            query=query,
            raw_count=raw_count,
            raw_results=raw_results,
        )

    results = deduplicate_results_by_domain(
        tuple(mapped_results),
        max_per_domain=2,
    )

    return SearchResponse(
        query=query,
        results=results[:max_results],
    )


def merge_search_responses(
    web_response: SearchResponse,
    image_response: SearchResponse,
) -> SearchResponse:
    return SearchResponse(
        query=web_response.query,
        results=web_response.results,
        images=image_response.images,
        source=web_response.source or image_response.source,
    )
