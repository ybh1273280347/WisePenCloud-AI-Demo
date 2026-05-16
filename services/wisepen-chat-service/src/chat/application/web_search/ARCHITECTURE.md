# web_search 模块架构

## 一、目录结构

```
web_search/
├── models/              # 数据模型 + provider adapter mapper
│   ├── common.py        # 核心通用模型
│   ├── helpers.py       # 通用校验/辅助函数
│   ├── searxng.py       # SearXNG mapper
│   ├── serper.py        # Serper mapper
│   ├── brave.py         # Brave mapper
│   ├── serpapi.py       # SerpAPI mapper
│   ├── tavily.py        # Tavily mapper
│   ├── exa.py           # Exa mapper
│   ├── perplexity.py    # Perplexity mapper
│   └── __init__.py      # 统一导出
├── searcher/            # 搜索引擎 HTTP 客户端
│   ├── base.py          # BaseSearcher 抽象基类
│   ├── searxng_searcher.py
│   ├── serper_searcher.py
│   ├── brave_searcher.py
│   ├── serpapi_searcher.py
│   ├── tavily_searcher.py
│   ├── exa_searcher.py
│   ├── perplexity_searcher.py
│   ├── wikipedia_searcher.py
│   └── __init__.py
├── planning/            # 搜索策略规划
│   ├── models.py        # 规划相关数据模型
│   ├── planner.py       # 搜索计划构建器
│   └── __init__.py
├── ranking/             # 搜索结果排序
│   ├── models.py        # 排序候选模型
│   ├── url_ranker.py    # URL 重排流水线
│   ├── rrf.py           # 加权 RRF 融合
│   ├── bm25.py          # BM25 / BM25F 排序
│   ├── tokenizer.py     # 分词工具
│   ├── url_canonical.py # URL 标准化
│   └── __init__.py
├── runner/              # 搜索执行器
│   ├── searxng_variant_runner.py
│   ├── serper_variant_runner.py
│   ├── custom_provider_runner.py
│   ├── wikipedia_grounding_runner.py
│   └── __init__.py
├── utils/               # 工具函数
│   ├── queries.py       # 查询标准化
│   ├── notes.py         # 备注管理
│   ├── domains.py       # 域名工具
│   ├── images.py        # 图片去重
│   └── __init__.py
├── search_coordinator.py      # 搜索协调器（主入口）
├── factory.py                 # 工厂函数
├── cache.py                   # 搜索缓存
├── provider_policy.py         # provider 选择策略
├── backend_policy.py          # SearXNG 后端选择策略
├── errors.py                  # 异常体系
├── searxng_instance_validation.py
└── __init__.py                # 模块统一导出
```

## 二、分层职责

### 2.1 models/ — 数据模型 + provider mapper

**核心模型**（`common.py`）：

- `SearchResult` / `ImageResult` / `SearchResponse` —— 整个 web_search 的三类核心数据，所有 provider 最终统一输出此格式
- 不可变 dataclass（`frozen=True, slots=True`），`__post_init__` 内自动做 strip 清洗

**Provider mapper**（每个 provider 一个文件）：

- 每个 mapper 文件导出 `*SearchRequest` dataclass + `map_*_response` 函数
- searcher 只负责 HTTP 请求，不写字段映射
- mapper 层有 locale 辅助函数（如 `brave_locale_params`）

**辅助函数**（`helpers.py`）：

- `is_valid_result(result)` —— 结果合法性判定：`url 必须有；title 和 snippet 至少有一个`
- `has_response_content(response)` —— 响应是否包含可用结果

### 2.2 searcher/ — HTTP 客户端

- `BaseSearcher` 抽象基类定义统一接口：`async def search(query, *, max_results, with_images) -> SearchResponse`
- 每个具体 searcher 实现 HTTP 请求逻辑，调用对应 mapper 的 `map_*_response` 转换为通用模型
- 异常统一转为 `errors.py` 中定义的异常类型

### 2.3 planning/ — 搜索策略

- `build_search_plan()` 根据 mode（fast/normal/deep）生成搜索计划
- `SearchPlan` 包含 query variants（primary/secondary 等）、wikipedia keywords
- `detect_query_language()` 检测查询语言
- `validate_wikipedia_keyword()` 校验 Wikipedia 关键词有效性

### 2.4 ranking/ — 排序融合

- `rank_urls_pipeline()` 主入口，串联完整排序流水线
- `canonicalize_url()` → `deduplicate_by_canonical_url()` → `rerank_urls_for_single_query()` → `apply_domain_diversity()` → `fuse_query_variant_rankings()`
- `weighted_rrf()` 加权倒数排序融合，用于合并多轮 query 结果
- `rank_by_metadata_bm25f()` BM25F 算法，按字段权重排序
- `tokenize_for_bm25()` 中文分词（jieba）+ 英文降维

### 2.5 runner/ — 执行层

每个 runner 负责一类搜索的执行：

