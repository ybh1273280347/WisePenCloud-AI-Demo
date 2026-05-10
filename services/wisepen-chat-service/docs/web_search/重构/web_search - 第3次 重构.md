对，这个拆法更干净。建议把**执行器**和**基础设施组件**分开：

```text
web_search/
├── __init__.py
├── coordinator.py          # 顶层：搜索调度器
├── factory.py              # 顶层：装配入口
├── errors.py               # 顶层：公共异常
├── models/
│   ├── __init__.py
│   ├── common.py
│   ├── tavily.py
│   └── searxng.py
├── searcher/               # 只放真实搜索引擎适配器
│   ├── __init__.py
│   ├── base.py
│   ├── tavily_searcher.py
│   ├── searxng_searcher.py
│   └── duckduckgo_searcher.py
└── infrastructure/         # 基础设施能力
    ├── __init__.py
    ├── cache.py            # Fresh/Stale TTL cache
    └── circuit_breaker.py  # 熔断器
```

这样职责边界更好：

```text
searcher/
    只负责“怎么调用某个搜索引擎”

infrastructure/
    负责缓存、熔断、限流、重试等横切能力

coordinator.py
    负责搜索链路编排

factory.py
    负责把所有组件组装起来
```

---

## 最终推荐结构

```text
chat/application/web_search/
├── __init__.py
├── coordinator.py
├── errors.py
├── factory.py
├── models/
│   ├── __init__.py
│   ├── common.py
│   ├── tavily.py
│   └── searxng.py
├── searcher/
│   ├── __init__.py
│   ├── base.py
│   ├── tavily_searcher.py
│   ├── searxng_searcher.py
│   └── duckduckgo_searcher.py
└── infrastructure/
    ├── __init__.py
    ├── cache.py
    └── circuit_breaker.py
```

---

# 1. `searcher/` 保持干净

## `searcher/base.py`

```python
from __future__ import annotations

from typing import Protocol

from chat.application.web_search.models import SearchResponse


class WebSearcher(Protocol):
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


__all__ = [
    "WebSearcher",
]
```

`searcher/` 下面只保留：

```text
TavilySearcher
SearXNGSearcher
DuckDuckGoBufferSearcher
```

不要再放：

```text
cache
circuit_breaker
factory
errors
coordinator
```

这些都不是搜索引擎适配器。

---

# 2. `infrastructure/cache.py`

```python
from __future__ import annotations

import asyncio
from typing import Optional

from cachetools import TTLCache

from chat.application.web_search.models import SearchResponse


SearchCacheKey = tuple[str, int, bool]


class SearchCache:
    """搜索缓存：fresh cache + stale cache"""

    def __init__(
        self,
        *,
        fresh_ttl: int = 3600,
        stale_ttl: int = 86400,
        maxsize: int = 1024,
    ) -> None:
        self._fresh_cache: TTLCache[SearchCacheKey, SearchResponse] = TTLCache(
            maxsize=maxsize,
            ttl=fresh_ttl,
        )
        self._stale_cache: TTLCache[SearchCacheKey, SearchResponse] = TTLCache(
            maxsize=maxsize,
            ttl=stale_ttl,
        )
        self._lock = asyncio.Lock()

    async def get_fresh(
        self,
        key: SearchCacheKey,
    ) -> Optional[SearchResponse]:
        async with self._lock:
            return self._fresh_cache.get(key)

    async def get_stale(
        self,
        key: SearchCacheKey,
    ) -> Optional[SearchResponse]:
        async with self._lock:
            return self._stale_cache.get(key)

    async def set(
        self,
        key: SearchCacheKey,
        response: SearchResponse,
    ) -> None:
        async with self._lock:
            self._fresh_cache[key] = response
            self._stale_cache[key] = response


def make_search_cache_key(
    *,
    query: str,
    max_results: int,
    with_images: bool,
) -> SearchCacheKey:
    normalized_query = " ".join(query.strip().lower().split())
    return normalized_query, max_results, with_images


__all__ = [
    "SearchCache",
    "SearchCacheKey",
    "make_search_cache_key",
]
```

---

# 3. `infrastructure/circuit_breaker.py`

```python
from __future__ import annotations

import time

from chat.application.web_search.errors import WebSearchUnavailable
from chat.application.web_search.models import SearchResponse
from chat.application.web_search.searcher.base import WebSearcher


class CircuitBreakerWebSearcher:
    """搜索器熔断包装器"""

    def __init__(
        self,
        searcher: WebSearcher,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: int = 60,
    ) -> None:
        self._searcher = searcher
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds

        self._failure_count = 0
        self._opened_until = 0.0

    @property
    def engine_name(self) -> str:
        return self._searcher.engine_name

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        now = time.monotonic()

        if now < self._opened_until:
            raise WebSearchUnavailable(f"{self.engine_name} circuit is open")

        try:
            response = await self._searcher.search(
                query=query,
                max_results=max_results,
                with_images=with_images,
            )
        except Exception:
            self._failure_count += 1

            if self._failure_count >= self._failure_threshold:
                self._opened_until = now + self._cooldown_seconds

            raise

        self._failure_count = 0
        self._opened_until = 0.0
        return response


__all__ = [
    "CircuitBreakerWebSearcher",
]
```

