这个类要做到多引擎兼容，关键是把它从：

```python
WebSearchTool -> TavilySearcher
```

改成：

```python
WebSearchTool -> WebSearcher 接口 -> TavilySearcher / BingSearcher / SearxngSearcher ...
```

也就是说：**Tool 层不应该知道 Tavily 的存在**。

---

# 主要问题

你现在这版有几个耦合点：

```python
from chat.application.web_search import TavilySearcher
```

以及：

```python
self._searcher = TavilySearcher(api_key=settings.TAVILY_API_KEY)
```

这会导致 `WebSearchTool` 只能使用 Tavily。

另外这个 schema 字段：

```python
"include_images"
```

也偏供应商风格。虽然不是 Tavily 独有，但更推荐统一为：

```python
"with_images"
```

因为它表达的是**能力请求**，不是某个 API 参数。

---

# 推荐改法

## 1. 先定义统一 Searcher 接口

建议新增：

```text
chat/application/web_search/searcher/base.py
```

```python
from __future__ import annotations

from typing import Protocol

from chat.application.web_search.models import SearchResponse


class WebSearcher(Protocol):
    """通用搜索引擎接口"""

    @property
    def engine_name(self) -> str:
        ...

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        ...
```

然后 `TavilySearcher` 实现这个接口：

```python
class TavilySearcher:
    @property
    def engine_name(self) -> str:
        return "tavily"

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        ...
```

后续接入其他引擎时，也实现同样接口：

```python
class BingSearcher:
    @property
    def engine_name(self) -> str:
        return "bing"

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        ...
```

---

# 2. `WebSearchTool` 不再直接创建 TavilySearcher

推荐把 searcher 通过构造函数注入。

## 优化版 `WebSearchTool`

```python
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from chat.application.web_search.models import SearchResponse
from chat.application.web_search.searcher.base import WebSearcher
from chat.core.config.app_settings import settings
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_error


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    response: SearchResponse
    expired_at: float


class WebSearchTool(BaseTool):
    """联网搜索工具。

    只依赖通用 WebSearcher 接口，不依赖 Tavily / Bing / SearxNG 等具体引擎。
    """

    def __init__(
        self,
        searcher: WebSearcher,
        *,
        ttl: int = 3600,
        max_results_limit: int = 10,
    ) -> None:
        self._ttl = ttl
        self._max_results_limit = max_results_limit
        self._cache: dict[tuple[str, str, str, int, bool], _CacheEntry] = {}
        self._searcher = searcher

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> str:
        session_id = _get_session_id(context)
        if session_id is None:
            return "[Tool Error] Missing session_id in execution context."

        query = _get_query(kwargs)
        if query is None:
            return "[Tool Error] Missing or invalid query."

        max_results = _normalize_max_results(
            kwargs.get("max_results"),
            default=5,
            upper=self._max_results_limit,
        )

        # 新字段使用 with_images；兼容旧字段 include_images
        with_images = _normalize_bool(
            kwargs.get("with_images", kwargs.get("include_images", False))
        )

        cache_key = (
            self._searcher.engine_name,
            session_id,
            query,
            max_results,
            with_images,
        )

        cached = self._get_cached(cache_key)
        if cached is not None:
            return _format_response(cached)

        try:
            response = await self._searcher.search(
                query=query,
                max_results=max_results,
                with_images=with_images,
            )

            self._set_cached(cache_key, response)

            if not _has_search_content(response):
                return "[Tool Result] No results found for the query."

            return _format_response(response)

        except Exception as exc:
            log_error(
                "联网搜索",
                exc,
                session_id=session_id,
                engine=self._searcher.engine_name,
                query=query,
                max_results=max_results,
                with_images=with_images,
            )
            return "[Tool Error] An error occurred while searching the web."

    def _get_cached(
        self,
        cache_key: tuple[str, str, str, int, bool],
    ) -> Optional[SearchResponse]:
        entry = self._cache.get(cache_key)
        if entry is None:
            return None

        if time.monotonic() >= entry.expired_at:
            self._cache.pop(cache_key, None)
            return None

        return entry.response

    def _set_cached(
        self,
        cache_key: tuple[str, str, str, int, bool],
        response: SearchResponse,
    ) -> None:
        self._cache[cache_key] = _CacheEntry(
            response=response,
            expired_at=time.monotonic() + self._ttl,
        )
```

---

# 3. 参数解析函数

这些建议独立出来，避免 `execute()` 变得太长。

```python
def _get_session_id(context: dict[str, Any]) -> Optional[str]:
    session_id = context.get("session_id")

    if not isinstance(session_id, str):
        return None

    session_id = session_id.strip()
    return session_id or None


def _get_query(kwargs: dict[str, Any]) -> Optional[str]:
    query = kwargs.get("query")

    if not isinstance(query, str):
        return None

    query = query.strip()
    return query or None


def _normalize_max_results(
    value: Any,
    *,
    default: int,
    upper: int,
) -> int:
    try:
        max_results = int(value)
    except (TypeError, ValueError):
        max_results = default

    return max(1, min(max_results, upper))


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}

    return bool(value)


def _has_search_content(response: SearchResponse) -> bool:
    return bool(response.answer or response.results or response.images)
```

这里修掉了你原来的一个小问题：

```python
max_results: int = min(kwargs.get("max_results", 5), 10)
```

如果 agent 传：

```python
max_results=-100
```

原代码会接受 `-100`。优化后会限制在：

```python
1 <= max_results <= max_results_limit
```

---

# 4. 格式化响应优化

你现在图片只输出：

```python
Images: 3 available
```

这对 agent 不够有用。既然支持图片，至少应该给出少量 URL。

