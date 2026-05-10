下面是**最终强化文档**。这版把前面所有评审意见收敛到一份可执行规范里：

```text
架构冻结
不再引入新 coordinator / pipeline / reranker
不使用 __all__
内部 helper 使用下划线
不引入第三方库
只做效果增强、用户体验增强、证据质量增强
```

当前 container 已经把 `web_search_coordinator` 做成 Singleton，同时完整注入了 `ContentProcessor`、`StaticFetcher`、`SteelFetcher`、`LocalScriptFetcher` 和 `FetchCoordinator`，并且 `WebSearchTool` / `WebFetchTool` 已复用同一个 `fetch_coordinator`，所以本次文档不再要求改 container 结构，只要求保持现状。

---

# Web Search 效果强化最终执行文档

## 1. 最终目标

在不改架构的前提下，把 `web_search` 从：

```text
多 query 搜索 + 合并结果
```

增强为：

```text
多 query 搜索 + 干净去重 + 可追溯证据包 + 可控 deep search + 更好的模型调用行为
```

本次强化只做这些：

```text
1. URL 规范化去重
2. fetch_top_pages 绑定 result index
3. site: operator 自动关闭 domain dedupe
4. query 长度保护
5. notes 机制
6. Tool description 强化 query 生成模板
7. fetch 单页 timeout
8. include_domains / exclude_domains 后处理
9. time_range + year 自动补全，仅 month/year 生效
```

不做：

```text
1. 不做 snippet 相似度去重
2. 不做 interleaving
3. 不做 search_many 全局 timeout
4. 不做 source quality 主观打分
5. 不接 embedding / reranker / LangChain / LlamaIndex
6. 不新增搜索引擎
7. 不新增架构层
```

理由：URL 规范化去重、result index 绑定、`site:` 自动关闭 domain dedupe、query 长度保护、Evidence Pack notes 和 Tool description 模板都被评审认为是低风险高收益增强；相反，snippet 相似度去重和 interleaving 会扩大内部数据流复杂度，不符合“架构冻结”。

---

# 2. 外部依据

Tavily 文档明确指出 `include_answer`、`include_raw_content`、`max_results` 会影响响应大小，需要手动设置；`search_depth=advanced` 会消耗更多 credits，所以多 query 场景必须控制付费兜底调用次数。([Tavily Docs][1])

SearXNG 官方 Search API 要求消费 JSON 结果时传 `format=json`，且服务端 `settings.yml` 必须启用对应格式；未启用时请求该格式会返回 403。([SearXNG 文档][2])

Python 官方 `asyncio` 文档支持用同步原语进行协程协调；`asyncio.gather()` 在异常传播行为上需要谨慎处理，使用 `return_exceptions=True` 可以避免单个任务异常直接中断整体收集。([Python documentation][3])

DDGS 项目提供 `DDGS().text()`、`images()` 等能力，适合作为免费缓冲搜索源。([GitHub][4])

---

# 3. 代码风格约束

全局遵循：

```text
1. 不使用 __all__
2. 模块内部 helper 使用下划线前缀
3. 类内部属性 / 方法使用下划线前缀
4. 保留 typing.Dict / List / Tuple / Optional / Set 风格
5. 不为少量 helper 拆新架构层
6. 不引入第三方库
```

例子：

```python
def _normalize_url_for_dedup(url: str) -> str:
    ...


def _has_site_operator(queries: List[str]) -> bool:
    ...


class WebSearchTool(BaseTool):
    async def _fetch_top_pages(...):
        ...
```

---

# 4. notes 机制

## 4.1 为什么叫 notes

不用 `warnings`，改名为：

```python
notes: List[str]
```

语义是“给模型看的执行说明”，不是错误日志。

notes 只记录会影响模型理解搜索结果的信息：

```text
保留：
- Query truncated to 400 characters.
- 3 duplicate URLs were removed.
- Domain dedupe disabled because a site: operator was detected.
- Tavily paid fallback was used once.
- Fetched page for result #2 was skipped because it returned non-text content.
- include_domains filter reduced results from 12 to 2.

不放：
- SearXNG 403
- DuckDuckGo timeout
- 某 query TimeoutError
- HTTP body
- stack trace
```

