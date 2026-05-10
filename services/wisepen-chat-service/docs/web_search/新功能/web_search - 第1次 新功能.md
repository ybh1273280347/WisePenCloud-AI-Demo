下面是结合官方/公开文档后，我建议定下来的**最终实践文档**。核心目标是：**多引擎兼容、控制 Tavily 成本、SearXNG 挂掉时有缓冲、对 AI Agent 暴露稳定接口**。

---

# Web Search 最终实践文档

## 1. 设计目标

系统最终不要绑定某一个搜索供应商。上层 AI Agent 只应该看到一个统一工具：

```python
search(
    query: str,
    max_results: int = 5,
    with_images: bool = False,
) -> SearchResponse
```

不要让 Agent 直接接触这些供应商字段：

```python
topic
search_depth
include_images
include_answer
include_raw_content
categories
engines
format
safesearch
```

原因是这些字段要么是 Tavily 特化参数，要么是 SearXNG 特化参数，不适合作为长期公共协议。Tavily 官方 Search API 有 `search_depth`、`topic`、`include_answer`、`include_raw_content`、`include_images` 等参数；SearXNG 官方 Search API 则是通过 `/search?q=...&format=json`、`categories`、`engines` 等参数控制行为。两者参数模型明显不同。([Tavily Docs][1])

---

## 2. 官方约束总结

### Tavily

Tavily 适合作为**高质量付费兜底搜索器**，不适合在 SearXNG 失败时被无节制调用。Tavily 官方文档说明 `max_results`、`include_answer`、`include_raw_content` 会直接影响响应大小，且 `search_depth` 可能影响 credit 消耗；因此这些参数应该由系统配置控制，而不是让 Agent 随意传入。([Tavily Docs][1])

### SearXNG

SearXNG 适合作为**主力自部署搜索器**。官方 Search API 支持 `GET /search` 和 `POST /search`，JSON 请求需要传 `format=json`；但 JSON 格式必须在 `settings.yml` 的 `search.formats` 中启用，否则请求会返回 `403 Forbidden`。([SearXNG 文档][2])

SearXNG 官方提供容器安装文档，也支持 Docker / Podman 方式部署；官方安装总览中也推荐使用 container 或 installation script 作为常规安装方式。([SearXNG 文档][3])

### DuckDuckGo

DuckDuckGo 不建议作为完整 Web Search API 的主力。公开的 DuckDuckGo Instant Answer API 文档明确说明它不是完整搜索结果 API，不包含所有链接结果，主要用于 Instant Answers、摘要、分类、消歧义等。([Postman][4])

如果要用 DuckDuckGo 做免费缓冲，可以考虑第三方 `ddgs` / `duckduckgo-search` 包，但它不是 DuckDuckGo 官方完整搜索 API。`duckduckgo-search` 的 PyPI 页面也提示该包已重命名为 `ddgs`；`ddgs` GitHub 页面将其描述为 metasearch library，并带有 educational purpose 的免责声明。因此它适合作为**best-effort buffer**，不适合作为强 SLA 主搜索。([PyPI][5])

---

# 3. 最终搜索链路

推荐链路：

```text
WebSearchTool
    ↓
CachedWebSearcher
    ↓
FallbackWebSearcher
    ├── CircuitBreaker(SearXNGSearcher)
    ├── CircuitBreaker(DuckDuckGoBufferSearcher)
    └── CircuitBreaker(TavilySearcher)
```

执行策略：

```text
1. Fresh Cache 命中
   → 直接返回

2. Fresh Cache 未命中
   → 调 SearXNG

3. SearXNG 挂了 / 超时 / 空结果
   → 调 DuckDuckGo Buffer

4. DuckDuckGo Buffer 失败 / 空结果
   → 尝试 Stale Cache

5. Stale Cache 没有
   → 最后调用 Tavily

6. Tavily 成功
   → 写入 Fresh Cache + Stale Cache
```

这比：

```text
SearXNG 挂了 → 立刻 Tavily
```

安全很多。Tavily 是付费兜底，不应该因为 Docker 服务短暂异常就被大量触发。

---

# 4. 推荐目录结构

