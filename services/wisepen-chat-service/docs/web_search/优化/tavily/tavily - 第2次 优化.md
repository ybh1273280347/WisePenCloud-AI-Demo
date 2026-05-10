对，这个 `TavilySearcher` 的方向已经很接近了。结合 Tavily 官方 Python SDK 文档，主要优化点是：

1. **不要用 `asyncio.to_thread(TavilyClient.search)` 作为首选**
2. **直接使用官方 `AsyncTavilyClient`**
3. **`search()` 对外仍然只暴露通用字段：`query / max_results / with_images`**
4. **Tavily 的 `include_images` 只在 `TavilySearchRequest.to_payload()` 里出现**
5. **增加错误包装，避免 Tavily SDK 异常泄漏到上层**

Tavily 官方 Python SDK 明确提供了同步客户端 `TavilyClient` 和异步客户端 `AsyncTavilyClient`；既然你的 `search()` 已经是 async，就应该优先使用 `AsyncTavilyClient`，而不是把同步方法丢进线程池。官方文档也说明 `search` 是 SDK 暴露的搜索方法，`query` 是必填，`max_results` 默认 5 且范围是 0 到 20，`include_images` 默认是 `False`。([Tavily Docs][1])

---

## 推荐版 `tavily_searcher.py`

```python id="5i872t"
from __future__ import annotations

from typing import Any, Optional

from tavily import AsyncTavilyClient

from chat.application.web_search.models import (
    SearchResponse,
    TavilySearchRequest,
    map_tavily_response,
)


class TavilySearchError(RuntimeError):
    """Tavily 搜索调用失败"""


class TavilySearcher:
    """Tavily 搜索器。

    对外只暴露通用搜索语义：
    - query
    - max_results
    - with_images

    Tavily 专有参数由 TavilySearchRequest.to_payload() 负责映射。
    """

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 60.0,
        project_id: Optional[str] = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key 不能为空")

        self._client = AsyncTavilyClient(
            api_key=api_key,
            project_id=project_id,
        )
        self._timeout = timeout

    async def search(
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

        payload = request.to_payload()

        # Tavily SDK 的 search 参数中支持 timeout，默认是 60 秒。
        payload["timeout"] = self._timeout

        try:
            raw_response = await self._client.search(**payload)
        except Exception as exc:
            raise TavilySearchError("Tavily search request failed") from exc

        return map_tavily_response(raw_response)


__all__ = [
    "TavilySearcher",
    "TavilySearchError",
]
```

---

## 为什么这样改

### 1. 用 `AsyncTavilyClient` 替代 `asyncio.to_thread`

你现在的写法：

```python id="8rz5vl"
raw_response = await asyncio.to_thread(self._client.search, **payload)
```

不是错的，但它是“同步 SDK 包一层线程池”。

既然官方已经提供：

```python id="zrkae2"
from tavily import AsyncTavilyClient

client = AsyncTavilyClient("tvly-YOUR_API_KEY")
```

那 async 项目里应该直接使用它。官方文档说明 Python SDK 同时提供同步和异步客户端。([Tavily Docs][1])

---

### 2. `timeout` 放在 searcher 内部配置，不暴露给 agent

Tavily 官方 `search` 参数里有 `timeout`，默认是 60 秒。([Tavily Docs][1])

但是 `timeout` 不应该成为 agent 调用工具时的核心字段。它属于基础设施配置，所以放在构造函数里：

```python id="azjlg7"
TavilySearcher(api_key=api_key, timeout=30.0)
```

而不是：

```python id="ugtxda"
search(query, timeout=30.0)
```

这样更适合多引擎统一接口。

---

### 3. `project_id` 可以放构造函数

Tavily 官方支持在 client 初始化时传 `project_id`，用于按项目追踪 API 使用情况。([Tavily Docs][1])

这也属于供应商级配置，不应该暴露给 agent，所以放在：

```python id="bff73f"
__init__(..., project_id: Optional[str] = None)
```

是合理的。

---

### 4. `with_images` 保持通用语义

你的 `search()` 保留：

```python id="caxli1"
with_images: bool = False
```

这是对的。

Tavily 官方参数叫：

```python id="ihwmes"
include_images
```

并且开启后会返回顶层 `images`，同时每个 `results[]` 里也可能有自己的 `images`。([Tavily Docs][1])

但这个供应商字段应该只在 `TavilySearchRequest.to_payload()` 里出现：

```python id="q2sehi"
if self.with_images:
    payload["include_images"] = True
```

上层只知道：

```python id="qz2b12"
with_images=True
```

不需要知道 Tavily 的字段名。

---

## 你的 `models/tavily.py` 应该对应这样

确认一下，`TavilySearchRequest.to_payload()` 最好保持这个形态：

```python id="ev0w17"
@dataclass(frozen=True, slots=True)
class TavilySearchRequest:
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
```

这个是符合当前设计的：公共语义是 `with_images`，Tavily 参数是 `include_images`。官方 `max_results` 范围是 `0 <= x <= 20`。([Tavily Docs][2])

---

## 关于 `include_image_descriptions`

暂时不建议在 searcher 层暴露。

虽然 Tavily 官方支持：

```python id="m88y5m"
include_image_descriptions
```

并且当它为 `True` 时，图片会返回 `{url, description}` 结构。([Tavily Docs][1])

但从多引擎兼容角度看，公共层只要：

```python id="obn343"
with_images
```

就够了。

你的 `ImageResult.desc` 可以兼容 Tavily 返回的 `description`，但不必主动要求 Tavily 一定返回描述。后面如果真的需要，可以在 `TavilySearcher.__init__` 中加一个供应商内部配置：

```python id="ued9rf"
include_image_descriptions: bool = False
```

而不是让 agent 每次调用时传。

---

## 最终结论

你的 `tavily_searcher.py` 最佳实践版就是：

```python id="jwgxfi"
from __future__ import annotations

from typing import Optional

from tavily import AsyncTavilyClient

from chat.application.web_search.models import (
    SearchResponse,
    TavilySearchRequest,
    map_tavily_response,
)


class TavilySearchError(RuntimeError):
    pass


class TavilySearcher:
    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 60.0,
        project_id: Optional[str] = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key 不能为空")

        self._client = AsyncTavilyClient(
            api_key=api_key,
            project_id=project_id,
        )
        self._timeout = timeout

    async def search(
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

        payload = request.to_payload()
        payload["timeout"] = self._timeout

        try:
            raw_response = await self._client.search(**payload)
        except Exception as exc:
            raise TavilySearchError("Tavily search request failed") from exc

        return map_tavily_response(raw_response)


__all__ = [
    "TavilySearcher",
    "TavilySearchError",
]
```

这版的边界比较稳：

```text id="1a9ewp"
Agent / 上层业务
    只知道 query, max_results, with_images

TavilySearcher
    负责异步调用 Tavily SDK

TavilySearchRequest
    负责把 with_images 映射为 include_images

map_tavily_response
    负责把 Tavily 响应映射成 SearchResponse
```

这样后面接入其他引擎时，`searcher.search()` 的公共形状不用变。

[1]: https://docs.tavily.com/sdk/python/reference "SDK Reference - Tavily Docs"
[2]: https://docs.tavily.com/documentation/api-reference/endpoint/search "Tavily Search - Tavily Docs"