评审里也明确区分了“给模型看的 warning”和“给开发者看的 log”，只有前者应该进入证据包。

---

## 4.2 notes 传递方式

统一采用：

```python
notes: List[str]
```

作为可变列表向下传递，不修改 `SearchResponse`，不让 helper 返回 tuple。

最终链路：

```text
WebSearchTool.execute()
    notes = []

    _get_queries(..., notes=notes)

    coordinator.search_many(..., notes=notes)

        _normalize_queries(..., notes=notes)

        _merge_many_search_responses(..., notes=notes)

        Tavily fallback used
            notes.append(...)

    _fetch_top_pages(..., notes=notes)

    _format_response(..., notes=notes)
```

---

# 5. SearchCoordinator 强化

文件：

```text
chat/application/web_search/search_coordinator.py
```

## 5.1 新增 import

```python
import asyncio
from typing import Awaitable, Callable, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse
```

如果实现 `site:` 检测和 `time_range`：

```python
import re
from datetime import date
```

模型 import：

```python
from chat.application.web_search.models import (
    ImageResult,
    SearchResponse,
    SearchResult,
)
```

保留：

```python
from chat.application.web_search.utils import deduplicate_results_by_domain
```

删除：

```python
__all__ = [...]
```

---

## 5.2 常量

```python
MAX_BROAD_SEARCH_QUERIES = 4
MAX_BROAD_SEARCH_CONCURRENCY = 3

MAX_QUERY_CHARS = 400

MAX_RESULTS_PER_QUERY = 10
DEFAULT_FINAL_RESULTS = 12
MAX_FINAL_RESULTS = 20

PAID_FALLBACK_MIN_RESULTS = 3
PAID_FALLBACK_LIMIT = 1

DEFAULT_DEDUPE_DOMAINS = True
DEFAULT_MAX_PER_DOMAIN = 2
MAX_PER_DOMAIN = 5

_TIME_RANGE_VALUES = {"day", "week", "month", "year"}

_TRACKING_PARAMS = frozenset({
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "msclkid",
})

_SITE_OPERATOR_PATTERN = re.compile(r"\bsite:", re.IGNORECASE)
_YEAR_PATTERN = re.compile(r"\b(19|20)\d{2}\b")
_TIME_WORD_PATTERN = re.compile(
    r"\b(today|yesterday|week|month|year|latest|recent|current|202\d|19\d{2}|20\d{2})\b",
    re.IGNORECASE,
)
```

注意：不要把整个 URL lower。只能小写 scheme 和 host，path 保持原样，因为部分服务器路径大小写敏感。评审明确指出原始方案对整个 URL `.lower()` 会误伤 GitHub 用户名路径、S3 object key 等场景，应直接修正。

---

## 5.3 `search()` 增加 `allow_paid_fallback`

签名：

```python
async def search(
    self,
    query: str,
    *,
    max_results: int = 5,
    with_images: bool = False,
    freshness_required: bool = False,
    allow_paid_fallback: bool = True,
) -> Optional[SearchResponse]:
```

stage loop 中执行 Tavily 前加入：

```python
if stage.name == "tavily" and not allow_paid_fallback:
    failures.append("tavily: skipped_for_paid_fallback_disabled")

    log_fail(
        "联网搜索跳过",
        "allow_paid_fallback=False，跳过 Tavily",
        stage=stage.name,
        query=query,
        max_results=max_results,
        with_images=with_images,
    )
    continue
```

语义：

```text
单 query:
    allow_paid_fallback=True

多 query:
    每个 query 内部 search(..., allow_paid_fallback=False)

多 query 付费兜底:
    search_many 内部最多直接调用 _search_tavily 一次
```

---

## 5.4 `search_many()` 最终签名

```python
async def search_many(
    self,
    queries: List[str],
    *,
    max_results_per_query: int = 5,
    final_max_results: int = DEFAULT_FINAL_RESULTS,
    with_images: bool = False,
    freshness_required: bool = False,
    allow_paid_fallback: bool = False,
    concurrency: int = MAX_BROAD_SEARCH_CONCURRENCY,
    dedupe_domains: bool = DEFAULT_DEDUPE_DOMAINS,
    max_per_domain: int = DEFAULT_MAX_PER_DOMAIN,
    time_range: Optional[str] = None,
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
    notes: Optional[List[str]] = None,
) -> SearchResponse:
```

