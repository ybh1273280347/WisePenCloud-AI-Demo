下面是**只根据这份日志**得出的修复文档。范围限定在日志直接暴露的问题，不引入新的架构调整。

---

# Web Search 日志驱动修复文档

## 0. 本次日志结论

日志显示：

1. 普通文本搜索下，`searxng` 正常成功返回结果。
   例如 `2024年诺贝尔物理学奖得主` 和 `Docker compose 网络配置最佳实践` 都由 `stage=searxng` 成功返回。

2. `with_images=True` 时，`searxng` 阶段失败，随后降级到 `duckduckgo`，并成功返回 `results=5 images=1`。

3. DuckDuckGo fallback 过程中有大量第三方内部请求日志，包括 Wikipedia 连接错误、Mojeek 403、DuckDuckGo i.js 403、Bing images 200 等，但最终 DuckDuckGo 阶段成功。

4. 一个明显测试用的异常 query：`xyznonexistentquery12345 qwertyuiop` 仍由 `searxng` 返回了 `results=5`。这不一定是系统错误，但说明当前“只要有 results 就算成功”的判定偏宽。

5. `mem0` warning 已确认是故意制造的测试隔离行为，本次不处理。

---

# 1. 必修：修复 SearXNG 图片搜索拖垮整个 stage 的问题

## 问题

日志中：

```text
联网搜索失败 | stage=searxng query=埃菲尔铁塔 max_results=5 with_images=True: SearXNG search request failed
```

随后：

```text
联网搜索成功 | stage=duckduckgo query=埃菲尔铁塔 results=5 images=1
```

说明 `with_images=True` 时，`searxng` 整个 stage 因某个子请求失败而失败。根据现象，最可能是图片请求失败后连带导致网页搜索结果也被丢弃。

## 修复原则

`with_images=True` 时：

```text
网页搜索是主路径
图片搜索是增强能力
图片搜索失败不能导致整个 SearXNG stage 失败
```

## 修改文件

```text
web_search/searcher/searxng_searcher.py
```

## 推荐修改

```python
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

    web_result, image_result = await asyncio.gather(
        self._search_web(query=query, max_results=max_results),
        self._search_images(query=query, max_results=max_results),
        return_exceptions=True,
    )

    # 网页搜索失败，才认为 SearXNG stage 失败
    if isinstance(web_result, Exception):
        raise web_result

    # 图片搜索失败，只返回网页搜索结果
    if isinstance(image_result, Exception):
        return web_result

    return _merge_search_responses(web_result, image_result)
```

## 验收标准

再次执行：

```python
await searcher.search(
    query="埃菲尔铁塔",
    max_results=5,
    with_images=True,
)
```

期望日志不再出现：

```text
stage=searxng ... with_images=True: SearXNG search request failed
```

即使图片分支失败，也应该返回：

```text
联网搜索成功 | stage=searxng query=埃菲尔铁塔 results=5 images=0
```

或：

```text
联网搜索成功 | stage=searxng query=埃菲尔铁塔 results=5 images>0
```

---

# 2. 必修：增强 SearXNG 错误日志

## 问题

当前日志只有：

```text
SearXNG search request failed
```

这个信息不够定位。无法判断失败的是：

```text
categories=general
```

还是：

```text
categories=images
```

也看不到 HTTP 状态码、请求参数、响应体。

## 修改文件

```text
web_search/searcher/searxng_searcher.py
```

## 推荐修改

在 `_get_json()` 中增强异常信息：

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
            "SearXNG search failed: "
            f"status={exc.response.status_code}, "
            f"url={exc.request.url}, "
            f"params={params}, "
            f"body={body}"
        ) from exc

    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"SearXNG search request failed: params={params}"
        ) from exc

    except ValueError as exc:
        raise RuntimeError(
            f"SearXNG response is not valid JSON: params={params}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            f"SearXNG response must be a JSON object: params={params}"
        )

    return data
