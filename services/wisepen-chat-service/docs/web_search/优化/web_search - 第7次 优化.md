可以。现在要测的不是“禁用 stage”，而是**真实进入异常分支**，验证日志里能看到：

```text
stage
query
max_results
with_images
elapsed_ms
异常类型
异常信息
failures 摘要
```

我建议加两类测试：

1. **确定性单元测试**：用 fake searcher 主动抛异常，稳定触发日志错误。
2. **真实集成测试**：用错误 SearXNG URL / 超短 DuckDuckGo timeout / 错误 Tavily key 触发真实失败。

---

# 1. 推荐先加确定性日志错误测试

这个最稳，不依赖网络状态，也不会消耗 Tavily。

## `tests/web_search/test_search_coordinator_failures.py`

```python
from __future__ import annotations

import pytest

from chat.application.web_search.coordinator import SearchCoordinator
from chat.application.web_search.infrastructure import SearchCache
from chat.application.web_search.models import SearchResponse, SearchResult


class RaisingSearcher:
    def __init__(self, name: str, message: str) -> None:
        self.name = name
        self._message = message

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        raise RuntimeError(self._message)


class EmptySearcher:
    def __init__(self, name: str) -> None:
        self.name = name

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        return SearchResponse(query=query)


class SuccessSearcher:
    def __init__(self, name: str) -> None:
        self.name = name

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        return SearchResponse(
            query=query,
            results=(
                SearchResult(
                    title="Example result",
                    url="https://example.com",
                    snippet="This is an example search result.",
                ),
            ),
        )


@pytest.mark.asyncio
async def test_searxng_exception_then_duckduckgo_success() -> None:
    cache = SearchCache(fresh_ttl=60, stale_ttl=3600, maxsize=64)

    coordinator = SearchCoordinator(
        cache=cache,
        searxng_searcher=RaisingSearcher(
            name="searxng",
            message="injected searxng request failure",
        ),
        duckduckgo_searcher=SuccessSearcher("duckduckgo"),
        tavily_searcher=SuccessSearcher("tavily"),
    )

    response = await coordinator.search(
        "Python asyncio gather",
        max_results=5,
        with_images=False,
    )

    assert response is not None
    assert response.source == "duckduckgo"
    assert len(response.results) == 1


@pytest.mark.asyncio
async def test_searxng_and_duckduckgo_exception_then_tavily_success() -> None:
    cache = SearchCache(fresh_ttl=60, stale_ttl=3600, maxsize=64)

    coordinator = SearchCoordinator(
        cache=cache,
        searxng_searcher=RaisingSearcher(
            name="searxng",
            message="injected searxng failure",
        ),
        duckduckgo_searcher=RaisingSearcher(
            name="duckduckgo",
            message="injected duckduckgo failure",
        ),
        tavily_searcher=SuccessSearcher("tavily"),
    )

    response = await coordinator.search(
        "unique-test-query-error-001",
        max_results=5,
        with_images=False,
    )

    assert response is not None
    assert response.source == "tavily"
    assert len(response.results) == 1


@pytest.mark.asyncio
async def test_empty_result_then_fallback_success() -> None:
    cache = SearchCache(fresh_ttl=60, stale_ttl=3600, maxsize=64)

    coordinator = SearchCoordinator(
        cache=cache,
        searxng_searcher=EmptySearcher("searxng"),
        duckduckgo_searcher=SuccessSearcher("duckduckgo"),
        tavily_searcher=SuccessSearcher("tavily"),
    )

    response = await coordinator.search(
        "empty result fallback test",
        max_results=5,
        with_images=False,
    )

    assert response is not None
    assert response.source == "duckduckgo"
    assert len(response.results) == 1


@pytest.mark.asyncio
async def test_all_searchers_raise_returns_none() -> None:
    cache = SearchCache(fresh_ttl=60, stale_ttl=3600, maxsize=64)

    coordinator = SearchCoordinator(
        cache=cache,
        searxng_searcher=RaisingSearcher(
            name="searxng",
            message="injected searxng failure",
        ),
        duckduckgo_searcher=RaisingSearcher(
            name="duckduckgo",
            message="injected duckduckgo failure",
        ),
        tavily_searcher=RaisingSearcher(
            name="tavily",
            message="injected tavily failure",
        ),
    )

    response = await coordinator.search(
        "all engines fail test",
        max_results=5,
        with_images=False,
    )

    assert response is None
```

这些测试会真实触发 `except Exception` 分支，日志里应该出现：

```text
联网搜索失败 | stage=searxng ... RuntimeError: injected searxng failure
联网搜索失败 | stage=duckduckgo ... RuntimeError: injected duckduckgo failure
联网搜索失败 | ... failures=[...]
```

---

# 2. 加 Stale Cache 错误路径测试

这个测试验证：

```text
SearXNG 抛异常
DuckDuckGo 抛异常
Stale Cache 命中
不调用 Tavily
```