```text
chat/application/web_search/
├── __init__.py
├── models/
│   ├── __init__.py
│   ├── common.py
│   ├── tavily.py
│   ├── searxng.py
│   └── duckduckgo.py
└── searcher/
    ├── __init__.py
    ├── base.py
    ├── errors.py
    ├── tavily_searcher.py
    ├── searxng_searcher.py
    ├── duckduckgo_searcher.py
    ├── circuit_breaker.py
    ├── fallback_searcher.py
    ├── cached_searcher.py
    └── factory.py
```

---

# 5. 公共模型设计

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

```python
SearchResult.images
SearchResponse.images
```

含义分别是：

```text
SearchResult.images    某条网页结果附带的图片
SearchResponse.images  本次 query 相关的整体图片结果
```

这样可以同时兼容 Tavily、SearXNG 图片分类搜索、以及 DuckDuckGo 图片结果。

---

# 6. 统一搜索器接口

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
```

所有搜索器都必须实现这个接口：

```text
TavilySearcher
SearXNGSearcher
DuckDuckGoBufferSearcher
FallbackWebSearcher
CachedWebSearcher
CircuitBreakerWebSearcher
```

---

# 7. 通用错误类型

## `searcher/errors.py`

```python
class WebSearchError(RuntimeError):
    """通用搜索错误"""


class WebSearchUnavailable(WebSearchError):
    """搜索引擎不可用，例如超时、连接失败、5xx"""


class WebSearchInvalidResponse(WebSearchError):
    """搜索引擎返回了无法解析的响应"""
```

具体搜索器内部不要把 `httpx.HTTPError`、Tavily SDK 异常、第三方 DuckDuckGo 包异常直接抛给上层。统一包装成 `WebSearchError` 系列。

---

# 8. Tavily 适配层

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
```

Tavily 官方文档中 `include_images` 是 Tavily 参数，但公共接口应该使用 `with_images`。也就是说，`include_images` 只能出现在 Tavily adapter 内部。([Tavily Docs][1])

---

## `searcher/tavily_searcher.py`

```python
from __future__ import annotations

from typing import Optional

from tavily import AsyncTavilyClient

from chat.application.web_search.models import (
    SearchResponse,
    TavilySearchRequest,
    map_tavily_response,
)
from chat.application.web_search.searcher.errors import WebSearchUnavailable


class TavilySearcher:
    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 15.0,
        project_id: Optional[str] = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key 不能为空")

        self._client = AsyncTavilyClient(
            api_key=api_key,
            project_id=project_id,
        )
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
```

---

# 9. SearXNG 适配层

## 部署要求

SearXNG 需要启用 JSON 输出：

```yaml
search:
  formats:
    - html
    - json
```

否则请求：

```text
/search?q=xxx&format=json
```

会返回 `403 Forbidden`。这是 SearXNG 官方 Search API 文档明确说明的行为。([SearXNG 文档][2])

---

