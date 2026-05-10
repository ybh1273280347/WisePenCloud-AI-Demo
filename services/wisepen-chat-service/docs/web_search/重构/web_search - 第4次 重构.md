可以，**保留降级链**。
批评里真正该吸收的不是“不要降级链”，而是：

> **不要为了降级链，把缓存伪装成 Searcher。**

所以最终方向应该是：

```text
保留降级链风格
但链路元素不是 Searcher
而是 SearchStage
```

也就是说：

```text
Searcher = 搜索引擎适配器
Stage = 降级链中的一个步骤
```

这样既保留你一贯的链式风格，又避免语义扭曲。

---

# 最佳重构方向

不要这样：

```python
chain = (
    SearXNGSearcher(),
    DuckDuckGoSearcher(),
    StaleCacheSearcher(),   # 语义不自然
    TavilySearcher(),
)
```

改成这样：

```python
chain = (
    SearchStage("searxng", self._search_searxng, cacheable=True),
    SearchStage("duckduckgo", self._search_duckduckgo, cacheable=True),
    SearchStage("stale_cache", self._search_stale_cache, cacheable=False),
    SearchStage("tavily", self._search_tavily, cacheable=True),
)
```

这就很干净：

```text
Stale Cache 是降级链 stage
但它不是 Searcher
```

---

# 最终推荐结构

保持你已经调整好的目录也可以：

```text
web_search/
├── __init__.py
├── coordinator.py
├── errors.py
├── factory.py
├── infrastructure/
│   ├── __init__.py
│   └── cache.py
├── models/
│   ├── __init__.py
│   ├── common.py
│   ├── searxng.py
│   └── tavily.py
└── searcher/
    ├── __init__.py
    ├── base_searcher.py
    ├── duckduckgo_searcher.py
    ├── searxng_searcher.py
    └── tavily_searcher.py
```

但可以先砍掉：

```text
infrastructure/circuit_breaker.py
```

熔断器当前不是必要复杂度。

---

# 核心：`coordinator.py`

推荐用一个轻量的 `SearchStage`。

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from chat.application.web_search.infrastructure.cache import (
    SearchCache,
    make_search_cache_key,
)
from chat.application.web_search.models import SearchResponse
from chat.application.web_search.searcher.duckduckgo_searcher import DuckDuckGoSearcher
from chat.application.web_search.searcher.searxng_searcher import SearXNGSearcher
from chat.application.web_search.searcher.tavily_searcher import TavilySearcher
from common.logger import log_fail, log_ok


SearchStageFunc = Callable[..., Awaitable[Optional[SearchResponse]]]


@dataclass(frozen=True, slots=True)
class SearchStage:
    name: str
    handler: SearchStageFunc
    cacheable: bool = True


