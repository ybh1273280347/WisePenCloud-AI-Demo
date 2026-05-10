最佳实践可以这样定：

> **公共接口只表达“搜索意图”，供应商适配层负责把意图翻译成具体 API 参数。**

也就是：

```text
Agent / 上层业务
    ↓
search(query, max_results=5, with_images=False)
    ↓
TavilySearcher
    ↓
TavilySearchRequest(query, max_results, with_images)
    ↓
to_payload() 映射成 Tavily 参数
```

Tavily 官方文档中，`include_images` 是 Tavily 自己的参数，默认 `False`；`max_results` 默认是 `5`；`topic`、`search_depth`、`include_answer`、`include_raw_content` 等都属于 Tavily 的供应商能力或响应大小控制参数，不适合直接做成多引擎公共接口。([Tavily Docs][1])

---

# 设计原则

## 1. 公共层字段越少越好

对 AI agent 暴露：

```python
search(
    query: str,
    max_results: int = 5,
    with_images: bool = False,
)
```

不要暴露：

```python
topic
search_depth
include_images
include_answer
include_raw_content
```

原因：

| 字段                    | 是否公共 | 原因             |
| --------------------- | ---: | -------------- |
| `query`               |    是 | 所有搜索引擎都有       |
| `max_results`         |    是 | 大多数搜索引擎都有类似概念  |
| `with_images`         |    是 | 通用能力表达，不绑定供应商  |
| `include_images`      |    否 | Tavily 参数名     |
| `topic`               |    否 | Tavily 分类参数    |
| `search_depth`        |    否 | Tavily 搜索深度参数  |
| `include_answer`      |    否 | 不是所有引擎都有“生成答案” |
| `include_raw_content` |    否 | 更像内容抽取/调试能力    |

---

# 2. `common.py` 保持通用响应模型

建议把图片能力设计成两层：

```python
SearchResponse.images
```

表示**本次查询整体相关图片**。

```python
SearchResult.images
```

表示**某条网页搜索结果附带的图片**。

这样既兼容 Tavily，也兼容后续 Bing、SearxNG、Google CSE 等引擎。

## `models/common.py`

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence


@dataclass(frozen=True, slots=True)
class ImageResult:
    """通用图片搜索结果"""

    url: str
    desc: Optional[str] = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    """通用网页搜索结果"""

    title: str
    url: str
    snippet: str
    images: Sequence[ImageResult] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "images", tuple(self.images))


@dataclass(frozen=True, slots=True)
class SearchResponse:
    """通用搜索响应，不绑定具体搜索供应商"""

    query: str
    results: Sequence[SearchResult] = field(default_factory=tuple)

    # 某些搜索引擎可能提供直接答案，不支持则为 None
    answer: Optional[str] = None

    # 本次查询整体相关图片
    images: Sequence[ImageResult] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", tuple(self.results))
        object.__setattr__(self, "images", tuple(self.images))


__all__ = [
    "ImageResult",
    "SearchResult",
    "SearchResponse",
]
```

这里保留 `answer` 是可以的，因为它是**可选能力**。不支持的引擎直接返回 `None`，不会破坏兼容性。

---

# 3. `tavily.py` 只做 Tavily 适配

这里的关键是：

```python
with_images
```

是你系统的通用语义。

```python
include_images
```

只在 `to_payload()` 里出现，是 Tavily 的供应商参数。

## `models/tavily.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from chat.application.web_search.models.common import (
    ImageResult,
    SearchResponse,
    SearchResult,
)


@dataclass(frozen=True, slots=True)
class TavilySearchRequest:
    """Tavily Search API 请求体。

    只接收公共搜索语义，然后在 to_payload() 中映射为 Tavily 参数。
    """

    query: str
    max_results: int = 5
    with_images: bool = False

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query 不能为空")

        if not 0 <= self.max_results <= 20:
            raise ValueError("max_results 必须在 0 到 20 之间")

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": self.query,
            "max_results": self.max_results,
        }

        if self.with_images:
            payload["include_images"] = True

        return payload


def map_tavily_response(data: Mapping[str, Any]) -> SearchResponse:
    """将 Tavily 原始响应映射为通用 SearchResponse"""

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
        images=_map_images(data.get("images")),
    )


def _map_tavily_result(item: Mapping[str, Any]) -> SearchResult:
    return SearchResult(
        title=str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        snippet=str(item.get("content") or item.get("snippet") or ""),
        images=_map_images(item.get("images")),
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

    desc = item.get("description") or item.get("desc") or item.get("alt")

    return ImageResult(
        url=str(url),
        desc=str(desc) if desc is not None else None,
    )


def _to_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None

    return str(value)


__all__ = [
    "TavilySearchRequest",
    "map_tavily_response",
]
```

---

# 4. 不建议保留 `TavilySearchResult`

虽然可以写：

```python
class TavilySearchResult(SearchResult):
    score: float | None = None
    raw_content: str | None = None
```

但从你的目标看，**不建议保留**。

因为你最终要接多个引擎，上层最好永远只看到：

```python
SearchResult
```

而不是：

```python
TavilySearchResult
BingSearchResult
SearxngSearchResult
```

否则供应商细节会泄漏到 agent 或业务层。

Tavily 的 `score`、`raw_content`、`favicon`、`published_date` 这些字段可以先忽略。以后如果确实需要，再设计一个通用的：

```python
metadata: Mapping[str, Any]
```

但现在不要加，避免提前复杂化。

---

# 5. `TavilySearcher.search()` 的最佳形态

```python
class TavilySearcher:
    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        request = TavilySearchRequest(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

        data = self._post(request.to_payload())
        return map_tavily_response(data)
```

对 agent 来说，只需要理解：

```python
search("猫咪图片", with_images=True)
```

而不是理解：

```python
include_images=True
topic="general"
search_depth="basic"
```

---

# 最终方案总结

你的最佳实践应该是：

```text
models/common.py
    ImageResult
    SearchResult
    SearchResponse

models/tavily.py
    TavilySearchRequest
    map_tavily_response

searcher/tavily_searcher.py
    TavilySearcher.search(query, max_results=5, with_images=False)
```

公共调用协议稳定为：

```python
search(
    query: str,
    max_results: int = 5,
    with_images: bool = False,
) -> SearchResponse
```

这个方案的好处是：

1. **多引擎兼容**：不暴露 Tavily 专属参数。
2. **支持图片搜索**：用 `with_images` 表达通用能力。
3. **响应结构统一**：所有引擎都映射成 `SearchResponse`。
4. **供应商隔离清楚**：`include_images` 只存在于 Tavily adapter 内部。
5. **不会过度设计**：没有泛型、协议、复杂继承，也没有提前设计一堆暂时用不到的字段。

[1]: https://docs.tavily.com/documentation/api-reference/endpoint/search?utm_source=chatgpt.com "Tavily Search"