---

## 5.5 `search_many()` 核心实现

```python
async def search_many(
    self,
    queries: List[str],
    *,
    max_results_per_query: int = 5,
    final_max_results: int = DEFAULT_FINAL_RESULTS,
    with_images: bool = False,
    freshness_required: bool = False,
    allow_paid_fallback: bool = False,
    concurrency: int = MAX_BROAD_SEARCH_CONCURRENCY,
    dedupe_domains: bool = DEFAULT_DEDUPE_DOMAINS,
    max_per_domain: int = DEFAULT_MAX_PER_DOMAIN,
    time_range: Optional[str] = None,
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
    notes: Optional[List[str]] = None,
) -> SearchResponse:
    notes = notes if notes is not None else []

    normalized_time_range = _normalize_time_range(time_range)

    normalized_queries = _normalize_queries(
        queries,
        limit=MAX_BROAD_SEARCH_QUERIES,
        time_range=normalized_time_range,
        notes=notes,
    )

    if not normalized_queries:
        return SearchResponse(query="", results=(), images=(), source="multi")

    max_results_per_query = _normalize_int(
        max_results_per_query,
        default=5,
        minimum=1,
        maximum=MAX_RESULTS_PER_QUERY,
    )
    final_max_results = _normalize_int(
        final_max_results,
        default=DEFAULT_FINAL_RESULTS,
        minimum=1,
        maximum=MAX_FINAL_RESULTS,
    )
    concurrency = _normalize_int(
        concurrency,
        default=MAX_BROAD_SEARCH_CONCURRENCY,
        minimum=1,
        maximum=MAX_BROAD_SEARCH_CONCURRENCY,
    )
    max_per_domain = _normalize_int(
        max_per_domain,
        default=DEFAULT_MAX_PER_DOMAIN,
        minimum=1,
        maximum=MAX_PER_DOMAIN,
    )

    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(query: str) -> Optional[SearchResponse]:
        async with semaphore:
            return await self.search(
                query=query,
                max_results=max_results_per_query,
                with_images=with_images,
                freshness_required=freshness_required,
                allow_paid_fallback=False,
            )

    tasks = [run_one(query) for query in normalized_queries]
    raw_responses = await asyncio.gather(*tasks, return_exceptions=True)

    responses: List[SearchResponse] = []
    failures: List[str] = []

    for query, result in zip(normalized_queries, raw_responses):
        if isinstance(result, Exception):
            failures.append(f"{query}: {type(result).__name__}: {result}")
            continue

        if result is None:
            failures.append(f"{query}: returned_none")
            continue

        if not _has_content(result):
            failures.append(f"{query}: empty_result")
            continue

        responses.append(result)

    merged = _merge_many_search_responses(
        query=" | ".join(normalized_queries),
        responses=responses,
        final_max_results=final_max_results,
        dedupe_domains=dedupe_domains,
        max_per_domain=max_per_domain,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        notes=notes,
    )

    tavily_used = False

    if (
        allow_paid_fallback
        and len(merged.results) < PAID_FALLBACK_MIN_RESULTS
        and normalized_queries
    ):
        try:
            paid_response = await self._search_tavily(
                query=normalized_queries[0],
                max_results=max_results_per_query,
                with_images=with_images,
            )
            tavily_used = True
        except Exception as exc:
            failures.append(f"tavily_paid_once: {type(exc).__name__}: {exc}")
            paid_response = None

        if paid_response is not None and _has_content(paid_response):
            paid_response = _with_source(paid_response, "tavily")
            notes.append("Tavily paid fallback was used once.")

            merged = _merge_many_search_responses(
                query=" | ".join(normalized_queries),
                responses=[merged, paid_response],
                final_max_results=final_max_results,
                dedupe_domains=dedupe_domains,
                max_per_domain=max_per_domain,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                notes=notes,
            )

    log_ok(
        "联网广搜",
        queries=len(normalized_queries),
        results=len(merged.results),
        images=len(merged.images),
        allow_paid_fallback=allow_paid_fallback,
        tavily_used=tavily_used,
        paid_fallback_min_results=PAID_FALLBACK_MIN_RESULTS,
        failures=failures,
    )

    return merged
```

