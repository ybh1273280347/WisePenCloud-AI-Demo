from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from chat.application.web_search.models.common import (
    ImageResult,
    SearchResponse,
    SearchResult,
)
from chat.application.web_search.internal.models.helpers import is_valid_result
from chat.application.web_search.internal.utils.domains import (
    deduplicate_results_by_domain,
)
from chat.application.web_search.internal.utils.images import deduplicate_images


def _map_tavily_result(item: Mapping[str, Any]) -> SearchResult:
    return SearchResult(
        title=str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        snippet=str(item.get("content") or item.get("snippet") or ""),
        images=_map_images(item.get("images")),
    )


def _map_images(items: Any) -> Tuple[ImageResult, ...]:
    if not isinstance(items, Sequence) or isinstance(items, str):
        return ()

    images: List[ImageResult] = []

    for item in items:
        image = _map_image(item)
        if image is not None:
            images.append(image)

    return deduplicate_images(images)


def _map_image(item: Any) -> Optional[ImageResult]:
    if isinstance(item, str):
        return ImageResult(url=item)

    if not isinstance(item, Mapping):
        return None

    url = item.get("url")
    if not url:
        return None

    desc = item.get("description") or item.get("desc") or item.get("alt")
    source_url = item.get("source_url") or item.get("source") or item.get("page_url")
    thumbnail_url = (
        item.get("thumbnail_url")
        or item.get("thumbnail")
        or item.get("thumbnail_src")
    )

    return ImageResult(
        url=str(url),
        desc=str(desc) if desc is not None else None,
        source_url=str(source_url) if source_url else None,
        thumbnail_url=str(thumbnail_url) if thumbnail_url else None,
    )


@dataclass(frozen=True, slots=True)
class TavilySearchRequest:
    """Tavily Search API 请求体。

    只接收公共搜索语义，然后在 to_payload() 中映射为 Tavily 参数。
    """

    query: str
    max_results: int = 5
    with_images: bool = False

    def to_payload(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "max_results": self.max_results,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_usage": True,
            "search_depth": "basic",
        }


def map_tavily_response(
    data: Mapping[str, Any],
    *,
    max_results: int = 5,
) -> SearchResponse:
    """将 Tavily 原始响应映射为通用 SearchResponse"""

    raw_results = data.get("results") or ()

    if not isinstance(raw_results, Sequence) or isinstance(raw_results, str):
        raw_results = ()

    results = tuple(
        result
        for item in raw_results
        if isinstance(item, Mapping)
        for result in (_map_tavily_result(item),)
        if is_valid_result(result)
    )
    results = deduplicate_results_by_domain(results, max_per_domain=2)

    return SearchResponse(
        query=str(data.get("query") or ""),
        results=results,
        images=(),
    )