## `models/searxng.py`

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
        )

        return SearchResponse(
            query=query,
            results=(),
            answer=_to_optional_str(data.get("answer")),
            images=images[:max_results],
        )

    results = tuple(
        _map_searxng_result(item)
        for item in raw_results
        if isinstance(item, Mapping)
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
```

SearXNG 的官方 Search API 使用 `categories` 做分类控制，图片搜索可以通过 `categories=images` 实现；但为了保持公共接口稳定，上层仍然只传 `with_images=True`，由 SearXNG adapter 内部决定是否额外请求图片分类。([SearXNG 文档][2])

---

## `searcher/searxng_searcher.py`

```python
from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from chat.application.web_search.models import SearchResponse
from chat.application.web_search.models.searxng import (
    SearXNGSearchRequest,
    map_searxng_response,
    merge_search_responses,
)
from chat.application.web_search.searcher.errors import (
    WebSearchInvalidResponse,
    WebSearchUnavailable,
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
```

---

# 10. DuckDuckGo Buffer 层

DuckDuckGo 不作为主搜索器，只作为缓冲层。

原因：

1. 官方公开 Instant Answer API 不是完整搜索结果 API。([Postman][4])
2. 第三方 `ddgs` / `duckduckgo-search` 可以拿到 text/images/news 等结果，但它不是官方稳定 SLA API，适合 best-effort buffer。([PyPI][5])

## `searcher/duckduckgo_searcher.py`

```python
from __future__ import annotations

import asyncio
from typing import Any

from ddgs import DDGS

from chat.application.web_search.models import ImageResult, SearchResponse, SearchResult
from chat.application.web_search.searcher.errors import WebSearchUnavailable


class DuckDuckGoBufferSearcher:
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
        with DDGS() as ddgs:
            text_items = list(
                ddgs.text(
                    query,
                    region=self._region,
                    safesearch=self._safesearch,
                    max_results=max_results,
                )
            )

            results = tuple(_map_text_result(item) for item in text_items)

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
                images = tuple(_map_image_result(item) for item in image_items)

            return SearchResponse(
                query=query,
                results=results,
                images=images,
            )


def _map_text_result(item: dict[str, Any]) -> SearchResult:
    return SearchResult(
        title=str(item.get("title") or ""),
        url=str(item.get("href") or item.get("url") or ""),
        snippet=str(item.get("body") or item.get("snippet") or ""),
    )


def _map_image_result(item: dict[str, Any]) -> ImageResult:
    return ImageResult(
        url=str(item.get("image") or item.get("thumbnail") or ""),
        desc=str(item.get("title") or "") or None,
    )
```

这个搜索器应该放在 SearXNG 后、Tavily 前。

---

# 11. 熔断器

## `searcher/circuit_breaker.py`

```python
from __future__ import annotations

import time

from chat.application.web_search.models import SearchResponse
from chat.application.web_search.searcher.base import WebSearcher
from chat.application.web_search.searcher.errors import WebSearchUnavailable


class CircuitBreakerWebSearcher:
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
```

---

# 12. Fallback 搜索器

## `searcher/fallback_searcher.py`

```python
from __future__ import annotations

from typing import Sequence

from chat.application.web_search.models import SearchResponse
from chat.application.web_search.searcher.base import WebSearcher
from chat.application.web_search.searcher.errors import WebSearchError
from common.logger import log_error


class FallbackWebSearcher:
    def __init__(
        self,
        searchers: Sequence[WebSearcher],
        *,
        continue_on_empty: bool = True,
    ) -> None:
        if not searchers:
            raise ValueError("searchers 不能为空")

        self._searchers = tuple(searchers)
        self._continue_on_empty = continue_on_empty

    @property
    def engine_name(self) -> str:
        engines = ",".join(searcher.engine_name for searcher in self._searchers)
        return f"fallback({engines})"

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        last_error: Exception | None = None

        for searcher in self._searchers:
            try:
                response = await searcher.search(
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )

                if _has_content(response):
                    return response

                if not self._continue_on_empty:
                    return response

            except Exception as exc:
                last_error = exc
                log_error(
                    "搜索引擎失败，尝试下一个 fallback",
                    exc,
                    engine=searcher.engine_name,
                    query=query,
                )

        if last_error is not None:
            raise WebSearchError("All search engines failed") from last_error

        return SearchResponse(query=query)


def _has_content(response: SearchResponse) -> bool:
    return bool(response.answer or response.results or response.images)
```

---

# 13. 双层缓存

这里推荐 `cachetools.TTLCache`。官方文档说明 `TTLCache` 是带 per-item TTL 的 LRU cache，默认使用 `time.monotonic()`；但 cachetools 的 cache 类不是线程安全的，因此共享访问时需要同步。([Cachetools][6])

## `searcher/cached_searcher.py`

```python
from __future__ import annotations

import asyncio
from typing import Optional

from cachetools import TTLCache

from chat.application.web_search.models import SearchResponse
from chat.application.web_search.searcher.base import WebSearcher


SearchCacheKey = tuple[str, int, bool]


class CachedWebSearcher:
    def __init__(
        self,
        searcher: WebSearcher,
        *,
        fresh_ttl: int = 3600,
        stale_ttl: int = 86400,
        maxsize: int = 1024,
    ) -> None:
        self._searcher = searcher

        self._fresh_cache: TTLCache[SearchCacheKey, SearchResponse] = TTLCache(
            maxsize=maxsize,
            ttl=fresh_ttl,
        )
        self._stale_cache: TTLCache[SearchCacheKey, SearchResponse] = TTLCache(
            maxsize=maxsize,
            ttl=stale_ttl,
        )

        self._lock = asyncio.Lock()

    @property
    def engine_name(self) -> str:
        return f"cached({self._searcher.engine_name})"

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        key = _make_cache_key(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

        fresh = await self._get_fresh(key)
        if fresh is not None:
            return fresh

        try:
            response = await self._searcher.search(
                query=query,
                max_results=max_results,
                with_images=with_images,
            )

            if _has_content(response):
                await self._set_cache(key, response)

            return response

        except Exception:
            stale = await self._get_stale(key)
            if stale is not None:
                return stale

            raise

    async def _get_fresh(
        self,
        key: SearchCacheKey,
    ) -> Optional[SearchResponse]:
        async with self._lock:
            return self._fresh_cache.get(key)

    async def _get_stale(
        self,
        key: SearchCacheKey,
    ) -> Optional[SearchResponse]:
        async with self._lock:
            return self._stale_cache.get(key)

    async def _set_cache(
        self,
        key: SearchCacheKey,
        response: SearchResponse,
    ) -> None:
        async with self._lock:
            self._fresh_cache[key] = response
            self._stale_cache[key] = response


def _make_cache_key(
    *,
    query: str,
    max_results: int,
    with_images: bool,
) -> SearchCacheKey:
    normalized_query = " ".join(query.strip().lower().split())
    return normalized_query, max_results, with_images


def _has_content(response: SearchResponse) -> bool:
    return bool(response.answer or response.results or response.images)
```

注意：这里不建议把 `session_id` 放进 cache key。搜索结果通常可以跨会话复用；放 `session_id` 会显著降低命中率。

---

# 14. 工厂组装

## `searcher/factory.py`

```python
from __future__ import annotations

from chat.application.web_search.searcher.base import WebSearcher
from chat.application.web_search.searcher.cached_searcher import CachedWebSearcher
from chat.application.web_search.searcher.circuit_breaker import CircuitBreakerWebSearcher
from chat.application.web_search.searcher.duckduckgo_searcher import DuckDuckGoBufferSearcher
from chat.application.web_search.searcher.fallback_searcher import FallbackWebSearcher
from chat.application.web_search.searcher.searxng_searcher import SearXNGSearcher
from chat.application.web_search.searcher.tavily_searcher import TavilySearcher
from chat.core.config.app_settings import settings


def create_web_searcher() -> WebSearcher:
    searchers: list[WebSearcher] = []

    if settings.SEARXNG_ENABLED:
        searchers.append(
            CircuitBreakerWebSearcher(
                SearXNGSearcher(
                    base_url=settings.SEARXNG_BASE_URL,
                    timeout=settings.SEARXNG_TIMEOUT,
                    language=settings.SEARXNG_LANGUAGE,
                    safesearch=settings.SEARXNG_SAFESEARCH,
                ),
                failure_threshold=3,
                cooldown_seconds=60,
            )
        )

    if settings.DUCKDUCKGO_BUFFER_ENABLED:
        searchers.append(
            CircuitBreakerWebSearcher(
                DuckDuckGoBufferSearcher(
                    timeout=settings.DUCKDUCKGO_TIMEOUT,
                ),
                failure_threshold=3,
                cooldown_seconds=120,
            )
        )

    if settings.TAVILY_ENABLED:
        searchers.append(
            CircuitBreakerWebSearcher(
                TavilySearcher(
                    api_key=settings.TAVILY_API_KEY,
                    timeout=settings.TAVILY_TIMEOUT,
                ),
                failure_threshold=5,
                cooldown_seconds=60,
            )
        )

    fallback = FallbackWebSearcher(
        searchers=searchers,
        continue_on_empty=True,
    )

    return CachedWebSearcher(
        fallback,
        fresh_ttl=settings.WEB_SEARCH_FRESH_CACHE_TTL,
        stale_ttl=settings.WEB_SEARCH_STALE_CACHE_TTL,
        maxsize=settings.WEB_SEARCH_CACHE_MAXSIZE,
    )
```

---

# 15. WebSearchTool 最终形态

`WebSearchTool` 不再知道 Tavily、SearXNG、DuckDuckGo、缓存、熔断这些细节。

```python
from __future__ import annotations

from typing import Any, Optional

from chat.application.web_search import SearchResponse
from chat.application.web_search.searcher.base import WebSearcher
from chat.core.config.app_settings import settings
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_error


class WebSearchTool(BaseTool):
    def __init__(self, searcher: WebSearcher) -> None:
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
        query = _get_query(kwargs)

        if query is None:
            return "[Tool Error] Missing or invalid query."

        max_results = _normalize_max_results(
            kwargs.get("max_results"),
            default=5,
            upper=10,
        )

        with_images = _normalize_bool(
            kwargs.get("with_images", kwargs.get("include_images", False))
        )

        try:
            response = await self._searcher.search(
                query=query,
                max_results=max_results,
                with_images=with_images,
            )

            if not _has_search_content(response):
                return "[Tool Result] No results found for the query."

            return _format_response(response)

        except Exception as exc:
            log_error(
                "联网搜索失败",
                exc,
                session_id=session_id,
                engine=self._searcher.engine_name,
                query=query,
                max_results=max_results,
                with_images=with_images,
            )
            return "[Tool Error] An error occurred while searching the web."


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

---

# 16. Tool Schema

字段统一用：

```python
with_images
```

不要再对 Agent 暴露：

```python
include_images
```

## `_TOOL_SCHEMA`

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

## `_TOOL_DESCRIPTION`

```python
_TOOL_DESCRIPTION = (
    "Searches the web using the configured search engine chain. "
    "Use this tool when current information, external facts, source lookup, or web evidence is required. "
    "Returns relevant web results with title, URL, and snippet. "
    "When requested, it can also return image results."
)
```

---

# 17. 推荐配置

```python
WEB_SEARCH_FRESH_CACHE_TTL = 3600
WEB_SEARCH_STALE_CACHE_TTL = 86400
WEB_SEARCH_CACHE_MAXSIZE = 1024

SEARXNG_ENABLED = True
SEARXNG_BASE_URL = "http://localhost:8080"
SEARXNG_TIMEOUT = 5.0
SEARXNG_LANGUAGE = None
SEARXNG_SAFESEARCH = 1

DUCKDUCKGO_BUFFER_ENABLED = True
DUCKDUCKGO_TIMEOUT = 8.0

TAVILY_ENABLED = True
TAVILY_API_KEY = "..."
TAVILY_TIMEOUT = 15.0
```

推荐默认策略：

```text
SearXNG:  主搜索，低成本
DuckDuckGo: 免费缓冲，best-effort
Tavily:   付费兜底，最后调用
```

---

# 18. 最终原则

## 应该做

```text
Agent 只传:
query, max_results, with_images

Tool 只依赖:
WebSearcher

搜索器链路负责:
缓存、熔断、fallback、供应商适配
```

## 不应该做

```text
WebSearchTool 直接 new TavilySearcher
Agent 直接传 topic / search_depth / include_images
SearXNG 挂了立刻无条件调用 Tavily
把 TavilySearchResult / SearXNGResult 泄露给上层
```

最终稳定边界是：

```text
WebSearchTool
    只负责解析参数、调用 searcher、格式化结果

WebSearcher
    统一搜索接口

Concrete Searcher
    负责具体引擎调用

Model Mapper
    负责供应商响应 → SearchResponse

Cached / Fallback / CircuitBreaker
    负责稳定性、成本控制、降级策略
```

这个方案能同时满足：

1. **多引擎兼容**
2. **SearXNG 自部署优先**
3. **Docker 异常时不直接烧 Tavily**
4. **DuckDuckGo 作为免费缓冲**
5. **Tavily 作为最后付费兜底**
6. **图片搜索能力可选兼容**
7. **Agent 侧接口长期稳定**

[1]: https://docs.tavily.com/documentation/api-reference/endpoint/search?utm_source=chatgpt.com "Tavily Search"
[2]: https://docs.searxng.org/dev/search_api.html?utm_source=chatgpt.com "Search API - SearXNG Documentation (2026.5.7+ef6290c8c)"
[3]: https://docs.searxng.org/admin/installation-docker.html?utm_source=chatgpt.com "Installation container"
[4]: https://www.postman.com/api-evangelist/duckduckgo/documentation/i9r819s/duckduckgo-instant-answer-api?utm_source=chatgpt.com "DuckDuckGo Instant Answer API | Documentation"
[5]: https://pypi.org/project/duckduckgo-search/?utm_source=chatgpt.com "duckduckgo-search"
[6]: https://cachetools.readthedocs.io/?utm_source=chatgpt.com "cachetools - Read the Docs"