```python
def _format_response(response: SearchResponse) -> str:
    lines = [f"[Tool Result] Web search results for: {response.query}"]

    if response.answer:
        lines.append(f"\nAnswer:\n{response.answer}")

    if response.results:
        lines.append("\nResults:")

    for index, result in enumerate(response.results, 1):
        title = result.title.strip() or result.url or "(no title)"
        snippet = result.snippet.strip()

        lines.append(f"\n{index}. {title}")
        lines.append(f"   URL: {result.url}")

        if snippet:
            lines.append(f"   Snippet: {snippet}")

        if result.images:
            lines.append("   Images:")
            for image in result.images[:2]:
                lines.append(_format_image_line(image, indent="      "))

    if response.images:
        lines.append("\nQuery-level images:")
        for image in response.images[:5]:
            lines.append(_format_image_line(image, indent="   "))

    raw = "\n".join(lines)

    if len(raw) > settings.TOOL_RESULT_MAX_CHARS:
        raw = raw[: settings.TOOL_RESULT_MAX_CHARS] + "\n...[truncated]"

    return raw


def _format_image_line(image: Any, *, indent: str) -> str:
    desc = getattr(image, "desc", None)
    url = getattr(image, "url", "")

    if desc:
        return f"{indent}- {url} ({desc})"

    return f"{indent}- {url}"
```

如果你确定传入的一定是 `ImageResult`，可以把 `Any` 改成：

```python
from chat.application.web_search.models import ImageResult

def _format_image_line(image: ImageResult, *, indent: str) -> str:
    ...
```

---

# 5. Tool schema 改成供应商无关

建议把：

```python
include_images
```

改成：

```python
with_images
```

## 推荐 `_TOOL_DESCRIPTION`

```python
_TOOL_DESCRIPTION = (
    "Searches the web using the configured search engine. "
    "Use this tool when current information, external facts, source lookup, or web evidence is required. "
    "Returns relevant web results with title, URL, and snippet. "
    "When requested, it can also return image results."
)
```

注意这里不要写：

```python
using the Tavily API
```

因为这会把工具语义绑定到 Tavily。

---

## 推荐 `_TOOL_SCHEMA`

```python
_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The search query string.",
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum number of web results to return. Default is 5.",
            "default": 5,
            "minimum": 1,
            "maximum": 10,
        },
        "with_images": {
            "type": "boolean",
            "description": (
                "Whether to include relevant image results. "
                "Use this when the user asks for pictures, photos, visual references, locations, people, products, animals, or other visual information."
            ),
            "default": False,
        },
    },
    "required": ["query"],
}
```

你可以在代码里继续兼容旧字段：

```python
kwargs.get("with_images", kwargs.get("include_images", False))
```

但 schema 对 agent 只暴露新字段。

---

# 6. 创建具体搜索引擎交给工厂或依赖注入

你可以加一个工厂：

```text
chat/application/web_search/searcher/factory.py
```

```python
from __future__ import annotations

from chat.application.web_search.searcher.base import WebSearcher
from chat.application.web_search.searcher.tavily_searcher import TavilySearcher
from chat.core.config.app_settings import settings


def create_web_searcher() -> WebSearcher:
    engine = settings.WEB_SEARCH_ENGINE.lower()

    if engine == "tavily":
        return TavilySearcher(api_key=settings.TAVILY_API_KEY)

    raise ValueError(f"Unsupported web search engine: {settings.WEB_SEARCH_ENGINE}")
```

然后工具注册时：

```python
from chat.application.web_search.searcher.factory import create_web_searcher

web_search_tool = WebSearchTool(
    searcher=create_web_searcher(),
    ttl=3600,
)
```

这样后面接入 Bing 时，只改工厂：

```python
if engine == "bing":
    return BingSearcher(api_key=settings.BING_SEARCH_API_KEY)
```

`WebSearchTool` 不用动。

---

# 最终推荐结构

```text
chat/application/web_search/
├── __init__.py
├── models/
│   ├── __init__.py
│   ├── common.py
│   └── tavily.py
└── searcher/
    ├── __init__.py
    ├── base.py
    ├── factory.py
    └── tavily_searcher.py
```

工具层：

```text
chat/application/tools/
└── web_search_tool.py
```

依赖方向应该是：

```text
WebSearchTool
    ↓
WebSearcher Protocol
    ↓
TavilySearcher / BingSearcher / SearxngSearcher
```

而不是：

```text
WebSearchTool
    ↓
TavilySearcher
```

---

# 这个文件的最终核心改动

你当前代码里最应该改的就是这三处：

## 第一处：构造函数注入 searcher

从：

```python
self._searcher = TavilySearcher(api_key=settings.TAVILY_API_KEY)
```

改成：

```python
def __init__(self, searcher: WebSearcher, *, ttl: int = 3600):
    self._searcher = searcher
```

---

## 第二处：schema 用 `with_images`

从：

```python
"include_images": {
    "type": "boolean",
}
```

改成：

```python
"with_images": {
    "type": "boolean",
}
```

代码里兼容旧字段：

```python
with_images = _normalize_bool(
    kwargs.get("with_images", kwargs.get("include_images", False))
)
```

---

## 第三处：cache key 加 engine name

从：

```python
cache_key = f"{session_id}:search:q={query}:n={max_results}:img={with_images}"
```

改成：

```python
cache_key = (
    self._searcher.engine_name,
    session_id,
    query,
    max_results,
    with_images,
)
```

否则将来切换搜索引擎时，可能出现不同引擎共享同一份缓存的问题。

---

这样改完以后，`WebSearchTool` 就真正变成了**多引擎兼容的统一搜索工具**，而不是 Tavily 的包装器。