关键点：

```text
1. Tavily paid fallback 只参与当次合并
2. 不写入普通 query cache
3. 避免缓存污染
```

---

# 6. SearchCoordinator helper

## 6.1 `_normalize_int`

```python
def _normalize_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default

    return max(minimum, min(number, maximum))
```

---

## 6.2 `_normalize_time_range`

```python
def _normalize_time_range(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    value = value.strip().lower()
    if value not in _TIME_RANGE_VALUES:
        return None

    return value
```

---

## 6.3 `_normalize_queries`

```python
def _normalize_queries(
    queries: List[str],
    *,
    limit: int,
    time_range: Optional[str],
    notes: List[str],
) -> List[str]:
    normalized: List[str] = []
    seen: Set[str] = set()
    skipped_duplicates = 0
    valid_input_count = 0

    for query in queries:
        if not isinstance(query, str):
            continue

        value = " ".join(query.strip().split())
        if not value:
            continue

        valid_input_count += 1

        value = _truncate_query(value, notes)
        value = _maybe_append_year(value, time_range)

        key = value.lower()
        if key in seen:
            skipped_duplicates += 1
            continue

        seen.add(key)
        normalized.append(value)

        if len(normalized) >= limit:
            break

    if skipped_duplicates:
        notes.append(
            f"{skipped_duplicates} duplicate search queries were removed."
        )

    if valid_input_count > limit:
        notes.append(
            f"Search queries were limited to {limit} focused queries."
        )

    return normalized
```

---

## 6.4 `_truncate_query`

```python
def _truncate_query(query: str, notes: List[str]) -> str:
    if len(query) <= MAX_QUERY_CHARS:
        return query

    truncated = query[:MAX_QUERY_CHARS].rsplit(" ", 1)[0].strip()
    if not truncated:
        truncated = query[:MAX_QUERY_CHARS].strip()

    notes.append(f"Query truncated to {MAX_QUERY_CHARS} characters.")

    return truncated
```

---

## 6.5 `_maybe_append_year`

只对 `month` / `year` 生效，不对 `day` / `week` 生效，避免干扰实时查询。评审也认为这个限制更合理：`day` 级别应该依赖实时索引，不应该强行补年份。

```python
def _maybe_append_year(query: str, time_range: Optional[str]) -> str:
    if time_range not in {"month", "year"}:
        return query

    if _YEAR_PATTERN.search(query):
        return query

    if _TIME_WORD_PATTERN.search(query):
        return query

    return f"{query} {date.today().year}"
```

---

## 6.6 `_normalize_url_for_dedup`

```python
def _normalize_url_for_dedup(url: str) -> str:
    raw_url = url.strip()

    try:
        parsed = urlparse(raw_url)
    except Exception:
        return raw_url

    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().removeprefix("www.")

    if not scheme or not host:
        return raw_url

    port = ""
    if parsed.port and not (
        (scheme == "http" and parsed.port == 80)
        or (scheme == "https" and parsed.port == 443)
    ):
        port = f":{parsed.port}"

    params = sorted(
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
    )

    path = parsed.path.rstrip("/") or "/"
    query = f"?{urlencode(params)}" if params else ""

    return f"{scheme}://{host}{port}{path}{query}"
```

重点：

```text
1. scheme 小写
2. host 小写
3. path 保持原大小写
4. 去掉默认端口
5. 去掉 fragment
6. 去掉 tracking 参数
7. 剩余 query 参数排序
8. keep_blank_values=True
```

URL 规范化去重被评审认为是整个搜索链路里收益最明确的改动，尤其适合多 query 合并场景。

---

## 6.7 `_deduplicate_results_by_url`

```python
def _deduplicate_results_by_url(
    results: Tuple[SearchResult, ...],
    *,
    notes: List[str],
) -> Tuple[SearchResult, ...]:
    seen: Set[str] = set()
    deduped: List[SearchResult] = []
    removed_count = 0

    for result in results:
        key = _normalize_url_for_dedup(result.url)
        if not key:
            continue

        if key in seen:
            removed_count += 1
            continue

        seen.add(key)
        deduped.append(result)

    if removed_count > 0:
        notes.append(f"{removed_count} duplicate URLs were removed.")

    return tuple(deduped)
```

