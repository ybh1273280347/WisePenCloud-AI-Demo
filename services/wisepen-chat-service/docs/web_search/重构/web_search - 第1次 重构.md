# Web Search 模块重构文档

## 1. 重构背景

### 1.1 原始实现

原始联网搜索功能实现为**单一文件** `web_search_tool.py`，内部直接调用 Tavily SDK：

```text
web_search_tool.py
├── Tavily SDK 调用
├── 响应解析
├── 结果格式化
├── 错误处理
└── 工具注册
```

### 1.2 存在的问题

| 问题 | 描述 |
|------|------|
| **单点依赖** | 仅依赖 Tavily（付费 API），Tavily 宕机或超时则搜索完全不可用 |
| **无缓存** | 相同查询重复调用 API，浪费额度和延迟 |
| **无降级** | 搜索引擎失败后无自动回退机制 |
| **无熔断** | 搜索引擎持续异常时仍反复请求，加剧故障 |
| **单文件膨胀** | 搜索逻辑、响应映射、格式化、工具注册全部耦合在一个文件中 |
| **不可扩展** | 新增搜索引擎需要大幅修改现有代码 |

### 1.3 重构目标

1. **多引擎降级链**：SearXNG → DuckDuckGo → Stale Cache → Tavily，自动回退
2. **二级缓存**：Fresh Cache（短期） + Stale Cache（长期），减少 API 调用
3. **熔断保护**：搜索引擎连续失败后自动断路，冷却后重试
4. **模块化架构**：职责分层，高内聚低耦合
5. **配置外部化**：所有引擎参数、熔断阈值、缓存 TTL 均从 settings 读取

---

## 2. 架构设计

### 2.1 最终目录结构

```text
chat/application/web_search/
├── __init__.py               # 顶层统一导出
├── coordinator.py            # 搜索调度器（降级链编排）
├── errors.py                 # 公共异常体系
├── factory.py                # 装配入口
├── infrastructure/           # 基础设施能力
│   ├── __init__.py
│   ├── cache.py              # Fresh/Stale 二级 TTL 缓存
│   └── circuit_breaker.py    # 熔断器
├── models/                   # 响应模型与供应商映射
│   ├── __init__.py
│   ├── common.py             # 通用 SearchResponse / SearchResult / ImageResult
│   ├── searxng.py            # SearXNG 请求/响应映射
│   └── tavily.py             # Tavily 请求/响应映射
└── searcher/                 # 搜索引擎适配器
    ├── __init__.py
    ├── base_searcher.py      # WebSearcher Protocol 接口
    ├── duckduckgo_searcher.py
    ├── searxng_searcher.py
    └── tavily_searcher.py
```

### 2.2 职责分层

```text
┌─────────────────────────────────────────────────────────────┐
│  web_search_tool.py  （工具层：面向 LLM 的工具接口）          │
│  - 参数校验、结果格式化、错误提示                              │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│  coordinator.py  （编排层：搜索调度与降级）                    │
│  - Fresh Cache 查询                                         │
│  - 降级链遍历                                               │
│  - 空结果判断与继续降级                                      │
│  - 成功结果写入缓存                                         │
└───────┬──────────────────────────────────┬──────────────────┘
        │                                  │
┌───────▼──────────┐  ┌────────────────────▼─────────────────┐
│  infrastructure/  │  │  searcher/                           │
│  - cache.py       │  │  - base_searcher.py (Protocol)      │
│  - circuit_breaker│  │  - searxng_searcher.py              │
└───────┬──────────┘  │  - duckduckgo_searcher.py            │
        │             │  - tavily_searcher.py                 │
        │             └──────────────────────────────────────┘
        │
┌───────▼──────────┐
│  models/          │
│  - common.py      │
│  - searxng.py     │
│  - tavily.py      │
└──────────────────┘
```

| 层 | 目录/文件 | 职责 |
|----|-----------|------|
| **工具层** | `web_search_tool.py` | 面向 LLM 的工具接口，参数校验与结果格式化 |
| **编排层** | `coordinator.py` | 搜索调度、降级链遍历、缓存读写 |
| **装配层** | `factory.py` | 按 settings 组装完整 SearchCoordinator |
| **基础设施层** | `infrastructure/` | 缓存、熔断等横切能力 |
| **适配器层** | `searcher/` | 各搜索引擎的具体调用实现 |
| **模型层** | `models/` | 通用响应模型与供应商响应映射 |