```python
@pytest.mark.asyncio
async def test_errors_then_stale_cache_hit() -> None:
    cache = SearchCache(fresh_ttl=1, stale_ttl=3600, maxsize=64)

    query = "stale cache error fallback test"

    # 先写入缓存
    seed_coordinator = SearchCoordinator(
        cache=cache,
        searxng_searcher=SuccessSearcher("searxng"),
        duckduckgo_searcher=RaisingSearcher("duckduckgo", "should not be used"),
        tavily_searcher=RaisingSearcher("tavily", "should not be used"),
    )

    first = await seed_coordinator.search(query, max_results=5)
    assert first is not None
    assert first.source == "searxng"

    # 手动让 fresh 过期：更推荐测试环境 fresh_ttl=0.1
    import asyncio
    await asyncio.sleep(1.1)

    coordinator = SearchCoordinator(
        cache=cache,
        searxng_searcher=RaisingSearcher(
            "searxng",
            "injected searxng failure before stale cache",
        ),
        duckduckgo_searcher=RaisingSearcher(
            "duckduckgo",
            "injected duckduckgo failure before stale cache",
        ),
        tavily_searcher=RaisingSearcher(
            "tavily",
            "tavily should not be called if stale cache works",
        ),
    )

    response = await coordinator.search(query, max_results=5)

    assert response is not None
    assert response.source == "stale_cache"
    assert len(response.results) == 1
```

这个测试非常重要。它证明：

```text
Stale Cache 确实在 Tavily 前面保护成本。
```

---

# 3. 真实集成错误测试：SearXNG 连接失败

这个测试会产生真实网络错误日志，不是 fake 异常。

```python
import pytest

from chat.application.web_search.searcher.searxng_searcher import SearXNGSearcher


@pytest.mark.asyncio
async def test_real_searxng_connection_error_log() -> None:
    searcher = SearXNGSearcher(
        base_url="http://127.0.0.1:1",
        timeout=0.5,
    )

    with pytest.raises(RuntimeError) as exc_info:
        await searcher.search(
            "Python asyncio gather",
            max_results=5,
            with_images=False,
        )

    message = str(exc_info.value)

    assert "SearXNG" in message
    assert "params=" in message
    assert "Python asyncio gather" in message
```

期望异常类似：

```text
SearXNG request error: url=http://127.0.0.1:1/search, params={...}
```

---

# 4. 真实集成错误测试：SearXNG HTTP 404

如果你的本地没有这个路径，可以这样测：

```python
@pytest.mark.asyncio
async def test_real_searxng_http_error_log() -> None:
    searcher = SearXNGSearcher(
        base_url="http://localhost:8080/not-exist",
        timeout=2.0,
    )

    with pytest.raises(RuntimeError) as exc_info:
        await searcher.search(
            "Python asyncio gather",
            max_results=5,
            with_images=False,
        )

    message = str(exc_info.value)

    assert "SearXNG HTTP error" in message
    assert "status=" in message
    assert "params=" in message
    assert "body=" in message
```

这个测试验证 `_get_json()` 的 HTTP 错误上下文是否足够。

---

# 5. 真实集成错误测试：DuckDuckGo 超时

```python
import pytest

from chat.application.web_search.searcher.duckduckgo_searcher import DuckDuckGoSearcher


@pytest.mark.asyncio
async def test_real_duckduckgo_timeout_log() -> None:
    searcher = DuckDuckGoSearcher(
        timeout=0.001,
    )

    with pytest.raises(RuntimeError) as exc_info:
        await searcher.search(
            "Python asyncio gather",
            max_results=5,
            with_images=True,
        )

    message = str(exc_info.value)

    assert "DuckDuckGo" in message
    assert "timeout" in message.lower() or "failed" in message.lower()
    assert "Python asyncio gather" in message
```

这会稳定制造 DuckDuckGo 超时或失败日志。

---

# 6. 真实集成错误测试：Tavily 错误 API Key

这个测试不会消耗有效额度，但会打 Tavily API。建议标记成 integration。

```python
import pytest

from chat.application.web_search.searcher.tavily_searcher import TavilySearcher


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_tavily_invalid_api_key_log() -> None:
    searcher = TavilySearcher(
        api_key="invalid-test-key",
        timeout=3.0,
    )

    with pytest.raises(RuntimeError) as exc_info:
        await searcher.search(
            "Python asyncio gather",
            max_results=5,
            with_images=False,
        )

    message = str(exc_info.value)

    assert "Tavily search failed" in message
    assert "payload=" in message
    assert "Python asyncio gather" in message
```

如果不想 CI 默认跑它，使用：

```bash
pytest -m integration
```

---

# 7. 推荐的测试分组

## 日常测试跑这些

```text
test_searxng_exception_then_duckduckgo_success
test_searxng_and_duckduckgo_exception_then_tavily_success
test_empty_result_then_fallback_success
test_all_searchers_raise_returns_none
test_errors_then_stale_cache_hit
```

这些都是 fake 注入，稳定、快、不依赖网络、不花钱。

---

## 手动集成测试跑这些

```text
test_real_searxng_connection_error_log
test_real_searxng_http_error_log
test_real_duckduckgo_timeout_log
test_real_tavily_invalid_api_key_log
```

这些会真实触发网络错误和第三方错误，适合你现在验证日志增强是否真的有效。

---

# 8. 最终建议

你现在要测“日志错误”，不要只靠自然失败。加这两层：

```text
fake error tests      验证 coordinator 的降级和日志路径
real error tests      验证 searcher 的异常上下文是否足够
```

这样可以同时覆盖：

```text
stage 异常
空结果降级
全部失败
stale cache 命中
SearXNG 连接失败
SearXNG HTTP 错误
DuckDuckGo 超时
Tavily API 错误
```

其中最值得马上补的是：

```text
test_errors_then_stale_cache_hit
test_real_searxng_connection_error_log
test_real_duckduckgo_timeout_log
```

这三条能直接验证你现在最关心的：**降级链顺序、错误日志质量、付费兜底保护。**
