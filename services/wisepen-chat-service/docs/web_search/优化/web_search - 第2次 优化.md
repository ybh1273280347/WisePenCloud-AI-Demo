有，**你这个场景首选 `cachetools.TTLCache`**，比自己维护：

```python
Dict[str, Tuple[SearchResponse, float]]
```

更稳。

`cachetools.TTLCache` 是一个带 TTL 的 LRU cache：每个 item 有过期时间；过期后不可访问；如果没有过期项可清理，又超过 `maxsize`，会按 LRU 淘汰。它默认用 `time.monotonic()` 作为计时器，比 `time.time()` 更适合做 TTL，因为不受系统时间回拨影响。([cachetools.readthedocs.io][1])

不过要注意：`cachetools` 的 cache 类**不是线程安全的**，共享 cache 需要自行同步。你的 `execute()` 是 async 场景，不是多线程为主，但多个 coroutine 同时访问同一个 cache 仍然建议加 `asyncio.Lock`。官方文档也明确提醒这些 cache 类不是 thread-safe。([cachetools.readthedocs.io][1])

---

## 推荐改法：`cachetools.TTLCache + asyncio.Lock`

先安装：

```bash
pip install cachetools
```

然后把你原来的：

```python
self._cache: Dict[str, Tuple[SearchResponse, float]] = {}
```

替换成：

```python
from cachetools import TTLCache
```

完整改法如下。

```python
from __future__ import annotations

import asyncio
from typing import Any, Optional

from cachetools import TTLCache

from chat.application.web_search import SearchResponse
from chat.application.web_search.searcher.base import WebSearcher
from chat.core.config.app_settings import settings
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_error


class WebSearchTool(BaseTool):
    """联网搜索工具"""

    def __init__(
        self,
        searcher: WebSearcher,
        *,
        ttl: int = 3600,
        cache_maxsize: int = 512,
        max_results_limit: int = 10,
    ) -> None:
        self._ttl = ttl
        self._max_results_limit = max_results_limit

        self._cache: TTLCache[tuple[str, str, str, int, bool], SearchResponse] = TTLCache(
            maxsize=cache_maxsize,
            ttl=ttl,
        )
        self._cache_lock = asyncio.Lock()

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

        cached = await self._get_cached(cache_key)
        if cached is not None:
            return _format_response(cached)

        try:
            response = await self._searcher.search(
                query=query,
                max_results=max_results,
                with_images=with_images,
            )

            await self._set_cached(cache_key, response)

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

    async def _get_cached(
        self,
        cache_key: tuple[str, str, str, int, bool],
    ) -> Optional[SearchResponse]:
        async with self._cache_lock:
            return self._cache.get(cache_key)

    async def _set_cached(
        self,
        cache_key: tuple[str, str, str, int, bool],
        response: SearchResponse,
    ) -> None:
        async with self._cache_lock:
            self._cache[cache_key] = response
```

---

## 辅助函数保持这样

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

---

## 为什么我更建议 `cachetools`，而不是 `aiocache`

`aiocache` 也可以，它是 async cache manager，支持 memory、Redis、Memcached 等后端，并且有统一的 `get/set/add/delete` 接口。它适合你后面要上 Redis、跨进程共享缓存、或者多服务部署的场景。([GitHub][2])

但你现在的需求是：

```text
单进程内存缓存
TTL
限制最大缓存数量
代码尽量轻
```

所以 `cachetools.TTLCache` 更合适。

---

## 当前最佳选择

| 场景                          | 推荐                    |
| --------------------------- | --------------------- |
| 当前本地进程内缓存                   | `cachetools.TTLCache` |
| FastAPI 多 worker 共享缓存       | Redis                 |
| 想要 async cache 抽象并可能接 Redis | `aiocache`            |
| 只做简单原型                      | 自己写 dict TTL          |

你的项目现在建议用：

```python
TTLCache(maxsize=512, ttl=3600)
```

并保留：

```python
asyncio.Lock()
```

这样代码轻，行为清楚，也比手写 TTL 更健壮。

[1]: https://cachetools.readthedocs.io/ "cachetools — Extensible memoizing collections and decorators — cachetools 7.1.1 documentation"
[2]: https://github.com/aio-libs/aiocache?utm_source=chatgpt.com "aio-libs/aiocache: Asyncio cache manager ..."