---

## 3. 核心组件详解

### 3.1 WebSearcher Protocol（`searcher/base_searcher.py`）

所有搜索引擎适配器必须满足的接口协议：

```python
class WebSearcher(Protocol):
    @property
    def engine_name(self) -> str: ...

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse: ...
```

采用 `Protocol` 而非抽象基类，搜索器无需显式继承，只需满足接口契约即可（鸭子类型）。

### 3.2 SearchCoordinator（`coordinator.py`）

搜索调度核心，按优先级依次尝试多种搜索策略，自动降级：

```text
请求进入
    │
    ▼
Fresh Cache ──命中──→ 返回
    │ 未命中
    ▼
SearXNG ──成功──→ 写缓存 → 返回
    │ 失败/空结果
    ▼
DuckDuckGo ──成功──→ 写缓存 → 返回
    │ 失败/空结果
    ▼
Stale Cache ──命中──→ 返回（不写缓存）
    │ 未命中
    ▼
Tavily ──成功──→ 写缓存 → 返回
    │ 失败
    ▼
返回 None 或最后一条空结果
```

关键设计：

- **`SearchChainItem`**：降级链节点，标记 `cacheable` 控制是否缓存该引擎的结果
- **`StaleCacheSearcher`**：将 stale cache 包装成 searcher 插入降级链，避免在 SearXNG/DDG 短暂故障时立刻烧 Tavily 额度
- **`continue_on_empty`**：搜索结果为空时是否继续降级（默认 True）
- **异常分类**：`WebSearchError`（预期降级）和 `Exception`（意外异常）均触发降级，但日志语义不同

### 3.3 SearchCache（`infrastructure/cache.py`）

二级 TTL 缓存，线程安全：

```text
写入时：同时写入 fresh_cache 和 stale_cache
读取时：
  - get_fresh()：从 fresh_cache 读，命中则直接使用
  - get_stale()：从 stale_cache 读，仅在降级链中作为兜底
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `fresh_ttl` | 3600 (1h) | Fresh 缓存有效期 |
| `stale_ttl` | 86400 (24h) | Stale 缓存有效期 |
| `maxsize` | 1024 | 每级缓存最大条目数 |

缓存 Key 生成规则：`(normalized_query, max_results, with_images)`，其中 query 做空白归一化和小写处理。

### 3.4 CircuitBreakerWebSearcher（`infrastructure/circuit_breaker.py`）

熔断器装饰器，包装任意 WebSearcher：

```text
关闭状态（正常）
    │ 连续失败次数 >= failure_threshold
    ▼
打开状态（熔断）
    │ 直接抛出 WebSearchUnavailable，不调用底层 searcher
    │ 经过 cooldown_seconds 后
    ▼
半开状态（下次请求尝试调用）
    │ 成功 → 重置计数，回到关闭状态
    │ 失败 → 重新进入打开状态
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `failure_threshold` | 3 | 连续失败多少次触发熔断 |
| `cooldown_seconds` | 60 | 熔断后冷却时间（秒） |

### 3.5 异常体系（`errors.py`）

```text
WebSearchError (RuntimeError)
├── WebSearchUnavailable      # 搜索引擎不可用（超时、连接失败、5xx、熔断）
└── WebSearchInvalidResponse  # 搜索引擎返回无法解析的响应
```

### 3.6 模型层（`models/`）

**通用模型**（`common.py`）：

```python
@dataclass(frozen=True, slots=True)
class ImageResult:
    url: str
    desc: Optional[str] = None

@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    images: Sequence[ImageResult] = ()

@dataclass(frozen=True, slots=True)
class SearchResponse:
    query: str
    results: Sequence[SearchResult] = ()
    answer: Optional[str] = None
    images: Sequence[ImageResult] = ()
```

**供应商映射**：每个搜索引擎有独立的请求模型和响应映射函数：

| 文件 | 请求模型 | 响应映射函数 |
|------|----------|-------------|
| `tavily.py` | `TavilySearchRequest` | `map_tavily_response()` |
| `searxng.py` | `SearXNGSearchRequest` | `map_searxng_response()`, `merge_search_responses()` |

