对，这里应该再收敛一版：**只保留必要字段，其余让 Tavily 用默认值**。

官方文档里 `query` 是必填；`search_depth` 默认是 `basic`；`max_results` 默认是 `5`，范围是 `0 <= x <= 20`；`topic` 默认是 `general`。`include_answer`、`include_raw_content` 默认都是 `false`；`include_images` 默认也是 `false`。([Tavily Docs][1])

所以你当前阶段的 `tavily.py` 不需要覆盖那么多参数。建议这样：

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Optional, Sequence

from chat.application.web_search.models.common import (
    ImageResult,
    SearchResponse,
    SearchResult,
)


SearchDepth = Literal["basic", "advanced", "fast", "ultra-fast"]
SearchTopic = Literal["general", "news", "finance"]
AnswerMode = bool | Literal["basic", "advanced"]
RawContentMode = bool | Literal["markdown", "text"]


@dataclass(frozen=True, slots=True)
class TavilySearchRequest:
    """Tavily Search API 请求体，只保留常用必要参数"""

    query: str

    # 常用控制项
    search_depth: SearchDepth = "basic"
    max_results: int = 5
    topic: SearchTopic = "general"

    # 响应体大小相关，显式控制
    include_answer: AnswerMode = False
    include_images: bool = False
    include_raw_content: RawContentMode = False

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query 不能为空")

        if not 0 <= self.max_results <= 20:
            raise ValueError("max_results 必须在 0 到 20 之间")

    def to_payload(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "search_depth": self.search_depth,
            "max_results": self.max_results,
            "topic": self.topic,
            "include_answer": self.include_answer,
            "include_images": self.include_images,
            "include_raw_content": self.include_raw_content,
        }


@dataclass(frozen=True, slots=True)
class TavilySearchResult(SearchResult):
    """Tavily 单条搜索结果"""

    score: Optional[float] = None
    raw_content: Optional[str] = None
```

映射函数保持健壮，但只映射必要字段：

```python
def map_tavily_response(data: Mapping[str, Any]) -> SearchResponse:
    raw_results = data.get("results") or ()

    if not isinstance(raw_results, Sequence) or isinstance(raw_results, str):
        raw_results = ()

    results = tuple(
        _map_tavily_result(item)
        for item in raw_results
        if isinstance(item, Mapping)
    )

    return SearchResponse(
        query=str(data.get("query") or ""),
        results=results,
        answer=_to_optional_str(data.get("answer")),
    )


def _map_tavily_result(item: Mapping[str, Any]) -> TavilySearchResult:
    return TavilySearchResult(
        title=str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        snippet=str(item.get("content") or ""),
        images=_map_images(item.get("images")),
        score=_to_optional_float(item.get("score")),
        raw_content=_to_optional_str(item.get("raw_content")),
    )


def _map_images(items: Any) -> tuple[ImageResult, ...]:
    if not isinstance(items, Sequence) or isinstance(items, str):
        return ()

    images: list[ImageResult] = []

    for item in items:
        image = _map_image(item)
        if image is not None:
            images.append(image)

    return tuple(images)


def _map_image(item: Any) -> Optional[ImageResult]:
    if isinstance(item, str):
        return ImageResult(url=item)

    if not isinstance(item, Mapping):
        return None

    url = item.get("url")
    if not url:
        return None

    desc = item.get("description")

    return ImageResult(
        url=str(url),
        desc=str(desc) if desc is not None else None,
    )


def _to_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None

    return str(value)


def _to_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None
```

最后加导出：

```python
__all__ = [
    "TavilySearchRequest",
    "TavilySearchResult",
    "map_tavily_response",
]
```

## 完整版 `tavily.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Optional, Sequence

from chat.application.web_search.models.common import (
    ImageResult,
    SearchResponse,
    SearchResult,
)


SearchDepth = Literal["basic", "advanced", "fast", "ultra-fast"]
SearchTopic = Literal["general", "news", "finance"]
AnswerMode = bool | Literal["basic", "advanced"]
RawContentMode = bool | Literal["markdown", "text"]


@dataclass(frozen=True, slots=True)
class TavilySearchRequest:
    """Tavily Search API 请求体，只保留常用必要参数"""

    query: str

    search_depth: SearchDepth = "basic"
    max_results: int = 5
    topic: SearchTopic = "general"

    include_answer: AnswerMode = False
    include_images: bool = False
    include_raw_content: RawContentMode = False

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query 不能为空")

        if not 0 <= self.max_results <= 20:
            raise ValueError("max_results 必须在 0 到 20 之间")

    def to_payload(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "search_depth": self.search_depth,
            "max_results": self.max_results,
            "topic": self.topic,
            "include_answer": self.include_answer,
            "include_images": self.include_images,
            "include_raw_content": self.include_raw_content,
        }


@dataclass(frozen=True, slots=True)
class TavilySearchResult(SearchResult):
    """Tavily 单条搜索结果"""

    score: Optional[float] = None
    raw_content: Optional[str] = None


def map_tavily_response(data: Mapping[str, Any]) -> SearchResponse:
    """将 Tavily Search API 响应映射为通用 SearchResponse"""

    raw_results = data.get("results") or ()

    if not isinstance(raw_results, Sequence) or isinstance(raw_results, str):
        raw_results = ()

    results = tuple(
        _map_tavily_result(item)
        for item in raw_results
        if isinstance(item, Mapping)
    )

    return SearchResponse(
        query=str(data.get("query") or ""),
        results=results,
        answer=_to_optional_str(data.get("answer")),
    )


def _map_tavily_result(item: Mapping[str, Any]) -> TavilySearchResult:
    return TavilySearchResult(
        title=str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        snippet=str(item.get("content") or ""),
        images=_map_images(item.get("images")),
        score=_to_optional_float(item.get("score")),
        raw_content=_to_optional_str(item.get("raw_content")),
    )


def _map_images(items: Any) -> tuple[ImageResult, ...]:
    if not isinstance(items, Sequence) or isinstance(items, str):
        return ()

    images: list[ImageResult] = []

    for item in items:
        image = _map_image(item)
        if image is not None:
            images.append(image)

    return tuple(images)


def _map_image(item: Any) -> Optional[ImageResult]:
    if isinstance(item, str):
        return ImageResult(url=item)

    if not isinstance(item, Mapping):
        return None

    url = item.get("url")
    if not url:
        return None

    desc = item.get("description")

    return ImageResult(
        url=str(url),
        desc=str(desc) if desc is not None else None,
    )


def _to_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None

    return str(value)


def _to_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "TavilySearchRequest",
    "TavilySearchResult",
    "map_tavily_response",
]
```

## 这版的取舍

保留：

```python
query
search_depth
max_results
topic
include_answer
include_images
include_raw_content
```

暂时不保留：

```python
chunks_per_source
include_image_descriptions
include_favicon
include_domains
exclude_domains
time_range
start_date
end_date
country
auto_parameters
exact_match
include_usage
safe_search
```

这些都属于**进阶筛选、调试、成本追踪或企业级控制项**。当前只做一个干净的 `searcher.search()`，确实不应该把它们全部暴露出来。

[1]: https://docs.tavily.com/documentation/api-reference/endpoint/search "Tavily Search - Tavily Docs"
