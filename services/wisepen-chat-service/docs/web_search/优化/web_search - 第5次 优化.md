对，当前失败日志确实不够。日志里最明显的问题是：链路知道“失败了”，但不知道**为什么失败、失败在哪个子请求、返回了什么、过滤前后发生了什么**。

例如这段：

```text
stage=duckduckgo ... DuckDuckGo buffer search failed
stage=tavily ... Tavily search request failed
```

只能知道两个 stage 失败，但看不到异常类型、根因、请求参数、HTTP 状态码、响应体片段。

还有：

```text
stage=searxng query=What is the capital of France?: 搜索结果为空，触发降级
```

这条也太粗，只知道“空”，不知道是 **SearXNG 原始结果为空**，还是 **mapper 过滤后为空**，还是 **结果字段不合格被过滤掉了**。

---

# 修复目标

不要引入 `errors.py`。
只做三件事：

```text
1. searcher 抛异常时带上下文
2. coordinator 记录异常类型、根因、耗时
3. 空结果时记录 response 摘要和过滤信息
```

---

# 1. Coordinator：失败日志增强

现在大概率是这种：

```python
except Exception as exc:
    log_fail("联网搜索", exc, stage=stage.name, query=query)
```

改成：

```python
import time
from typing import Optional


def _format_exception(exc: Exception) -> str:
    root = _get_root_cause(exc)

    if root is exc:
        return f"{type(exc).__name__}: {exc}"

    return (
        f"{type(exc).__name__}: {exc} | "
        f"root={type(root).__name__}: {root}"
    )


def _get_root_cause(exc: Exception) -> Exception:
    current = exc

    while True:
        next_exc = current.__cause__ or current.__context__
        if next_exc is None:
            return current
        current = next_exc
```

然后 stage 执行处改成：

```python
for stage in self._chain:
    started_at = time.perf_counter()

    try:
        response = await stage.handler(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)

        log_fail(
            "联网搜索",
            _format_exception(exc),
            stage=stage.name,
            query=query,
            max_results=max_results,
            with_images=with_images,
            elapsed_ms=elapsed_ms,
        )
        continue
```

这样日志会从：

```text
DuckDuckGo buffer search failed
```

变成类似：

```text
联网搜索失败 | stage=duckduckgo query=... elapsed_ms=7341:
RuntimeError: DuckDuckGo buffer search failed | root=TimeoutError: ...
```

---

# 2. 空结果日志增强

当前日志：

```text
搜索结果为空，触发降级
```

不够。改成：

```python
if not _has_content(response):
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)

    log_fail(
        "联网搜索",
        "搜索结果为空，触发降级",
        stage=stage.name,
        query=query,
        max_results=max_results,
        with_images=with_images,
        elapsed_ms=elapsed_ms,
        results=len(response.results),
        images=len(response.images),
        has_answer=bool(response.answer),
        source=response.source,
    )

    if self._continue_on_empty:
        continue

    return _with_source(response, stage.name)
```

但这还不够，因为 `len(response.results)=0` 只说明 mapper 后为空。更好的是让 searcher 在 mapper 阶段记录：

```text
raw_results=5 mapped_results=0 filtered_results=5
```

这个在 SearXNG 里尤其重要，因为日志中 `What is the capital of France?` 被判定为空，但不清楚是 SearXNG 没返回，还是过滤太狠。

---

# 3. SearXNG：HTTP 错误带完整上下文

`SearXNG search request failed` 太少。改 `_get_json()`：

```python
async def _get_json(self, params: dict[str, Any]) -> dict[str, Any]:
    url = f"{self._base_url}/search"

    try:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:500]
        raise RuntimeError(
            "SearXNG HTTP error: "
            f"status={exc.response.status_code}, "
            f"url={exc.request.url}, "
            f"params={params}, "
            f"body={body!r}"
        ) from exc

    except httpx.TimeoutException as exc:
        raise RuntimeError(
            "SearXNG timeout: "
            f"url={url}, params={params}, timeout={self._timeout}"
        ) from exc

    except httpx.RequestError as exc:
        raise RuntimeError(
            "SearXNG request error: "
            f"url={url}, params={params}"
        ) from exc

    except ValueError as exc:
        raw = response.text[:500] if "response" in locals() else ""
        raise RuntimeError(
            "SearXNG invalid JSON: "
            f"url={url}, params={params}, body={raw!r}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            "SearXNG invalid response type: "
            f"type={type(data).__name__}, params={params}"
        )

    return data
```

这样下一次失败时能看出：

```text
categories=general
```

还是：

```text
categories=images
```

出了问题。

---

# 4. SearXNG：mapper 记录原始数量、过滤数量

在 `_map_searxng_response()` 里加统计，建议临时用日志，定位稳定后可以降级到 debug：

```python
def _map_searxng_response(
    data: Mapping[str, Any],
    *,
    query: str,
    max_results: int,
    images_only: bool,
) -> SearchResponse:
    raw_results = data.get("results") or ()

    if not isinstance(raw_results, Sequence) or isinstance(raw_results, str):
        raw_results = ()

    raw_count = len(raw_results)

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

    mapped_results: list[SearchResult] = []
    invalid_count = 0

    for item in raw_results:
        if not isinstance(item, Mapping):
            invalid_count += 1
            continue

        result = _map_searxng_result(item)
        if not _is_valid_result(result):
            invalid_count += 1
            continue

        mapped_results.append(result)

    if raw_count > 0 and not mapped_results:
        # 用你自己的 log_fail/log_debug 均可
        log_fail(
            "SearXNG结果过滤",
            "原始结果全部被过滤",
            query=query,
            raw_count=raw_count,
            invalid_count=invalid_count,
            sample=_sample_raw_results(raw_results),
        )

    return SearchResponse(
        query=query,
        results=tuple(mapped_results[:max_results]),
        answer=_to_optional_str(data.get("answer")),
    )
```