映射函数负责将供应商特有的 JSON 结构转换为通用 `SearchResponse`，过滤无效结果（如 url 为空的条目）。

---

## 4. 搜索引擎适配器

### 4.1 TavilySearcher（`searcher/tavily_searcher.py`）

```python
class TavilySearcher:
    engine_name = "tavily"

    # 构造参数：api_key, timeout
    # 使用 AsyncTavilyClient 异步调用
    # payload 中固定 search_depth="basic"
    # 异常包装为 WebSearchUnavailable
```

### 4.2 SearXNGSearcher（`searcher/searxng_searcher.py`）

```python
class SearXNGSearcher:
    engine_name = "searxng"

    # 构造参数：base_url, timeout, language, safesearch
    # 使用 httpx.AsyncClient 调用自托管 SearXNG 实例
    # with_images=True 时并发请求 web + images 两个分类
    # 403 错误给出配置提示（search.formats 必须包含 json）
    # HTTP 错误 → WebSearchUnavailable
    # JSON 解析错误 → WebSearchInvalidResponse
```

### 4.3 DuckDuckGoBufferSearcher（`searcher/duckduckgo_searcher.py`）

```python
class DuckDuckGoBufferSearcher:
    engine_name = "duckduckgo_buffer"

    # 构造参数：timeout, region, safesearch
    # 使用 ddgs.DDGS 同步客户端，通过 asyncio.to_thread 包装为异步
    # 外层 asyncio.wait_for 控制总超时
    # 过滤 url 为空的搜索结果和图片结果
    # 异常包装为 WebSearchUnavailable
```

---

## 5. 装配与依赖注入

### 5.1 factory.py

```python
def create_search_coordinator() -> SearchCoordinator:
    cache = SearchCache(
        fresh_ttl=settings.WEB_SEARCH_FRESH_CACHE_TTL,
        stale_ttl=settings.WEB_SEARCH_STALE_CACHE_TTL,
        maxsize=settings.WEB_SEARCH_CACHE_MAXSIZE,
    )

    searxng = CircuitBreakerWebSearcher(
        SearXNGSearcher(...),
        failure_threshold=settings.SEARXNG_FAILURE_THRESHOLD,
        cooldown_seconds=settings.SEARXNG_COOLDOWN_SECONDS,
    )

    duckduckgo = CircuitBreakerWebSearcher(
        DuckDuckGoBufferSearcher(...),
        failure_threshold=settings.DUCKDUCKGO_FAILURE_THRESHOLD,
        cooldown_seconds=settings.DUCKDUCKGO_COOLDOWN_SECONDS,
    )

    tavily = CircuitBreakerWebSearcher(
        TavilySearcher(...),
        failure_threshold=settings.TAVILY_FAILURE_THRESHOLD,
        cooldown_seconds=settings.TAVILY_COOLDOWN_SECONDS,
    )

    chain = (
        SearchChainItem(searxng, cacheable=True),
        SearchChainItem(duckduckgo, cacheable=True),
        SearchChainItem(StaleCacheSearcher(cache), cacheable=False),
        SearchChainItem(tavily, cacheable=True),
    )

    return SearchCoordinator(cache=cache, chain=chain, continue_on_empty=True)
```

### 5.2 container.py

```python
web_search_tool = providers.Singleton(
    WebSearchTool,
    coordinator=providers.Callable(create_search_coordinator),
)
```

---