---

## 6.8 `_deduplicate_images`

```python
def _deduplicate_images(
    images: Tuple[ImageResult, ...],
    *,
    notes: List[str],
) -> Tuple[ImageResult, ...]:
    seen: Set[str] = set()
    deduped: List[ImageResult] = []
    removed_count = 0

    for image in images:
        key = _normalize_url_for_dedup(image.url)
        if not key:
            continue

        if key in seen:
            removed_count += 1
            continue

        seen.add(key)
        deduped.append(image)

    if removed_count > 0:
        notes.append(f"{removed_count} duplicate image URLs were removed.")

    return tuple(deduped)
```

---

## 6.9 `_normalize_domain_list`

```python
def _normalize_domain_list(values: Optional[List[str]]) -> Set[str]:
    if not values:
        return set()

    domains: Set[str] = set()

    for value in values:
        if not isinstance(value, str):
            continue

        domain = value.strip().lower().removeprefix("www.")
        if domain:
            domains.add(domain)

    return domains
```

---

## 6.10 `_filter_results_by_domains`

```python
def _filter_results_by_domains(
    results: Tuple[SearchResult, ...],
    *,
    include_domains: Optional[List[str]],
    exclude_domains: Optional[List[str]],
    notes: List[str],
) -> Tuple[SearchResult, ...]:
    include_set = _normalize_domain_list(include_domains)
    exclude_set = _normalize_domain_list(exclude_domains)

    if not include_set and not exclude_set:
        return results

    before_count = len(results)
    filtered: List[SearchResult] = []

    for result in results:
        domain = _extract_domain_for_filter(result.url)

        if include_set and domain not in include_set:
            continue

        if exclude_set and domain in exclude_set:
            continue

        filtered.append(result)

    after_count = len(filtered)

    if include_set and after_count < before_count:
        notes.append(
            f"include_domains filter reduced results from {before_count} to {after_count}."
        )

    if exclude_set and after_count < before_count:
        notes.append(
            f"exclude_domains filter removed {before_count - after_count} results."
        )

    return tuple(filtered)
```

---

## 6.11 `_extract_domain_for_filter`

```python
def _extract_domain_for_filter(url: str) -> str:
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return ""

    host = parsed.hostname or ""
    return host.lower().removeprefix("www.")
```

---

## 6.12 `_has_site_operator`

用模块级编译正则，避免误匹配 `website:` / `offsite:`，这也是评审明确建议的实现。

```python
def _has_site_operator(queries: List[str]) -> bool:
    return any(_SITE_OPERATOR_PATTERN.search(query) for query in queries)
```

---

## 6.13 `_merge_many_search_responses`

```python
def _merge_many_search_responses(
    *,
    query: str,
    responses: List[SearchResponse],
    final_max_results: int,
    dedupe_domains: bool,
    max_per_domain: int,
    include_domains: Optional[List[str]],
    exclude_domains: Optional[List[str]],
    notes: List[str],
) -> SearchResponse:
    results: List[SearchResult] = []
    images: List[ImageResult] = []
    sources: List[str] = []

    for response in responses:
        results.extend(response.results)
        images.extend(response.images)

        if response.source:
            sources.append(response.source)

    deduped_results = _deduplicate_results_by_url(
        tuple(results),
        notes=notes,
    )

    filtered_results = _filter_results_by_domains(
        deduped_results,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        notes=notes,
    )

    if dedupe_domains:
        before_domain_dedupe = len(filtered_results)

        filtered_results = deduplicate_results_by_domain(
            filtered_results,
            max_per_domain=max_per_domain,
        )

        removed_count = before_domain_dedupe - len(filtered_results)
        if removed_count > 0:
            notes.append(
                f"{removed_count} same-domain results were removed by domain dedupe."
            )

    deduped_images = _deduplicate_images(
        tuple(images),
        notes=notes,
    )

    source = "multi"
    if sources:
        source = "multi:" + ",".join(sorted(set(sources)))

    return SearchResponse(
        query=query,
        results=filtered_results[:final_max_results],
        images=deduped_images[:final_max_results],
        source=source,
    )
```

---

## 6.14 `_has_content`

```python
def _has_content(response: SearchResponse) -> bool:
    return bool(response.answer or response.results or response.images)
```