---

# 4. `infrastructure/__init__.py`

```python
from chat.application.web_search.infrastructure.cache import (
    SearchCache,
    SearchCacheKey,
    make_search_cache_key,
)
from chat.application.web_search.infrastructure.circuit_breaker import (
    CircuitBreakerWebSearcher,
)

__all__ = [
    "SearchCache",
    "SearchCacheKey",
    "make_search_cache_key",
    "CircuitBreakerWebSearcher",
]
```

---

# 5. `coordinator.py` 放顶层

`coordinator.py` 顶层是合理的，因为它是整个 `web_search` 子系统的核心编排器，不属于某个具体搜索引擎，也不只是基础设施。

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from chat.application.web_search.errors import WebSearchError, WebSearchUnavailable
from chat.application.web_search.infrastructure import (
    SearchCache,
    make_search_cache_key,
)
from chat.application.web_search.models import SearchResponse
from chat.application.web_search.searcher.base import WebSearcher
from common.logger import log_fail, log_ok


@dataclass(frozen=True, slots=True)
class SearchChainItem:
    """搜索降级链节点"""

    searcher: WebSearcher
    cacheable: bool = True


class StaleCacheSearcher:
    """把 stale cache 包装成 searcher，插入降级链"""

    def __init__(self, cache: SearchCache) -> None:
        self._cache = cache

    @property
    def engine_name(self) -> str:
        return "stale_cache"

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        key = make_search_cache_key(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

        response = await self._cache.get_stale(key)
        if response is None:
            raise WebSearchUnavailable("stale cache miss")

        return response


class SearchCoordinator:
    """联网搜索调度器：按优先级依次尝试多种搜索策略，自动降级"""

    def __init__(
        self,
        *,
        cache: SearchCache,
        chain: Sequence[SearchChainItem],
        continue_on_empty: bool = True,
    ) -> None:
        if not chain:
            raise ValueError("搜索降级链不能为空")

        self._cache = cache
        self._chain: tuple[SearchChainItem, ...] = tuple(chain)
        self._continue_on_empty = continue_on_empty

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> Optional[SearchResponse]:
        key = make_search_cache_key(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

        fresh_response = await self._cache.get_fresh(key)
        if fresh_response is not None:
            log_ok(
                "联网搜索",
                engine="fresh_cache",
                query=query,
                max_results=max_results,
                with_images=with_images,
            )
            return fresh_response

        last_empty_response: Optional[SearchResponse] = None

        for item in self._chain:
            searcher = item.searcher
            engine_name = searcher.engine_name

            try:
                response = await searcher.search(
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )
            except WebSearchError as exc:
                log_fail(
                    "联网搜索",
                    exc,
                    engine=engine_name,
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )
                continue
            except Exception as exc:
                log_fail(
                    "联网搜索",
                    exc,
                    engine=engine_name,
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )
                continue

            if not _has_search_content(response):
                last_empty_response = response

                log_fail(
                    "联网搜索",
                    "搜索结果为空，触发降级",
                    engine=engine_name,
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )

                if self._continue_on_empty:
                    continue

                return response

            if item.cacheable:
                await self._cache.set(key, response)

            log_ok(
                "联网搜索",
                engine=engine_name,
                query=query,
                max_results=max_results,
                with_images=with_images,
                results=len(response.results),
                images=len(response.images),
            )

            return response

        log_fail(
            "联网搜索",
            "所有搜索策略均失败",
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

        return last_empty_response


def _has_search_content(response: SearchResponse) -> bool:
    return bool(response.answer or response.results or response.images)


__all__ = [
    "SearchChainItem",
    "SearchCoordinator",
    "StaleCacheSearcher",
]
```

---

# 6. `factory.py` 顶层装配

```python
from __future__ import annotations

from chat.application.web_search.coordinator import (
    SearchChainItem,
    SearchCoordinator,
    StaleCacheSearcher,
)
from chat.application.web_search.infrastructure import (
    CircuitBreakerWebSearcher,
    SearchCache,
)
from chat.application.web_search.searcher.duckduckgo_searcher import (
    DuckDuckGoBufferSearcher,
)
from chat.application.web_search.searcher.searxng_searcher import SearXNGSearcher
from chat.application.web_search.searcher.tavily_searcher import TavilySearcher
from chat.core.config.app_settings import settings


def create_search_coordinator() -> SearchCoordinator:
    cache = SearchCache(
        fresh_ttl=settings.WEB_SEARCH_FRESH_CACHE_TTL,
        stale_ttl=settings.WEB_SEARCH_STALE_CACHE_TTL,
        maxsize=settings.WEB_SEARCH_CACHE_MAXSIZE,
    )

    searxng = CircuitBreakerWebSearcher(
        SearXNGSearcher(
            base_url=settings.SEARXNG_BASE_URL,
            timeout=settings.SEARXNG_TIMEOUT,
            language=settings.SEARXNG_LANGUAGE,
            safesearch=settings.SEARXNG_SAFESEARCH,
        ),
        failure_threshold=settings.SEARXNG_FAILURE_THRESHOLD,
        cooldown_seconds=settings.SEARXNG_COOLDOWN_SECONDS,
    )

    duckduckgo = CircuitBreakerWebSearcher(
        DuckDuckGoBufferSearcher(
            timeout=settings.DUCKDUCKGO_TIMEOUT,
            region=settings.DUCKDUCKGO_REGION,
            safesearch=settings.DUCKDUCKGO_SAFESEARCH,
        ),
        failure_threshold=settings.DUCKDUCKGO_FAILURE_THRESHOLD,
        cooldown_seconds=settings.DUCKDUCKGO_COOLDOWN_SECONDS,
    )

    tavily = CircuitBreakerWebSearcher(
        TavilySearcher(
            api_key=settings.TAVILY_API_KEY,
            timeout=settings.TAVILY_TIMEOUT,
        ),
        failure_threshold=settings.TAVILY_FAILURE_THRESHOLD,
        cooldown_seconds=settings.TAVILY_COOLDOWN_SECONDS,
    )

    chain = (
        SearchChainItem(searxng, cacheable=True),
        SearchChainItem(duckduckgo, cacheable=True),
        SearchChainItem(StaleCacheSearcher(cache), cacheable=False),
        SearchChainItem(tavily, cacheable=True),
    )

    return SearchCoordinator(
        cache=cache,
        chain=chain,
        continue_on_empty=True,
    )


__all__ = [
    "create_search_coordinator",
]
```

最终策略仍然非常清晰：

```python
chain = (
    SearchChainItem(searxng, cacheable=True),
    SearchChainItem(duckduckgo, cacheable=True),
    SearchChainItem(StaleCacheSearcher(cache), cacheable=False),
    SearchChainItem(tavily, cacheable=True),
)
```

---

# 7. `searcher/__init__.py`

```python
from chat.application.web_search.searcher.base import WebSearcher
from chat.application.web_search.searcher.duckduckgo_searcher import (
    DuckDuckGoBufferSearcher,
)
from chat.application.web_search.searcher.searxng_searcher import SearXNGSearcher
from chat.application.web_search.searcher.tavily_searcher import TavilySearcher

__all__ = [
    "WebSearcher",
    "DuckDuckGoBufferSearcher",
    "SearXNGSearcher",
    "TavilySearcher",
]
```

---

# 8. `web_search/__init__.py`

```python
from chat.application.web_search.coordinator import (
    SearchChainItem,
    SearchCoordinator,
    StaleCacheSearcher,
)
from chat.application.web_search.errors import (
    WebSearchError,
    WebSearchInvalidResponse,
    WebSearchUnavailable,
)
from chat.application.web_search.factory import create_search_coordinator
from chat.application.web_search.models import (
    ImageResult,
    SearchResponse,
    SearchResult,
)

__all__ = [
    "ImageResult",
    "SearchResult",
    "SearchResponse",
    "SearchChainItem",
    "SearchCoordinator",
    "StaleCacheSearcher",
    "create_search_coordinator",
    "WebSearchError",
    "WebSearchUnavailable",
    "WebSearchInvalidResponse",
]
```

---

# 9. 最终职责分层

整理后职责非常明确：

```text
web_search/
├── coordinator.py
│   └── 负责搜索调度、降级链、fresh/stale cache 使用
│
├── factory.py
│   └── 负责按 settings 装配完整 SearchCoordinator
│
├── errors.py
│   └── 负责公共异常类型
│
├── models/
│   └── 负责通用响应模型和供应商响应映射
│
├── searcher/
│   └── 只负责真实搜索引擎调用
│
└── infrastructure/
    └── 负责缓存、熔断等基础设施能力
```

这个结构比把 `cache.py`、`circuit_breaker.py` 放在 `searcher/` 里更好，因为：

```text
cache
circuit_breaker
```

不是搜索引擎，它们是横切基础设施。

而：

```text
coordinator
factory
errors
```

也不属于 `searcher/`，应该在 `web_search` 顶层。

---

# 10. 最终形态

最终搜索链路：

```text
WebSearchTool
    ↓
SearchCoordinator
    ↓
Fresh Cache
    ↓
(
    CircuitBreaker(SearXNGSearcher),
    CircuitBreaker(DuckDuckGoBufferSearcher),
    StaleCacheSearcher,
    CircuitBreaker(TavilySearcher)
)
```

目录结构：

```text
chat/application/web_search/
├── __init__.py
├── coordinator.py
├── errors.py
├── factory.py
├── infrastructure/
│   ├── __init__.py
│   ├── cache.py
│   └── circuit_breaker.py
├── models/
│   ├── __init__.py
│   ├── common.py
│   ├── tavily.py
│   └── searxng.py
└── searcher/
    ├── __init__.py
    ├── base.py
    ├── tavily_searcher.py
    ├── searxng_searcher.py
    └── duckduckgo_searcher.py
```

这就是更优版本：**searcher 干净，基础设施独立，coordinator 顶层编排，factory 顶层装配。**