- `run_searxng_variants()` —— 执行 SearXNG query variants
- `run_serper_variants()` —— 执行 Serper fallback
- `run_custom_provider_calls()` —— 执行自定义 provider（tavily / brave / serpapi / exa / perplexity）
- `run_wikipedia_grounding()` —— Wikipedia 知识支撑

### 2.6 cache/ — 缓存

- `SearchCache` 基于 `cachetools.TTLCache`，双层 TTL：recall（30min）/ grounding（24h）
- `make_search_cache_key()` 构建确定性缓存键，支持 source / query / language / engines / backend_mode / user_id_hash / searxng_instance_hash 等维度

### 2.7 search_coordinator.py — 调度中心

`SearchCoordinator` 是整个模块的入口，流程：

```
search_many()
    ↓
normalize_queries()                   查询标准化
build_search_plan()                   构建搜索计划
run_wikipedia_grounding()             Wikipedia 知识支撑
run_searxng_variants()                平台 SearXNG
select_default_provider_calls()       Serper 降级策略
run_serper_variants()                 执行 Serper
select_custom_provider_calls()        自定义 provider 策略
run_custom_provider_calls()           执行自定义 provider
rank_urls_pipeline()                  排序融合
    ↓
SearchManyResult(response, grounding)
```

### 2.8 provider_policy.py — Provider 选择策略

- `select_default_provider_calls()` —— 根据 mode 和 SearXNG 结果质量决定是否触发 Serper
- `select_custom_provider_calls()` —— 解析用户配置的自定义 provider credential
- `hash_user_id()` / `provider_params_hash()` —— 缓存键构建

### 2.9 backend_policy.py — SearXNG 后端选择

- `select_searxng_backend()` —— 根据 user_id 选择自定义 SearXNG 后端或平台默认
- `custom_searxng_backend_slot()` —— 并发控制（按 user + instance 两级信号量）

## 三、数据流

```
用户请求 → SearchCoordinator.search_many()
    │
    ├─ 1. 查询标准化 (normalize_queries)
    ├─ 2. 构建搜索计划 (build_search_plan)
    │      └─ 拆分 query variants + wikipedia keywords
    ├─ 3. Wikipedia grounding (run_wikipedia_grounding)
    │      └─ WikipediaClient → WikipediaGroundingResult
    ├─ 4. SearXNG 搜索 (run_searxng_variants)
    │      └─ SearXNGSearcher → map_searxng_response → SearchResponse
    ├─ 5. Serper 降级 (select / run_serper_variants)
    │      └─ SerperSearcher → map_serper_response → SearchResponse
    ├─ 6. 自定义 provider (select / run_custom_provider_calls)
    │      └─ *Searcher → map_*_response → SearchResponse
    ├─ 7. 排序融合 (rank_urls_pipeline)
    │      └─ canonicalize → deduplicate → rerank → domain diversity → RRF fuse
    └─ 8. 返回 SearchManyResult
```

## 四、设计原则

### 4.1 Provider adapter 分层

```
models/provider.py          ← 字段映射 + request 结构
    ↓ map_*_response
searcher/provider_searcher.py  ← 只做 HTTP
    ↓ SearchResponse
search_coordinator.py       ← 调度
```

- mapper 和 searcher 严格分离，searcher 不写字段映射
- provider 不走 mapper 的复杂 dataclass，统一输出 `SearchResult` / `SearchResponse`
- locale 参数放在 mapper 层

### 4.2 结果合法性

统一由 `is_valid_result()` 决定，语义为：

- `url` 必须有
- `title` 和 `snippet` 至少有一个

### 4.3 异常体系

```
WebSearchError
├── SearchProviderError         (provider 返回错误)
├── SearchTimeoutError          (HTTP 超时)
├── SearchRateLimitError        (429 限流)
├── EmptySearchResultError      (空结果)
└── SearchProviderTransientError (可重试的瞬态错误)
```

### 4.4 缓存策略

- 分 purpose 隔离 TTL：recall（30min）/ grounding（24h）
- 缓存键包含 source / query / language / engines / backend_mode / user_id_hash / searxng_instance_hash / provider_params_hash 等维度
- custom provider 支持按 user_id 隔离缓存
- 空结果不缓存

### 4.5 类型规范

- 集合类型统一大写：`List`, `Dict`, `Tuple`, `Set`, `Optional`
- 不使用 `list[...]` / `dict[...]` / `str | None` 等语法
- 内部常量以 `_` 前缀标记为模块私有

## 五、关键边界

| 边界 | 处理方式 |
|---|---|
| 自定义 SearXNG 后端 | `backend_policy.py` 根据 user_id 选择，支持 fallback |
| 自定义 provider | `provider_policy.py` 解析 credential，支持按 mode 决定是否触发 |
| 用户不控制 base_url | `CustomProviderCredential` 不允许用户设置 base_url |
| 缓存隔离 | 有 user_id 时写 custom cache，否则写 platform cache |
| Wikipedia 失败 | 不阻塞主搜索，失败只打日志 |
| Serper 降级 | fast 模式不触发，normal 模式在 SearXNG 结果不足时触发，deep 模式强制触发 |