---

## 6.15 `_with_source`

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

---

# 7. WebSearchTool 强化

文件：

```text
chat/application/tools/web_search_tool.py
```

## 7.1 常量

```python
MAX_QUERY_CHARS = 400

MAX_FETCH_TOP_PAGES = 3
DEFAULT_FETCH_TOP_PAGES_LIMIT = 2
FETCHED_PAGE_MAX_CHARS = 3000
MAX_FETCHED_PAGE_CHARS = 6000
FETCH_PAGE_TIMEOUT_SECONDS = 15.0
```

---

## 7.2 schema 增加参数

新增：

```python
"include_domains": {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "Only keep results from these domains after search. "
        "Use for official-site or site-specific research."
    ),
},
"exclude_domains": {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "Exclude results from these domains after search."
    ),
},
"time_range": {
    "type": "string",
    "enum": ["day", "week", "month", "year"],
    "description": (
        "Optional freshness window. Best-effort parameter. "
        "When month/year is used, queries without time words may get the current year appended."
    ),
},
"fetched_page_max_chars": {
    "type": "integer",
    "description": "Maximum characters retained from each fetched top page. Default 3000. Maximum 6000.",
    "default": 3000,
    "minimum": 500,
    "maximum": 6000,
},
"fetch_page_timeout_seconds": {
    "type": "number",
    "description": "Timeout for each fetched top page. Default 15 seconds. Maximum 30 seconds.",
    "default": 15.0,
    "minimum": 3.0,
    "maximum": 30.0,
},
```

已有参数保留：

```text
query
queries
max_results
final_max_results
with_images
freshness_required
fetch_top_pages
fetch_top_pages_limit
allow_paid_fallback
dedupe_domains
max_per_domain
```

---

## 7.3 Tool description 强化 query 生成模板

追加到 `_TOOL_DESCRIPTION`：

```python
_TOOL_DESCRIPTION += (
    "\n\nQuery generation guidance:\n"
    "- For simple lookup, use query.\n"
    "- For complex questions, comparisons, debugging, or research tasks, use queries.\n"
    "- Generate 2-4 concise focused search-engine-style queries.\n"
    "- Keep each query short. Do not pass long natural-language paragraphs.\n\n"
    "Recommended query patterns for technical questions:\n"
    "1) original focused query\n"
    "2) official documentation query, often with site:docs...\n"
    "3) exact error message keywords\n"
    "4) GitHub issue or StackOverflow style query when debugging\n\n"
    "Recommended query patterns for comparison or selection:\n"
    "1) A vs B focused comparison\n"
    "2) A official documentation\n"
    "3) B official documentation\n"
    "4) real-world usage A OR B with current year if freshness matters\n"
)
```

评审也指出 Tool description 中的具体示例比抽象描述更有效，尤其是代码报错和对比/选型类 query 模板。

---

## 7.4 `execute()` notes 入口

```python
notes: List[str] = []

queries = _get_queries(
    kwargs,
    notes=notes,
)
```

---

## 7.5 `site:` 自动关闭 domain dedupe

在解析 `dedupe_domains` 后：

```python
dedupe_domains_provided = "dedupe_domains" in kwargs

dedupe_domains = _normalize_bool(
    kwargs.get("dedupe_domains", True)
)

if (
    not dedupe_domains_provided
    and _has_site_operator(queries)
):
    dedupe_domains = False
    notes.append(
        "Domain dedupe disabled because a site: operator was detected."
    )
```

---

## 7.6 单 query / 多 query 调用

单 query：

```python
response = await self._coordinator.search(
    query=queries[0],
    max_results=max_results,
    with_images=with_images,
    freshness_required=freshness_required,
    allow_paid_fallback=True,
)
```

多 query：

```python
response = await self._coordinator.search_many(
    queries=queries,
    max_results_per_query=max_results,
    final_max_results=final_max_results,
    with_images=with_images,
    freshness_required=freshness_required,
    allow_paid_fallback=allow_paid_fallback,
    concurrency=3,
    dedupe_domains=dedupe_domains,
    max_per_domain=max_per_domain,
    time_range=time_range,
    include_domains=include_domains,
    exclude_domains=exclude_domains,
    notes=notes,
)
```

---

