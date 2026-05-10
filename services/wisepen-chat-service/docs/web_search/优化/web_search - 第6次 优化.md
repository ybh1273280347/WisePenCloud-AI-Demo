对，现在应该做一次**日志增强后的回收清理**。
增强日志阶段会自然引入很多“诊断函数”，但链路稳定后，不能让这些临时排障工具长期污染核心代码。

我建议按这个原则清理：

```text
保留能长期提高可观测性的函数
删除只为排一次故障服务的函数
合并只被调用一次、语义不强的函数
```

---

# 一、建议保留的函数

这些属于长期稳定能力，建议保留。

## 1. `_has_content`

```python
def _has_content(response: SearchResponse) -> bool:
    return bool(response.answer or response.results or response.images)
```

保留。
这是调度器的核心判定逻辑，不是日志辅助。

---

## 2. `_with_source`

```python
def _with_source(response: SearchResponse, source: str) -> SearchResponse:
    return SearchResponse(
        query=response.query,
        results=response.results,
        answer=response.answer,
        images=response.images,
        source=source,
    )
```

保留。
`source` 对观察 Tavily 是否被触发、是否命中 stale cache 很有价值。

---

## 3. `make_search_cache_key`

```python
def make_search_cache_key(
    *,
    query: str,
    max_results: int,
    with_images: bool,
) -> SearchCacheKey:
    normalized_query = " ".join(query.strip().lower().split())
    return normalized_query, max_results, with_images
```

保留。
这是缓存语义的一部分，不是日志函数。

---

# 二、建议删除或合并的函数

## 1. `_format_exception`

如果现在所有 searcher 抛出的异常已经带了上下文，比如：

```text
SearXNG timeout: params=...
DuckDuckGo timeout: query=...
Tavily search failed: payload=...
```

那么 coordinator 里不需要再复杂展开 root cause。

可以从：

```python
log_fail(
    "联网搜索",
    _format_exception(exc),
    ...
)
```

改回：

```python
log_fail(
    "联网搜索",
    exc,
    ...
)
```

删除：

```python
_format_exception()
_get_root_cause()
```

理由：
现在异常源头已经足够清楚，coordinator 不需要再做异常分析器。

---

## 2. `_sample_raw_results`

这个函数适合排查 mapper 过滤问题，但不适合长期保留在主路径里。

```python
def _sample_raw_results(...):
    ...
```

建议删除，或者只在 debug 开关下保留。

如果你没有专门的 debug flag，直接删掉。

原因：
它会把搜索结果内容塞进日志，长期看容易造成日志膨胀，而且可能包含无关页面内容。

---

## 3. mapper 里的 `raw_count / invalid_count / sample` 日志

增强期有用：

```python
log_fail(
    "SearXNG结果过滤",
    "原始结果全部被过滤",
    raw_count=raw_count,
    invalid_count=invalid_count,
    sample=...
)
```

现在稳定后建议改成更轻的版本，甚至删除。

保守保留版：

```python
if raw_count > 0 and not mapped_results:
    log_fail(
        "SearXNG结果过滤",
        "原始结果全部被过滤",
        query=query,
        raw_count=raw_count,
    )
```

删除：

```python
invalid_count
sample
_sample_raw_results
```

这样足够提示“过滤过严”，但不会污染日志。

---

## 4. `failures` 摘要如果只在全失败时用，可以保留，但简化

你现在可能有：

```python
failures.append(
    f"{stage.name}({elapsed_ms}ms): {_format_exception(exc)}"
)
```

建议改成：

```python
failures.append(
    f"{stage.name}({elapsed_ms}ms): {type(exc).__name__}: {exc}"
)
```

这样不再依赖 `_format_exception()`。

保留 `failures` 是有价值的，因为测试 3 已经证明它很好用：

```text
failures=[
  'searxng(0ms): disabled_by_test',
  'duckduckgo(0ms): disabled_by_test',
  ...
]
```

---

# 三、Coordinator 推荐清理版

这是清理后的 `search()` 核心逻辑。