```

## 验收标准

下一次失败日志必须能看出：

```text
params={'q': '埃菲尔铁塔', 'format': 'json', 'categories': 'images'}
```

或者：

```text
params={'q': '埃菲尔铁塔', 'format': 'json', 'categories': 'general'}
```

---

# 3. 必修：压制 DuckDuckGo / ddgs 内部 INFO 噪声

## 问题

DuckDuckGo fallback 最终成功，但中间刷了很多第三方内部请求日志：

```text
Error in engine wikipedia ...
response: https://www.mojeek.com/search ... 403
response: https://duckduckgo.com/i.js ... 403
response: https://www.bing.com/images/async ... 200
```

这些日志不是你的业务日志，会干扰判断主链路状态。最终真正有价值的是：

```text
联网搜索成功 | stage=duckduckgo query=埃菲尔铁塔 results=5 images=1
```

## 修改位置

应用启动日志配置处，例如：

```text
main.py
app startup
logging config
```

## 推荐修改

```python
import logging


def configure_third_party_loggers() -> None:
    logging.getLogger("ddgs").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
```

如果这些日志仍然从根 logger 透出，可以在你的 logger sink/filter 中过滤包含以下模式的 INFO：

```text
response: https://
Error in engine wikipedia
```

但不要过滤你自己的：

```text
联网搜索成功
联网搜索失败
```

## 验收标准

再次触发 DuckDuckGo fallback 时，日志应保留：

```text
联网搜索成功 | stage=duckduckgo ...
```

不再刷屏显示第三方内部 URL 请求过程。

---

# 4. 建议修复：SearXNG 结果质量最小过滤

## 问题

日志里：

```text
stage=searxng query=xyznonexistentquery12345 qwertyuiop results=5 images=0
```

这可能是搜索引擎返回了泛化结果，也可能是 mapper 没有过滤低质量结果。单凭日志不能断定结果一定错误，但至少说明当前成功条件过宽。

## 修改原则

不要引入 rerank，不做复杂评分。只做最低限度过滤：

```text
必须有 URL
title 和 snippet 不能同时为空
title + snippet 不能过短
```

## 修改位置

优先放在各 searcher 的 mapper 末尾。比如：

```text
web_search/searcher/searxng_searcher.py
web_search/searcher/duckduckgo_searcher.py
web_search/searcher/tavily_searcher.py
```

## 通用过滤函数

```python
def _is_valid_result(result: SearchResult) -> bool:
    title = result.title.strip()
    url = result.url.strip()
    snippet = result.snippet.strip()

    if not url:
        return False

    if not title and not snippet:
        return False

    if len(title) + len(snippet) < 12:
        return False

    return True
```

## SearXNG mapper 示例

```python
results = tuple(
    result
    for item in raw_results
    if isinstance(item, Mapping)
    for result in (_map_searxng_result(item),)
    if _is_valid_result(result)
)
```

## 验收标准

对异常 query：

```text
xyznonexistentquery12345 qwertyuiop
```

不强制要求一定返回 0 条，但至少应过滤掉空 URL、空标题、空摘要、明显无内容的结果。

---

# 5. 可选修复：DELETE session 404 日志降噪

## 问题

日志里多次出现：

```text
DELETE /chat/session/... 404
POST /chat/session/createSession 200
```

如果这是测试流程中“删除旧 session，再创建新 session”的正常行为，那么不是错误。

## 建议

如果希望日志更干净，可以把删除不存在 session 改成幂等成功：

```text
DELETE 不存在的 session → 204 No Content
```

或者只在测试环境降低这类 404 日志级别。

## 本次优先级

低。它不影响 web_search 链路。

---

# 6. 本次明确不处理

## mem0 warning

日志中的：

```text
长期记忆检索失败 | user=e2e-test-user ...
```

已确认是故意错传 ID，目的是防止测试污染数据库。本次不修。

## Tavily

这份日志中没有看到 Tavily 被触发。说明当前链路已经避免了无谓付费调用。本次不改 Tavily。

---

# 最终执行顺序

建议按这个顺序提交：

```text
1. 修改 SearXNG with_images 容错：图片失败不拖垮网页结果
2. 增强 SearXNG _get_json 错误日志
3. 压制 ddgs/httpx/httpcore 第三方 INFO 噪声
4. 增加 SearchResult 最小质量过滤
5. 可选：DELETE session 404 幂等化或降噪
```

最核心的修复是前两项。它们直接对应日志中最明确的问题：

```text
with_images=True 时 SearXNG stage 失败
失败日志缺少定位信息
```
