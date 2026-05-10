下面是一份**最终可执行实践文档**。它按你现有 `WebFetchTool + FetchCoordinator` 的风格来设计：**Tool 只做参数校验和结果格式化，Coordinator 负责元组降级链调度**。

关键约束基于官方/公开文档确认：Tavily Python SDK 支持同步/异步客户端，`include_answer`、`include_raw_content`、`max_results` 会影响响应大小，需要显式控制；`include_images=True` 时会返回顶层图片和单条结果内图片。SearXNG Search API 支持 `/search?q=...&format=json`，但 JSON 格式必须在 `settings.yml` 中启用，否则会 403。cachetools 的 cache 类不是线程安全的，因此共享缓存需要锁。DuckDuckGo 公开 Instant Answer API 不是完整搜索结果 API；这里用 `ddgs` 作为 best-effort 免费缓冲层，而不是主力强 SLA 搜索。([Tavily Docs](https://docs.tavily.com/sdk/python/reference "SDK Reference - Tavily Docs"))

***

# Web Search 最终工程实践

## 1. 最终目标

搜索工具最终链路：

```
WebSearchTool
    ↓
SearchCoordinator
    ↓
Fresh Cache
    ↓ 未命中
(
    SearXNG,
    DuckDuckGo Buffer,
    Stale Cache,
    Tavily
)

```

策略含义：

```
1. Fresh Cache 命中
   → 直接返回

2. Fresh Cache 未命中
   → SearXNG 自部署搜索

3. SearXNG 失败 / 空结果
   → DuckDuckGo Buffer 免费缓冲

4. DuckDuckGo 失败 / 空结果
   → Stale Cache 旧缓存兜底

5. Stale Cache 未命中
   → Tavily 付费兜底

```

Agent 只看到统一工具参数：

```
query: str
max_results: int = 5
with_images: bool = False

```

不要暴露：

```
topic
search_depth
include_images
include_answer
include_raw_content
categories
engines
safesearch
format

```

这些都属于具体搜索引擎的适配层参数。

***

# 2. 依赖安装

```
pip install tavily-python httpx cachetools ddgs

```

建议 `pyproject.toml`：

```
[project]
dependencies = [
    "tavily-python>=0.5.0",
    "httpx>=0.27.0",
    "cachetools>=5.5.0",
    "ddgs>=9.0.0",
]

```

`ddgs` 当前 PyPI 包名就是 `ddgs`，提供 `text()`、`images()` 等搜索接口，适合作为免费缓冲层。([PyPI](https://pypi.org/project/ddgs/ "ddgs · PyPI"))

***

# 3. 推荐目录结构

```
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
└── searcher/
    ├── __init__.py
    ├── base.py
    ├── search_cache.py
    ├── circuit_breaker.py
    ├── tavily_searcher.py
    ├── searxng_searcher.py
    └── duckduckgo_searcher.py

```

Tool 文件保持在你现有工具目录，例如：

```
chat/domain/tools/web_search_tool.py

```

***

# 4. SearXNG 部署要求

SearXNG 必须启用 JSON 输出。官方 Search API 文档说明，JSON/CSV/RSS 这类格式由 `settings.yml` 的 `search.formats` 控制，请求未启用的格式会返回 `403 Forbidden`。([SearXNG 文档](https://docs.searxng.org/dev/search_api.html "Search API - SearXNG Documentation (2026.5.8+d8ab61a9e)"))

`settings.yml` 至少需要：

```
search:
  formats:
    - html
    - json

```

你的服务配置里：

```
SEARXNG_BASE_URL = "http://localhost:8080"

```

实际请求会打到：

```
http://localhost:8080/search?q=...&format=json

```

***

# 5. 公共模型

## `models/common.py`

```
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
    """通用搜索响应"""

    query: str
    results: Sequence[SearchResult] = field(default_factory=tuple)
    answer: Optional[str] = None
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

这里保留两层图片：

```
SearchResult.images      某条网页结果附带的图片
SearchResponse.images    本次查询整体相关图片

```

这能兼容 Tavily 的顶层图片和 result 内图片，也能兼容 SearXNG / DDGS 的图片搜索结果。Tavily 文档明确说明 `include_images=True` 时会返回顶层 `images`，并且每个 result 也可能包含自己的 `images`。([Tavily Docs](https://docs.tavily.com/sdk/python/reference "SDK Reference - Tavily Docs"))

***

## `models/__init__.py`

```
from chat.application.web_search.models.common import (
    ImageResult,
    SearchResponse,
    SearchResult,
)
from chat.application.web_search.models.tavily import (
    TavilySearchRequest,
    map_tavily_response,
)
from chat.application.web_search.models.searxng import (
    SearXNGSearchRequest,
    map_searxng_response,
    merge_search_responses,
)

__all__ = [
    "ImageResult",
    "SearchResult",
    "SearchResponse",
    "TavilySearchRequest",
    "map_tavily_response",
    "SearXNGSearchRequest",
    "map_searxng_response",
    "merge_search_responses",
]

```

***

# 6. 顶层错误类型

## `errors.py`

```
class WebSearchError(RuntimeError):
    """通用搜索错误"""


class WebSearchUnavailable(WebSearchError):
    """搜索引擎不可用，例如超时、连接失败、熔断、缓存未命中"""


class WebSearchInvalidResponse(WebSearchError):
    """搜索引擎返回了无法解析的响应"""


__all__ = [
    "WebSearchError",
    "WebSearchUnavailable",
    "WebSearchInvalidResponse",
]

```

***

# 7. 搜索器协议

## `searcher/base.py`

```
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

***

# 8. Tavily 适配

Tavily 这里保持最小公共语义：

```
query
max_results
with_images

```

`include_images`、`include_answer`、`include_raw_content` 只出现在 Tavily adapter 内部。Tavily SDK 文档说明 `include_answer`、`include_raw_content`、`max_results` 会直接影响响应大小，应该显式设置；这里为了控制成本和响应体大小，默认关闭 answer/raw content。([Tavily Docs](https://docs.tavily.com/sdk/python/reference "SDK Reference - Tavily Docs"))

## `models/tavily.py`

```
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

    只接收公共搜索语义。
    Tavily 专有参数只在 to_payload() 中出现。
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
            "include_answer": False,
            "include_raw_content": False,
            "search_depth": "basic",
        }

        if self.with_images:
            payload["include_images"] = True

        return payload


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

***

## `searcher/tavily_searcher.py`

Tavily Python SDK 提供同步客户端和异步客户端；这里用 `AsyncTavilyClient`，避免 `asyncio.to_thread()` 包同步方法。([Tavily Docs](https://docs.tavily.com/sdk/python/reference "SDK Reference - Tavily Docs"))

```
from __future__ import annotations

from tavily import AsyncTavilyClient

from chat.application.web_search.errors import WebSearchUnavailable
from chat.application.web_search.models import (
    SearchResponse,
    TavilySearchRequest,
    map_tavily_response,
)


class TavilySearcher:
    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 15.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key 不能为空")

        self._client = AsyncTavilyClient(api_key)
        self._timeout = timeout

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
            raise WebSearchUnavailable("Tavily search request failed") from exc

        return map_tavily_response(raw_response)


__all__ = [
    "TavilySearcher",
]

```

***

# 9. SearXNG 适配

SearXNG JSON API 使用 `format=json`；`categories` 是可选参数，可用于普通网页分类和图片分类。官方文档说明 `/` 和 `/search` 均支持 GET/POST，`q` 为查询字段，`categories` 可指定搜索分类。([SearXNG 文档](https://docs.searxng.org/dev/search_api.html "Search API - SearXNG Documentation (2026.5.8+d8ab61a9e)"))

## `models/searxng.py`

```
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from chat.application.web_search.models.common import (
    ImageResult,
    SearchResponse,
    SearchResult,
)


@dataclass(frozen=True, slots=True)
class SearXNGSearchRequest:
    query: str
    category: Optional[str] = None
    language: Optional[str] = None
    safesearch: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query 不能为空")

    def to_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "q": self.query,
            "format": "json",
        }

        if self.category:
            params["categories"] = self.category

        if self.language:
            params["language"] = self.language

        if self.safesearch is not None:
            params["safesearch"] = self.safesearch

        return params


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

    if images_only:
        images = tuple(
            image
            for item in raw_results
            if isinstance(item, Mapping)
            for image in _map_result_images(item)
            if image.url
        )

        return SearchResponse(
            query=query,
            results=(),
            answer=_to_optional_str(data.get("answer")),
            images=images[:max_results],
        )

    results = tuple(
        result
        for item in raw_results
        if isinstance(item, Mapping)
        for result in (_map_searxng_result(item),)
        if result.url
    )

    return SearchResponse(
        query=query,
        results=results[:max_results],
        answer=_to_optional_str(data.get("answer")),
    )


def merge_search_responses(
    web_response: SearchResponse,
    image_response: SearchResponse,
) -> SearchResponse:
    return SearchResponse(
        query=web_response.query,
        results=web_response.results,
        answer=web_response.answer or image_response.answer,
        images=image_response.images,
    )


def _map_searxng_result(item: Mapping[str, Any]) -> SearchResult:
    return SearchResult(
        title=str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        snippet=str(item.get("content") or item.get("snippet") or ""),
        images=_map_result_images(item),
    )


def _map_result_images(item: Mapping[str, Any]) -> tuple[ImageResult, ...]:
    img_url = (
        item.get("img_src")
        or item.get("thumbnail")
        or item.get("thumbnail_src")
    )

    if not img_url:
        return ()

    return (
        ImageResult(
            url=str(img_url),
            desc=_to_optional_str(item.get("title")),
        ),
    )


def _to_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None

    return str(value)


__all__ = [
    "SearXNGSearchRequest",
    "map_searxng_response",
    "merge_search_responses",
]

```

***

## `searcher/searxng_searcher.py`

```
from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from chat.application.web_search.errors import (
    WebSearchInvalidResponse,
    WebSearchUnavailable,
)
from chat.application.web_search.models import SearchResponse
from chat.application.web_search.models.searxng import (
    SearXNGSearchRequest,
    map_searxng_response,
    merge_search_responses,
)


class SearXNGSearcher:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 5.0,
        language: Optional[str] = None,
        safesearch: Optional[int] = None,
        web_category: str = "general",
        image_category: str = "images",
    ) -> None:
        base_url = base_url.rstrip("/")

        if not base_url:
            raise ValueError("base_url 不能为空")

        self._base_url = base_url
        self._timeout = timeout
        self._language = language
        self._safesearch = safesearch
        self._web_category = web_category
        self._image_category = image_category

    @property
    def engine_name(self) -> str:
        return "searxng"

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        if not with_images:
            return await self._search_web(
                query=query,
                max_results=max_results,
            )

        web_response, image_response = await asyncio.gather(
            self._search_web(query=query, max_results=max_results),
            self._search_images(query=query, max_results=max_results),
        )

        return merge_search_responses(web_response, image_response)

    async def _search_web(
        self,
        *,
        query: str,
        max_results: int,
    ) -> SearchResponse:
        request = SearXNGSearchRequest(
            query=query,
            category=self._web_category,
            language=self._language,
            safesearch=self._safesearch,
        )

        data = await self._get_json(request.to_params())

        return map_searxng_response(
            data,
            query=query,
            max_results=max_results,
            images_only=False,
        )

    async def _search_images(
        self,
        *,
        query: str,
        max_results: int,
    ) -> SearchResponse:
        request = SearXNGSearchRequest(
            query=query,
            category=self._image_category,
            language=self._language,
            safesearch=self._safesearch,
        )

        data = await self._get_json(request.to_params())

        return map_searxng_response(
            data,
            query=query,
            max_results=max_results,
            images_only=True,
        )

    async def _get_json(self, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/search"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                raise WebSearchUnavailable(
                    "SearXNG returned 403. Check settings.yml: search.formats must include json."
                ) from exc

            raise WebSearchUnavailable(
                f"SearXNG search failed: HTTP {exc.response.status_code}"
            ) from exc

        except httpx.HTTPError as exc:
            raise WebSearchUnavailable("SearXNG search request failed") from exc

        except ValueError as exc:
            raise WebSearchInvalidResponse("SearXNG response is not valid JSON") from exc

        if not isinstance(data, dict):
            raise WebSearchInvalidResponse("SearXNG response must be a JSON object")

        return data


__all__ = [
    "SearXNGSearcher",
]

```

***

# 10. DuckDuckGo Buffer

DuckDuckGo Instant Answer API 不是完整搜索结果 API，所以不把它作为强搜索引擎。这里使用 `ddgs` 包作为免费 buffer。`ddgs` 是第三方元搜索库，PyPI 描述其支持 `text()`、`images()` 等能力。([Postman](https://www.postman.com/api-evangelist/duckduckgo/documentation/i9r819s/duckduckgo-instant-answer-api "DuckDuckGo Instant Answer API | Documentation | Postman API Network"))

## `searcher/duckduckgo_searcher.py`

```
from __future__ import annotations

import asyncio
from typing import Any, Mapping

from ddgs import DDGS

from chat.application.web_search.errors import WebSearchUnavailable
from chat.application.web_search.models import ImageResult, SearchResponse, SearchResult


class DuckDuckGoBufferSearcher:
    """DuckDuckGo 免费缓冲搜索器。

    注意：这是 best-effort buffer，不是强 SLA 主搜索。
    """

    def __init__(
        self,
        *,
        timeout: float = 8.0,
        region: str = "wt-wt",
        safesearch: str = "moderate",
    ) -> None:
        self._timeout = timeout
        self._region = region
        self._safesearch = safesearch

    @property
    def engine_name(self) -> str:
        return "duckduckgo_buffer"

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self._search_sync,
                    query,
                    max_results,
                    with_images,
                ),
                timeout=self._timeout,
            )
        except Exception as exc:
            raise WebSearchUnavailable("DuckDuckGo buffer search failed") from exc

    def _search_sync(
        self,
        query: str,
        max_results: int,
        with_images: bool,
    ) -> SearchResponse:
        with DDGS(timeout=int(self._timeout)) as ddgs:
            text_items = list(
                ddgs.text(
                    query,
                    region=self._region,
                    safesearch=self._safesearch,
                    max_results=max_results,
                )
            )

            results = tuple(
                result
                for item in text_items
                if isinstance(item, Mapping)
                for result in (_map_text_result(item),)
                if result.url
            )

            images: tuple[ImageResult, ...] = ()
            if with_images:
                image_items = list(
                    ddgs.images(
                        query,
                        region=self._region,
                        safesearch=self._safesearch,
                        max_results=max_results,
                    )
                )

                images = tuple(
                    image
                    for item in image_items
                    if isinstance(item, Mapping)
                    for image in (_map_image_result(item),)
                    if image.url
                )

            return SearchResponse(
                query=query,
                results=results,
                images=images,
            )


def _map_text_result(item: Mapping[str, Any]) -> SearchResult:
    return SearchResult(
        title=str(item.get("title") or ""),
        url=str(item.get("href") or item.get("url") or ""),
        snippet=str(item.get("body") or item.get("snippet") or ""),
    )


def _map_image_result(item: Mapping[str, Any]) -> ImageResult:
    return ImageResult(
        url=str(item.get("image") or item.get("thumbnail") or ""),
        desc=str(item.get("title") or "") or None,
    )


__all__ = [
    "DuckDuckGoBufferSearcher",
]

```

***

# 11. 搜索缓存

这里使用 `cachetools.TTLCache`。官方文档说明 cachetools 的 cache 类不是线程安全的，所以这里用 `asyncio.Lock` 保护共享缓存。([Cachetools](https://cachetools.readthedocs.io/ "cachetools — Extensible memoizing collections and decorators — cachetools 7.1.1 documentation"))

## `searcher/search_cache.py`

```
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

不把 `session_id` 放进 cache key。搜索结果通常可以跨会话复用，放 session 会显著降低命中率。

***

# 12. 熔断器

## `searcher/circuit_breaker.py`

```
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

***

# 13. 调度器：元组降级链

这是核心。它和你的 `FetchCoordinator` 设计保持一致：具体搜索器只负责执行，Coordinator 负责编排、降级、缓存、日志和结果判定。

## `coordinator.py`

```
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from chat.application.web_search.errors import WebSearchError, WebSearchUnavailable
from chat.application.web_search.models import SearchResponse
from chat.application.web_search.searcher.base import WebSearcher
from chat.application.web_search.searcher.search_cache import (
    SearchCache,
    make_search_cache_key,
)
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
    """联网搜索调度器：按优先级依次尝试多种搜索策略，自动降级。

    链路:
        FreshCache
            ↓ 未命中
        元组降级链:
            SearXNG → DuckDuckGoBuffer → StaleCache → Tavily
    """

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

***

# 14. 工厂装配

## `factory.py`

```
from __future__ import annotations

from chat.application.web_search.coordinator import (
    SearchChainItem,
    SearchCoordinator,
    StaleCacheSearcher,
)
from chat.application.web_search.searcher.circuit_breaker import (
    CircuitBreakerWebSearcher,
)
from chat.application.web_search.searcher.duckduckgo_searcher import (
    DuckDuckGoBufferSearcher,
)
from chat.application.web_search.searcher.search_cache import SearchCache
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

这就是最终降级链：

```
chain = (
    SearchChainItem(searxng, cacheable=True),
    SearchChainItem(duckduckgo, cacheable=True),
    SearchChainItem(StaleCacheSearcher(cache), cacheable=False),
    SearchChainItem(tavily, cacheable=True),
)

```

重点是：

```
StaleCacheSearcher 在 Tavily 前面

```

这样 SearXNG Docker 挂掉时，不会立刻烧 Tavily。

***

# 15. searcher 包导出

## `searcher/__init__.py`

```
from chat.application.web_search.searcher.base import WebSearcher
from chat.application.web_search.searcher.circuit_breaker import (
    CircuitBreakerWebSearcher,
)
from chat.application.web_search.searcher.duckduckgo_searcher import (
    DuckDuckGoBufferSearcher,
)
from chat.application.web_search.searcher.search_cache import (
    SearchCache,
    SearchCacheKey,
    make_search_cache_key,
)
from chat.application.web_search.searcher.searxng_searcher import SearXNGSearcher
from chat.application.web_search.searcher.tavily_searcher import TavilySearcher

__all__ = [
    "WebSearcher",
    "CircuitBreakerWebSearcher",
    "DuckDuckGoBufferSearcher",
    "SearchCache",
    "SearchCacheKey",
    "make_search_cache_key",
    "SearXNGSearcher",
    "TavilySearcher",
]

```

***

# 16. web\_search 顶层导出

## `__init__.py`

```
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

***

# 17. Tool 最终实现

这个 Tool 参考你的 `WebFetchTool`：构造时创建 coordinator，执行时只做参数解析、调用、格式化。

## `web_search_tool.py`

```
from __future__ import annotations

from typing import Any, Dict, Optional

from chat.application.web_search import SearchResponse, create_search_coordinator
from chat.application.web_search.coordinator import SearchCoordinator
from chat.application.web_search.models import ImageResult
from chat.core.config.app_settings import settings
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail


_TRUNCATION_MARKER = "\n\n...(Search result truncated due to length)"


class WebSearchTool(BaseTool):
    """联网搜索工具"""

    def __init__(self, coordinator: Optional[SearchCoordinator] = None):
        self._coordinator = coordinator or create_search_coordinator()

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        """执行联网搜索并返回格式化搜索结果"""
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        query = _get_query(kwargs)
        if not query:
            return "[Tool Error] Missing required query parameter."

        max_results = _normalize_max_results(
            kwargs.get("max_results"),
            default=5,
            upper=10,
        )

        # schema 对外使用 with_images；兼容历史 include_images
        with_images = _normalize_bool(
            kwargs.get("with_images", kwargs.get("include_images", False))
        )

        try:
            response = await self._coordinator.search(
                query=query,
                max_results=max_results,
                with_images=with_images,
            )
        except Exception as exc:
            log_fail(
                "联网搜索工具",
                exc,
                session_id=session_id,
                query=query,
                max_results=max_results,
                with_images=with_images,
            )
            return "[Tool Error] Unexpected error while searching the web."

        if response is None:
            return "[Tool Result] Failed to search the web (all search methods exhausted)."

        if not _has_search_content(response):
            return "[Tool Result] No results found for the query."

        return _format_response(response)


_TOOL_DESCRIPTION = (
    "Searches the web using the configured search engine chain. "
    "Use this tool when current information, external facts, source lookup, or web evidence is required.\n\n"
    "**Search strategy:** Multi-stage automatic fallback: "
    "1) fresh cache; 2) self-hosted SearXNG; 3) DuckDuckGo buffer; "
    "4) stale cache; 5) paid Tavily fallback.\n\n"
    "**Result format:** Returns web results with title, URL, and snippet. "
    "When requested, it can also return image results.\n\n"
    "**with_images:** Set to true only when the user asks for pictures, photos, visual references, "
    "locations, people, animals, products, UI screenshots, or other visual information. "
    "Do not enable it for ordinary factual text search because it may increase latency."
)


_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The web search query string.",
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum number of web results to return. Default is 5. Maximum is 10.",
            "default": 5,
            "minimum": 1,
            "maximum": 10,
        },
        "with_images": {
            "type": "boolean",
            "description": (
                "Whether to include relevant image results. "
                "Use this when the user asks for pictures, photos, visual references, locations, "
                "people, products, animals, screenshots, or other visual information."
            ),
            "default": False,
        },
    },
    "required": ["query"],
}


def _get_query(kwargs: Dict[str, Any]) -> Optional[str]:
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


def _format_response(response: SearchResponse) -> str:
    lines = [f"[Tool Result] Web search results for: {response.query}"]

    if response.answer:
        lines.append(f"\nAnswer:\n{response.answer}")

    if response.results:
        lines.append("\nResults:")

    for index, result in enumerate(response.results, 1):
        title = result.title.strip() or result.url or "(no title)"
        url = result.url.strip()
        snippet = result.snippet.strip()

        lines.append(f"\n{index}. {title}")

        if url:
            lines.append(f"   URL: {url}")

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

    return _normalize_search_result("\n".join(lines))


def _format_image_line(image: ImageResult, *, indent: str) -> str:
    url = image.url.strip()
    desc = image.desc.strip() if image.desc else ""

    if desc:
        return f"{indent}- {url} ({desc})"

    return f"{indent}- {url}"


def _normalize_search_result(result: str) -> str:
    result = result.strip()

    if len(result) > settings.TOOL_RESULT_MAX_CHARS:
        limit = settings.TOOL_RESULT_MAX_CHARS
        keep_len = max(0, limit - len(_TRUNCATION_MARKER))
        result = result[:keep_len].rstrip() + _TRUNCATION_MARKER

    return result

```

***

# 18. 配置项

在你的 `settings` 中补充：

```
# Web Search Cache
WEB_SEARCH_FRESH_CACHE_TTL: int = 3600
WEB_SEARCH_STALE_CACHE_TTL: int = 86400
WEB_SEARCH_CACHE_MAXSIZE: int = 1024

# SearXNG
SEARXNG_BASE_URL: str = "http://localhost:8080"
SEARXNG_TIMEOUT: float = 5.0
SEARXNG_LANGUAGE: str | None = None
SEARXNG_SAFESEARCH: int | None = 1
SEARXNG_FAILURE_THRESHOLD: int = 3
SEARXNG_COOLDOWN_SECONDS: int = 60

# DuckDuckGo Buffer
DUCKDUCKGO_TIMEOUT: float = 8.0
DUCKDUCKGO_REGION: str = "wt-wt"
DUCKDUCKGO_SAFESEARCH: str = "moderate"
DUCKDUCKGO_FAILURE_THRESHOLD: int = 3
DUCKDUCKGO_COOLDOWN_SECONDS: int = 120

# Tavily
TAVILY_API_KEY: str = ""
TAVILY_TIMEOUT: float = 15.0
TAVILY_FAILURE_THRESHOLD: int = 5
TAVILY_COOLDOWN_SECONDS: int = 60

```

***

# 19. 最终行为验证

## 文本搜索

```
tool = WebSearchTool()

result = await tool.execute(
    {"session_id": "s1"},
    query="Python dataclass frozen slots",
    max_results=5,
)

```

执行链路：

```
FreshCache
→ SearXNG
→ DuckDuckGoBuffer
→ StaleCache
→ Tavily

```

## 图片搜索

```
result = await tool.execute(
    {"session_id": "s1"},
    query="Mount Fuji cherry blossom photos",
    max_results=5,
    with_images=True,
)

```

行为：

```
SearXNG:
    general 搜索 + images 分类搜索

DuckDuckGo Buffer:
    text() + images()

Tavily:
    include_images=True

```

Tool 输出中会包含：

```
Query-level images:
   - image_url

```

***

# 20. 最终原则

这版实现遵守下面几条边界：

```
WebSearchTool
    只负责参数校验、调用调度器、格式化结果

SearchCoordinator
    负责 Fresh Cache、元组降级链、Stale Cache、降级日志

Searcher
    只负责具体引擎调用

Model Mapper
    负责供应商响应 → SearchResponse

Tavily
    只作为最后付费兜底

DuckDuckGo Buffer
    只作为免费 best-effort 缓冲

SearXNG
    作为主力自部署搜索

```

最终链路固定为：

```
FreshCache
    ↓
SearXNG
    ↓
DuckDuckGoBuffer
    ↓
StaleCache
    ↓
Tavily

```

这就是当前最稳的实践：**多引擎兼容、Docker 故障可降级、Tavily 成本可控、Agent 接口稳定、Tool 层保持轻量。**