辅助函数：

```python
def _sample_raw_results(raw_results: Sequence[Any], limit: int = 2) -> list[dict[str, str]]:
    samples: list[dict[str, str]] = []

    for item in raw_results[:limit]:
        if not isinstance(item, Mapping):
            continue

        samples.append(
            {
                "title": str(item.get("title") or "")[:120],
                "url": str(item.get("url") or "")[:200],
                "content": str(item.get("content") or "")[:200],
                "engine": str(item.get("engine") or ""),
                "category": str(item.get("category") or ""),
            }
        )

    return samples
```

这能直接解释“为什么 SearXNG 搜索 France 首都为空”。

---

# 5. DuckDuckGo：不要吞根因

现在日志只显示：

```text
DuckDuckGo buffer search failed
```

但前面第三方内部日志显示它实际访问了 `grokipedia`、`yahoo` 等源，最后还是失败了。

改成：

```python
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

    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            "DuckDuckGo timeout: "
            f"query={query!r}, max_results={max_results}, "
            f"with_images={with_images}, timeout={self._timeout}"
        ) from exc

    except Exception as exc:
        raise RuntimeError(
            "DuckDuckGo buffer search failed: "
            f"query={query!r}, max_results={max_results}, "
            f"with_images={with_images}, error={type(exc).__name__}: {exc}"
        ) from exc
```

---

# 6. Tavily：也不要只说 request failed

当前 Tavily 失败日志是：

```text
Tavily search request failed
```

看不到是 API key、网络、超时、429、还是 SDK 返回异常。

改成：

```python
async def search(
    self,
    query: str,
    *,
    max_results: int = 5,
    with_images: bool = False,
) -> SearchResponse:
    payload: dict[str, Any] = {
        "query": query,
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": False,
        "search_depth": "basic",
        "timeout": self._timeout,
    }

    if with_images:
        payload["include_images"] = True

    safe_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"api_key"}
    }

    try:
        raw_response = await self._client.search(**payload)

    except Exception as exc:
        raise RuntimeError(
            "Tavily search failed: "
            f"payload={safe_payload}, "
            f"error={type(exc).__name__}: {exc}"
        ) from exc

    return _map_tavily_response(raw_response)
```

如果 Tavily SDK 异常里有 status code，后面可以再专门解析；第一版先把异常类型和 payload 打出来就够。

---

# 7. Coordinator：最终失败时输出失败摘要

现在如果所有 stage 都失败，日志会分散在各 stage。建议 coordinator 收集失败摘要：

```python
failures: list[str] = []

for stage in self._chain:
    started_at = time.perf_counter()

    try:
        response = await stage.handler(...)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        failure = f"{stage.name}({elapsed_ms}ms): {_format_exception(exc)}"
        failures.append(failure)

        log_fail(
            "联网搜索",
            failure,
            stage=stage.name,
            query=query,
            max_results=max_results,
            with_images=with_images,
        )
        continue

# chain exhausted
log_fail(
    "联网搜索",
    "所有搜索阶段均失败",
    query=query,
    max_results=max_results,
    with_images=with_images,
    failures=failures,
)
```

这样最终能看到：

```text
failures=[
  "searxng(5600ms): empty result",
  "duckduckgo(7998ms): TimeoutError...",
  "tavily(1320ms): AuthenticationError..."
]
```

---

# 8. 还要补一个成功日志字段：elapsed_ms

现在成功日志是：

```text
联网搜索成功 | stage=searxng query=... results=5 images=0
```

建议补：

```python
elapsed_ms=elapsed_ms
```

成功和失败都带耗时，才能判断是不是超时边缘。

```python
log_ok(
    "联网搜索成功",
    stage=stage.name,
    query=query,
    max_results=max_results,
    with_images=with_images,
    elapsed_ms=elapsed_ms,
    results=len(response.results),
    images=len(response.images),
)
```

---

# 9. 建议日志格式目标

最终失败日志应该长这样：

```text
联网搜索失败 |
stage=searxng
query="What is the capital of France?"
max_results=5
with_images=False
elapsed_ms=5521
reason="empty_result"
raw_results=3
valid_results=0
sample=[{"title": "...", "url": "...", "content": "..."}]
```

DuckDuckGo：

```text
联网搜索失败 |
stage=duckduckgo
query="What is the capital of France?"
elapsed_ms=7998
error="RuntimeError: DuckDuckGo timeout..."
root="TimeoutError"
```

Tavily：

```text
联网搜索失败 |
stage=tavily
query="What is the capital of France?"
elapsed_ms=1221
error="RuntimeError: Tavily search failed..."
root="AuthenticationError: invalid api key"
```

这样才够排障。

---

# 最小执行清单

按优先级改：

```text
1. coordinator 记录 exc 类型、root cause、elapsed_ms
2. coordinator 最终失败时聚合 failures 摘要
3. searxng _get_json 异常带 params/status/body
4. searxng mapper 记录 raw_count / valid_count / sample
5. duckduckgo search 失败时保留原始异常类型和上下文
6. tavily search 失败时保留 payload 和原始异常类型
7. 成功日志补 elapsed_ms
```

这几项不改变架构，只增强可观测性。当前日志已经能看到链路走向，但排障信息不足，尤其是 `DuckDuckGo buffer search failed` 和 `Tavily search request failed` 这种消息基本没有诊断价值。