## 7.7 stale cache note

在 `_format_response` 前：

```python
if response.source == "stale_cache" or (
    response.source and "stale_cache" in response.source
):
    notes.append(
        "Some results came from stale cache and may be outdated."
    )
```

---

## 7.8 `_fetch_top_pages`

```python
async def _fetch_top_pages(
    self,
    response: SearchResponse,
    *,
    limit: int,
    max_chars_per_page: int,
    timeout_seconds: float,
    notes: List[str],
) -> List[str]:
    if self._fetcher is None:
        return []

    contents: List[str] = []

    for index, result in enumerate(response.results[:limit], 1):
        url = result.url.strip()
        if not url:
            continue

        try:
            content = await asyncio.wait_for(
                self._fetcher.fetch(url),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            notes.append(
                f"Fetched page for result #{index} was skipped because it timed out."
            )
            continue
        except Exception as exc:
            log_fail(
                "搜索结果页面抓取失败",
                exc,
                url=url,
            )
            notes.append(
                f"Fetched page for result #{index} failed and was skipped."
            )
            continue

        if content is None:
            continue

        if not isinstance(content, str):
            notes.append(
                f"Fetched page for result #{index} was skipped because it returned non-text content."
            )
            continue

        content = content.strip()
        if not content:
            continue

        contents.append(
            f"--- Fetched page for result #{index} ---\n"
            f"Title: {result.title}\n"
            f"URL: {url}\n"
            f"Content:\n"
            f"{content[:max_chars_per_page]}"
        )

    return contents
```

这同时解决：

```text
1. FetchedDocument / 非 str 返回导致 .strip() 崩溃
2. deep search 被单页 fetch 卡死
3. fetched page 与搜索结果无法对应
```

评审明确建议每个 fetch 单独 timeout，而暂缓 `search_many` 全局 timeout。

---

## 7.9 `_format_response`

```python
def _format_response(
    response: SearchResponse,
    *,
    mode: str,
    queries: List[str],
    notes: List[str],
    extra_contents: Optional[List[str]] = None,
) -> str:
    unique_domains = count_unique_domains(tuple(response.results))

    lines = ["[Tool Result] Web search evidence pack"]
    lines.append(f"Mode: {mode}")

    if queries:
        lines.append("Queries:")
        for query in queries:
            lines.append(f"- {query}")

    if response.source:
        lines.append(f"Source: {response.source}")

    lines.append(
        f"Summary: {len(response.results)} results, "
        f"{len(response.images)} query-level images, "
        f"{unique_domains} unique domains."
    )

    if notes:
        lines.append("\nNotes:")
        for note in _deduplicate_notes(notes):
            lines.append(f"- {note}")

    if response.answer:
        lines.append(f"\nAnswer:\n{response.answer}")

    if response.results:
        lines.append("\nResults:")

    for index, result in enumerate(response.results, 1):
        title = result.title.strip() or result.url or "(no title)"
        url = result.url.strip()
        snippet = result.snippet.strip()
        domain = extract_domain(url)

        lines.append(f"\n{index}. {title}")

        if domain:
            lines.append(f"   Domain: {domain}")

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

    if extra_contents:
        lines.append("\nFetched top pages:")
        for content in extra_contents:
            lines.append("")
            lines.append(content)

    return _normalize_tool_result("\n".join(lines))
```

---

## 7.10 Tool helper

```python
def _deduplicate_notes(notes: List[str]) -> List[str]:
    seen: Set[str] = set()
    deduped: List[str] = []

    for note in notes:
        value = note.strip()
        if not value or value in seen:
            continue

        seen.add(value)
        deduped.append(value)

    return deduped
```

```python
def _normalize_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}

    return bool(value)
```

```python
def _normalize_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default

    return max(minimum, min(number, maximum))
```

```python
def _normalize_float(
    value: object,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default

    return max(minimum, min(number, maximum))
```

```python
def _get_optional_str_list(value: object) -> Optional[List[str]]:
    if not isinstance(value, list):
        return None

    items: List[str] = []

    for item in value:
        if not isinstance(item, str):
            continue

        item = item.strip()
        if item:
            items.append(item)

    return items or None
```

---

# 8. Container 状态

当前 container 已经符合本轮要求：