```python
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
        log_ok(
            "联网搜索成功",
            stage="fresh_cache",
            query=query,
            max_results=max_results,
            with_images=with_images,
            results=len(fresh.results),
            images=len(fresh.images),
        )
        return _with_source(fresh, "fresh_cache")

    failures: list[str] = []
    last_empty: Optional[SearchResponse] = None

    for stage in self._chain:
        started_at = time.perf_counter()

        if stage.name in self._disabled_stages:
            failures.append(f"{stage.name}(0ms): disabled_by_test")

            log_fail(
                "联网搜索跳过",
                "测试注入：stage 被禁用，触发降级",
                stage=stage.name,
                query=query,
                max_results=max_results,
                with_images=with_images,
            )
            continue

        try:
            response = await stage.handler(
                query=query,
                max_results=max_results,
                with_images=with_images,
            )

        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            failures.append(
                f"{stage.name}({elapsed_ms}ms): {type(exc).__name__}: {exc}"
            )

            log_fail(
                "联网搜索失败",
                exc,
                stage=stage.name,
                query=query,
                max_results=max_results,
                with_images=with_images,
                elapsed_ms=elapsed_ms,
            )
            continue

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)

        if response is None:
            failures.append(f"{stage.name}({elapsed_ms}ms): returned_none")
            continue

        if not _has_content(response):
            last_empty = response
            failures.append(f"{stage.name}({elapsed_ms}ms): empty_result")

            log_fail(
                "联网搜索失败",
                "搜索结果为空，触发降级",
                stage=stage.name,
                query=query,
                max_results=max_results,
                with_images=with_images,
                elapsed_ms=elapsed_ms,
                results=len(response.results),
                images=len(response.images),
                has_answer=bool(response.answer),
            )

            if self._continue_on_empty:
                continue

            return _with_source(response, stage.name)

        response = _with_source(response, stage.name)

        if stage.cacheable:
            await self._cache.set(key, response)

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

        return response

    log_fail(
        "联网搜索失败",
        "所有搜索阶段均失败",
        query=query,
        max_results=max_results,
        with_images=with_images,
        failures=failures,
    )

    return last_empty
```

这个版本保留了有用日志：

```text
stage
query
max_results
with_images
elapsed_ms
results
images
failures
```

删除了过度诊断：

```text
root cause 展开函数
raw result sample
复杂 mapper 统计
```

---

# 四、SearXNG `_get_json()` 保留增强，不要删

这个建议保留，因为它是 searcher 边界处最有价值的信息：

```python
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
```

这类上下文应该放在 **searcher 内部**，而不是 coordinator 里解析。

---

# 五、DuckDuckGo / Tavily 异常上下文也保留

保留这种：

```python
except asyncio.TimeoutError as exc:
    raise RuntimeError(
        "DuckDuckGo timeout: "
        f"query={query!r}, max_results={max_results}, "
        f"with_images={with_images}, timeout={self._timeout}"
    ) from exc
```

和：

```python
except Exception as exc:
    raise RuntimeError(
        "Tavily search failed: "
        f"payload={safe_payload}, "
        f"error={type(exc).__name__}: {exc}"
    ) from exc
```

这些是边界错误，不算临时日志函数。

---

# 六、最终清理清单

## 删除

```text
_format_exception()
_get_root_cause()
_sample_raw_results()
mapper 中 sample 日志
mapper 中过细 invalid_count 日志
```

## 保留

```text
_has_content()
_with_source()
make_search_cache_key()
failures 摘要
elapsed_ms
searcher 内部 HTTP / timeout / payload 上下文
```

## 简化

```text
“联网搜索失败”日志：
只保留 stage/query/max_results/with_images/elapsed_ms/error

“空结果”日志：
只保留 results/images/has_answer，不再记录 raw sample

“全部失败”日志：
保留 failures 列表
```

---

# 七、最终原则

清理后日志应该达到这个状态：

```text
日常成功日志简洁
失败日志能定位 stage 和基本根因
深度原始数据不常驻日志
searcher 负责带上下文抛异常
coordinator 只负责记录 stage 级别结果
```

这就是稳定后的合理状态：
**可观测，但不过度诊断；能排障，但不污染主流程。**
