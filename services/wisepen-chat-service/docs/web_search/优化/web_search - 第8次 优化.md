# web_search 第 8 次优化：客观证据输出与时效控制增强

## 背景

当前 `web_search` 已经形成稳定降级链：

```text
Fresh Cache
    -> SearXNG
    -> DuckDuckGo
    -> Stale Cache
    -> Tavily
```

本轮目标不是扩大调度架构，而是增强工具输出对模型的可用性：

```text
1. 结构化搜索结果输出
2. freshness_required 控制是否跳过 stale cache
3. 图片结果字段增强
4. 域名客观展示
5. 域名轻量去重
6. 可选 fetch_top_pages
```

---

## 核心边界

工具只提供客观信息：

```text
Domain
URL
Snippet
Image URL
source_url
thumbnail_url
resolution
```

工具不做主观可信度判断：

```text
不输出 Source type
不判断 official_documentation
不判断 repository
不判断 qa_forum
不维护域名白名单
```

是否官方、是否可信、是否需要继续读取正文，由模型根据用户问题和上下文判断。

---

## 改动文件

```text
chat/application/web_search/models/common.py
chat/application/web_search/models/searxng.py
chat/application/web_search/models/tavily.py
chat/application/web_search/searcher/duckduckgo_searcher.py
chat/application/web_search/search_coordinator.py
chat/application/web_search/utils/domains.py
chat/application/web_search/utils/__init__.py
chat/application/tools/web_search_tool.py
```

没有新增：

```text
web_search/utils/freshness.py
web_search/utils/source_quality.py
```

---

## 1. ImageResult 字段增强

### 目标

图片搜索结果不仅要返回图片 URL，还要给模型更多客观上下文。

### 新字段

```text
source_url
thumbnail_url
resolution
```

### 用途

```text
source_url       图片所在页面或来源页面
thumbnail_url    搜索引擎返回的缩略图
resolution       搜索引擎返回的图片尺寸信息
```

这些字段只表达客观来源，不表达可信度判断。

---

## 2. freshness_required 参数

### 目标

模型可以根据用户问题决定是否允许使用过期缓存。

### 行为

```text
freshness_required=False:
    Fresh Cache -> SearXNG -> DuckDuckGo -> Stale Cache -> Tavily

freshness_required=True:
    Fresh Cache -> SearXNG -> DuckDuckGo -> Tavily
```

也就是说：

```text
fresh cache 仍然可用
stale cache 会被跳过
```

### 原因

`freshness_required` 只影响 stale cache 是否可用，不影响 fresh cache 复用，因此没有进入 cache key。

---

## 3. SearchCoordinator 日志增强

### 新增日志字段

成功、失败和空结果路径会记录：

```text
stage
query
max_results
with_images
elapsed_ms
results
images
```

最终失败时记录：

```text
freshness_required
failures
```

当跳过 stale cache 时记录：

```text
freshness_required=True，跳过 stale cache
```

---

## 4. 域名客观展示与轻量去重

### 新增模块

```text
web_search/utils/domains.py
```

提供：

```text
extract_domain
count_unique_domains
deduplicate_results_by_domain
```

### 规则

```text
去掉 www. 前缀
同一 domain 最多保留 2 条结果
无法解析 domain 的结果保留
```

### 说明

这个模块只做客观域名处理，不做 source quality 分类。

---

## 5. 搜索引擎 mapper 增强

### SearXNG

图片 mapper 现在读取：

```text
img_src
thumbnail_src / thumbnail
url
resolution
title
```

文本结果返回前做同域去重。

### DuckDuckGo

图片 mapper 现在读取：

```text
image / thumbnail
url / source
title
```

文本结果返回前做同域去重。

### Tavily

图片 mapper 现在支持：

```text
字符串图片 URL
对象图片 URL
description / desc / alt
source_url
thumbnail_url
```

文本结果返回前做同域去重。

---

## 6. WebSearchTool 输出结构化

### 新输出结构

```text
[Tool Result] Web search results for: ...
Source: ...
Summary: ... results, ... query-level images, ... unique domains.

Results:

1. Title
   Domain: example.com
   URL: https://example.com/page
   Snippet: ...
```

图片输出包含客观详情：

```text
- https://image.example/a.jpg (alt text; resolution=800x600; source=https://example.com/page)
```

当结果来自 stale cache 时，额外提示：

```text
Note: These results came from stale cache and may be outdated.
```

---

## 7. fetch_top_pages

### 目标

当片段不足以支撑回答时，模型可以主动要求抓取前 1-2 个结果页正文。

### 行为

`fetch_top_pages=True` 时：

```text
1. 对前 2 个搜索结果调用 FetchCoordinator.fetch()
2. 每页最多追加 3000 字符
3. 抓取失败则跳过该页
4. 不影响原搜索结果输出
```

输出位置：

```text
Fetched top pages:

--- Page 1 ---
Fetched page: https://...
...
```

默认不开启。

---

## 8. 工具参数

新增参数：

```text
freshness_required: bool = False
fetch_top_pages: bool = False
```

保留参数：

```text
query
max_results
with_images
```

根据 `docs/code_style.md`，布尔参数直接按 schema 约定读取，不支持字符串布尔值转换。

---

## 风格调整

本轮遵守：

```text
1. 不增加宽容式 bool 转换
2. 不新增 source_quality 主观分类
3. 不新增 freshness 工具模块
4. 不新增空壳配置类
5. 模块级函数不使用单下划线前缀
6. 用 __all__ 表达导出边界
```

`utils/domains.py` 虽然位于 `utils` 目录，但文件名表达稳定职责：域名解析和去重。没有新增 `utils.py` 这类万能文件。

---

## 验收结果

已验证：

```text
1. py_compile 通过
2. freshness_required=True 会跳过 stale cache 并继续 Tavily
3. freshness_required=False 时 stale cache 仍可命中
4. 同一 domain 最多保留 2 条
5. 格式化输出包含 Source / Summary / Domain / URL / Snippet
6. 图片输出包含 resolution 和 source
7. 输出不包含 Source type / official_documentation / qa_forum / repository
8. git diff --check 通过
```

完整 `uv run` 测试依赖本机 uv cache 权限，本轮未强制执行。

---

## 最终效果

```text
web_search 不再做主观可信度判断
工具只返回客观、结构化、可被模型利用的搜索证据
模型可以控制是否需要强时效
模型可以在需要时抓取前几个结果页正文
搜索结果的域名分布更清楚
同域刷屏结果更少
```