## 6. 配置项（`app_settings.py`）

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| **Tavily** | | | |
| `TAVILY_API_KEY` | str | `"dummy_key"` | Tavily API 密钥 |
| `TAVILY_ENABLED` | bool | `True` | 是否启用 Tavily |
| `TAVILY_TIMEOUT` | float | `15.0` | 请求超时（秒） |
| `TAVILY_FAILURE_THRESHOLD` | int | `5` | 连续失败触发熔断次数 |
| `TAVILY_COOLDOWN_SECONDS` | int | `60` | 熔断冷却时间（秒） |
| **SearXNG** | | | |
| `SEARXNG_ENABLED` | bool | `False` | 是否启用 SearXNG |
| `SEARXNG_BASE_URL` | str | `"http://localhost:8080"` | SearXNG 实例地址 |
| `SEARXNG_TIMEOUT` | float | `5.0` | 请求超时（秒） |
| `SEARXNG_LANGUAGE` | str | `""` | 搜索语言 |
| `SEARXNG_SAFESEARCH` | int | `1` | 安全搜索级别 |
| `SEARXNG_FAILURE_THRESHOLD` | int | `3` | 连续失败触发熔断次数 |
| `SEARXNG_COOLDOWN_SECONDS` | int | `60` | 熔断冷却时间（秒） |
| **DuckDuckGo** | | | |
| `DUCKDUCKGO_BUFFER_ENABLED` | bool | `True` | 是否启用 DuckDuckGo |
| `DUCKDUCKGO_TIMEOUT` | float | `8.0` | 请求超时（秒） |
| `DUCKDUCKGO_REGION` | str | `"wt-wt"` | 搜索区域 |
| `DUCKDUCKGO_SAFESEARCH` | str | `"moderate"` | 安全搜索级别 |
| `DUCKDUCKGO_FAILURE_THRESHOLD` | int | `3` | 连续失败触发熔断次数 |
| `DUCKDUCKGO_COOLDOWN_SECONDS` | int | `120` | 熔断冷却时间（秒） |
| **缓存** | | | |
| `WEB_SEARCH_FRESH_CACHE_TTL` | int | `3600` | Fresh 缓存 TTL（秒） |
| `WEB_SEARCH_STALE_CACHE_TTL` | int | `86400` | Stale 缓存 TTL（秒） |
| `WEB_SEARCH_CACHE_MAXSIZE` | int | `1024` | 缓存最大条目数 |

---

## 7. 重构前后对比

### 7.1 架构对比

```text
重构前：
┌──────────────────────────────────────┐
│        web_search_tool.py            │
│  ┌────────────────────────────────┐  │
│  │ Tavily SDK 直接调用            │  │
│  │ 响应解析 + 格式化 + 错误处理   │  │
│  │ 工具注册                       │  │
│  └────────────────────────────────┘  │
└──────────────────────────────────────┘

重构后：
┌─────────────────────────────────────────────────────────────┐
│  web_search_tool.py  ──→  SearchCoordinator                 │
│                              │                               │
│              ┌───────────────┼───────────────┐               │
│              ▼               ▼               ▼               │
│         SearXNG        DuckDuckGo       Tavily              │
│         (熔断)          (熔断)          (熔断)               │
│              │               │               │               │
│              └───────┬───────┘               │               │
│                      ▼                       │               │
│                Stale Cache                   │               │
│                      │                       │               │
│                      └───────────────────────┘               │
│                              │                               │
│                        Fresh Cache                           │
└─────────────────────────────────────────────────────────────┘
```

### 7.2 关键改进

| 维度 | 重构前 | 重构后 |
|------|--------|--------|
| 搜索引擎 | 仅 Tavily | SearXNG + DuckDuckGo + Tavily 降级链 |
| 缓存 | 无 | Fresh + Stale 二级 TTL 缓存 |
| 熔断 | 无 | 每个引擎独立熔断器 |
| 降级 | 无 | 自动降级 + Stale Cache 兜底 |
| 文件组织 | 单文件 | 4 层 13 文件，职责清晰 |
| 可扩展性 | 新增引擎需改原文件 | 新增 Searcher + 注册到 chain 即可 |
| 配置管理 | 硬编码 | 全部外部化到 settings |

---

## 8. 扩展指南

### 8.1 新增搜索引擎

1. 在 `searcher/` 下新建 `xxx_searcher.py`，实现 `WebSearcher` Protocol
2. 在 `models/` 下新建 `xxx.py`，实现请求模型和响应映射
3. 在 `factory.py` 的 `create_search_coordinator()` 中添加 `SearchChainItem`
4. 在 `app_settings.py` 中添加对应配置项

### 8.2 新增基础设施能力

1. 在 `infrastructure/` 下新建模块（如 `rate_limiter.py`）
2. 在 `infrastructure/__init__.py` 中导出
3. 在 `factory.py` 中装配到搜索引擎或 coordinator

### 8.3 调整降级顺序

修改 `factory.py` 中 `chain` 元组的顺序即可，无需改动其他代码。