```text
1. web_search_coordinator 是 Singleton
2. fetch_coordinator 是 Singleton
3. WebSearchTool 注入 coordinator + fetcher
4. WebFetchTool 复用同一个 fetcher
```

对应代码已经存在：`web_search_coordinator = providers.Singleton(create_search_coordinator)`，`fetch_coordinator = providers.Singleton(FetchCoordinator, static_fetcher=..., steel_fetcher=..., local_script_fetcher=..., processor=...)`，并且 `WebSearchTool` / `WebFetchTool` 都注入了同一个 `fetch_coordinator`。

本轮不需要继续调整 container。

---

# 9. 测试清单

必须补齐：

```text
1. URL normalize 不小写 path
2. URL normalize 去掉 tracking params
3. URL normalize 对剩余 query 参数排序
4. URL normalize 去掉默认端口
5. query 超长截断 + notes
6. site: operator 自动关闭 domain dedupe
7. website: / offsite: 不误触发
8. fetched page 输出 result index
9. fetch 返回非 str 时跳过且 notes
10. fetch 单页 timeout 时跳过且 notes
11. Tavily paid fallback 最多一次
12. Tavily paid fallback 不写入普通 query cache
13. include_domains 过滤后 notes
14. exclude_domains 过滤后 notes
15. time_range=year/month 时 query 自动补当前年份
16. time_range=day/week 时不自动补年份
17. notes 去重
18. deep search 输出包含 Evidence Pack / Notes / Fetched top pages
```

关键断言示例：

```python
def test_normalize_url_preserves_path_case() -> None:
    url = "https://example.com/User/File?utm_source=x"
    assert _normalize_url_for_dedup(url) == "https://example.com/User/File"
```

```python
def test_site_operator_does_not_match_website() -> None:
    assert _has_site_operator(["website:example.com test"]) is False
    assert _has_site_operator(["offsite:example.com test"]) is False
    assert _has_site_operator(["site:example.com test"]) is True
```

```python
async def test_fetch_top_pages_skips_non_text_result() -> None:
    contents = await tool._fetch_top_pages(
        response,
        limit=1,
        max_chars_per_page=3000,
        timeout_seconds=15.0,
        notes=notes,
    )

    assert contents == []
    assert any("non-text content" in note for note in notes)
```

```python
async def test_tavily_paid_fallback_does_not_pollute_query_cache() -> None:
    await coordinator.search_many(
        queries=["unique query one", "unique query two"],
        allow_paid_fallback=True,
        notes=[],
    )

    key = make_search_cache_key(
        query="unique query one",
        max_results=5,
        with_images=False,
    )

    cached = await cache.get_fresh(key)
    assert cached is None
```

---

# 10. 最终交付清单

本次提交包含：

```text
1. URL 规范化去重
2. notes 机制
3. fetch_top_pages 绑定 result index
4. fetch 单页 timeout
5. 非 str fetch 结果跳过
6. site: operator 自动关闭 domain dedupe
7. query 长度保护
8. Tool description query 模板
9. include_domains / exclude_domains 后处理
10. time_range month/year 自动补年份
11. Tavily paid fallback 不写缓存
12. 新增测试
```

不包含：

```text
1. 新架构
2. 新第三方库
3. rerank
4. embedding
5. source quality scoring
6. search_many 全局 timeout
7. snippet 相似度去重
8. interleaving
```

---


# 11. 最终效果

最终输出从：

```text
搜索结果列表
```

升级为：

```text
[Tool Result] Web search evidence pack
Mode: deep
Queries:
- ...
Source: multi:searxng,duckduckgo,tavily
Summary: 12 results, 5 query-level images, 9 unique domains.

Notes:
- 3 duplicate URLs were removed.
- Domain dedupe disabled because a site: operator was detected.
- Tavily paid fallback was used once.
- Fetched page for result #2 was skipped because it returned non-text content.

Results:
1. ...
   Domain:
   URL:
   Snippet:

Fetched top pages:

--- Fetched page for result #1 ---
Title:
URL:
Content:
...
```

这版不会再扩大架构，但会显著提升：

```text
1. 结果干净度
2. 证据可追溯性
3. 模型使用搜索结果的稳定性
4. deep search 的抗失败能力
5. 多 query 搜索的用户体验
```