class SearchCoordinator:
    """联网搜索调度器：Fresh Cache + 显式降级链"""

    def __init__(
        self,
        *,
        cache: SearchCache,
        searxng_searcher: SearXNGSearcher,
        duckduckgo_searcher: DuckDuckGoSearcher,
        tavily_searcher: TavilySearcher,
        continue_on_empty: bool = True,
    ) -> None:
        self._cache = cache
        self._searxng = searxng_searcher
        self._duckduckgo = duckduckgo_searcher
        self._tavily = tavily_searcher
        self._continue_on_empty = continue_on_empty

        self._chain: tuple[SearchStage, ...] = (
            SearchStage("searxng", self._search_searxng, cacheable=True),
            SearchStage("duckduckgo", self._search_duckduckgo, cacheable=True),
            SearchStage("stale_cache", self._search_stale_cache, cacheable=False),
            SearchStage("tavily", self._search_tavily, cacheable=True),
        )

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

        fresh = await self._cache.get_fresh(key)
        if fresh is not None:
            log_ok("联网搜索", stage="fresh_cache", query=query)
            return _with_source(fresh, "fresh_cache")

        last_empty: Optional[SearchResponse] = None

        for stage in self._chain:
            try:
                response = await stage.handler(
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )
            except Exception as exc:
                log_fail(
                    "联网搜索",
                    exc,
                    stage=stage.name,
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )
                continue

            if response is None:
                continue

            if not _has_content(response):
                last_empty = response

                log_fail(
                    "联网搜索",
                    "搜索结果为空，触发降级",
                    stage=stage.name,
                    query=query,
                )

                if self._continue_on_empty:
                    continue

                return _with_source(response, stage.name)

            response = _with_source(response, stage.name)

            if stage.cacheable:
                await self._cache.set(key, response)

            log_ok(
                "联网搜索",
                stage=stage.name,
                query=query,
                results=len(response.results),
                images=len(response.images),
            )

            return response

        return last_empty

    async def _search_searxng(
        self,
        *,
        query: str,
        max_results: int,
        with_images: bool,
    ) -> Optional[SearchResponse]:
        return await self._searxng.search(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

    async def _search_duckduckgo(
        self,
        *,
        query: str,
        max_results: int,
        with_images: bool,
    ) -> Optional[SearchResponse]:
        return await self._duckduckgo.search(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

    async def _search_stale_cache(
        self,
        *,
        query: str,
        max_results: int,
        with_images: bool,
    ) -> Optional[SearchResponse]:
        key = make_search_cache_key(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

        return await self._cache.get_stale(key)

    async def _search_tavily(
        self,
        *,
        query: str,
        max_results: int,
        with_images: bool,
    ) -> Optional[SearchResponse]:
        return await self._tavily.search(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )


def _has_content(response: SearchResponse) -> bool:
    return bool(response.answer or response.results or response.images)


def _with_source(response: SearchResponse, source: str) -> SearchResponse:
    return SearchResponse(
        query=response.query,
        results=response.results,
        answer=response.answer,
        images=response.images,
        source=source,
    )
```

---

# 这个版本的好处

## 1. 保留降级链风格

策略仍然一眼可见：

```python
self._chain = (
    SearchStage("searxng", self._search_searxng, cacheable=True),
    SearchStage("duckduckgo", self._search_duckduckgo, cacheable=True),
    SearchStage("stale_cache", self._search_stale_cache, cacheable=False),
    SearchStage("tavily", self._search_tavily, cacheable=True),
)
```

这就是你的架构审美：**链路显式，顺序即策略**。

---

## 2. 不再伪装语义

`stale_cache` 不再是：

```python
StaleCacheSearcher
```

而是：

```python
SearchStage("stale_cache", self._search_stale_cache)
```

它是降级步骤，不是搜索引擎。

---

## 3. 不需要 Protocol 也能清晰

`SearchStage` 接收的是 handler：

```python
Callable[..., Awaitable[Optional[SearchResponse]]]
```

所以 coordinator 并不关心 handler 背后是搜索器、缓存、mock、还是别的东西。

这比强行要求所有东西都是 `WebSearcher` 更自然。

---

## 4. 不需要熔断器

第一版先砍掉熔断器。
失败直接进入下一 stage，足够了。

后面真遇到 SearXNG 连续超时，再加一个极简 cooldown 字段也可以，不必现在引入独立 `CircuitBreakerWebSearcher`。

---

# 对 `SearchResponse` 的小调整

为了调试方便，建议保留 `source`：

```python
@dataclass(frozen=True, slots=True)
class SearchResponse:
    query: str
    results: Sequence[SearchResult] = field(default_factory=tuple)
    answer: Optional[str] = None
    images: Sequence[ImageResult] = field(default_factory=tuple)
    source: Optional[str] = None
```

这样 Tool 输出时可以知道：

```text
source = searxng
source = duckduckgo
source = stale_cache
source = tavily
```

这对观察 Tavily 调用频率很有价值。

---

# `factory.py` 仍然可以保留

如果你喜欢和 `WebFetchTool` 一致，也可以让 Tool 直接：

```python
self._coordinator = SearchCoordinator(...)
```

但我建议保留 `factory.py`，因为搜索装配确实比 fetch 复杂一点。

```python
from chat.application.web_search.coordinator import SearchCoordinator
from chat.application.web_search.infrastructure.cache import SearchCache
from chat.application.web_search.searcher.duckduckgo_searcher import DuckDuckGoSearcher
from chat.application.web_search.searcher.searxng_searcher import SearXNGSearcher
from chat.application.web_search.searcher.tavily_searcher import TavilySearcher
from chat.core.config.app_settings import settings


def create_search_coordinator() -> SearchCoordinator:
    cache = SearchCache(
        fresh_ttl=settings.WEB_SEARCH_FRESH_CACHE_TTL,
        stale_ttl=settings.WEB_SEARCH_STALE_CACHE_TTL,
        maxsize=settings.WEB_SEARCH_CACHE_MAXSIZE,
    )

    return SearchCoordinator(
        cache=cache,
        searxng_searcher=SearXNGSearcher(
            base_url=settings.SEARXNG_BASE_URL,
            timeout=settings.SEARXNG_TIMEOUT,
            language=settings.SEARXNG_LANGUAGE,
            safesearch=settings.SEARXNG_SAFESEARCH,
        ),
        duckduckgo_searcher=DuckDuckGoSearcher(
            timeout=settings.DUCKDUCKGO_TIMEOUT,
            region=settings.DUCKDUCKGO_REGION,
            safesearch=settings.DUCKDUCKGO_SAFESEARCH,
        ),
        tavily_searcher=TavilySearcher(
            api_key=settings.TAVILY_API_KEY,
            timeout=settings.TAVILY_TIMEOUT,
        ),
    )
```

---

# 最终取舍

保留：

```text
降级链
SearchCoordinator
Fresh/Stale Cache
多搜索源 fallback
统一 SearchResponse
factory
```

砍掉：

```text
StaleCacheSearcher
CircuitBreakerWebSearcher
过重的 Protocol 约束
过多供应商 request dataclass
过度拆分的 infrastructure 组件
```

核心思想变成：

> **降级链保留，但链路节点叫 Stage，不叫 Searcher。**

这既保留了你的风格，也吸收了那份批评里最有价值的部分。
