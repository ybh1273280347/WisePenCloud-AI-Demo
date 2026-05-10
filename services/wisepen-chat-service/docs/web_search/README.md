# web_search 文档索引

## 功能范围

`web_search` 负责搜索能力，核心线索包括 WebSearchTool、SearchCoordinator、多搜索引擎适配、缓存、降级 stage、日志增强、错误测试增强和 Tavily 子模块。

## 当前可用结论

- Tool 层不应直接绑定 Tavily，应通过通用 searcher/coordinator 接口工作。
- Searcher 表达搜索引擎适配，Stage 表达降级链步骤，避免把缓存伪装成 searcher。
- 缓存使用 TTLCache 时需要注意同步边界。
- Tavily 属于 web_search 的供应商子模块，文档放在 `优化/tavily/`。
- 日志和错误测试是稳定性增强线索，不是首次架构设计。

## 推荐阅读顺序

| 顺序 | 文档 | 用途 |
|---|---|---|
| 1 | [新功能/web_search - 第1次 新功能.md](新功能/web_search%20-%20第1次%20新功能.md) | Web Search 首次接入设计。 |
| 2 | [重构/web_search - 第1次 重构.md](重构/web_search%20-%20第1次%20重构.md) | Web Search 模块重构背景和架构设计。 |
| 3 | [重构/web_search - 第2次 重构.md](重构/web_search%20-%20第2次%20重构.md) | 第二轮重构结论。 |
| 4 | [重构/web_search - 第3次 重构.md](重构/web_search%20-%20第3次%20重构.md) | searcher/infrastructure 目录调整。 |
| 5 | [重构/web_search - 第4次 重构.md](重构/web_search%20-%20第4次%20重构.md) | SearchStage 降级链设计决策。 |

## 优化线索

| 文档 | 主题 |
|---|---|
| [优化/web_search - 第1次 优化.md](优化/web_search%20-%20第1次%20优化.md) | Web Search 工程实践总方案。 |
| [优化/web_search - 第2次 优化.md](优化/web_search%20-%20第2次%20优化.md) | TTLCache 与 async lock 缓存优化。 |
| [优化/web_search - 第3次 优化.md](优化/web_search%20-%20第3次%20优化.md) | 响应模型优化。 |
| [优化/web_search - 第4次 优化.md](优化/web_search%20-%20第4次%20优化.md) | WebSearchTool 与 searcher 接口解耦。 |
| [优化/web_search - 第5次 优化.md](优化/web_search%20-%20第5次%20优化.md) | 日志增强。 |
| [优化/web_search - 第6次 优化.md](优化/web_search%20-%20第6次%20优化.md) | 函数清理。 |
| [优化/web_search - 第7次 优化.md](优化/web_search%20-%20第7次%20优化.md) | 错误路径测试增强方案。 |
| [优化/web_search - 第8次 优化.md](优化/web_search%20-%20第8次%20优化.md) | 客观证据输出、freshness_required、图片字段增强、域名去重和 fetch_top_pages。 |

## Tavily 子模块

| 文档 | 主题 |
|---|---|
| [优化/tavily/tavily - 第1次 优化.md](优化/tavily/tavily%20-%20第1次%20优化.md) | Tavily 请求模型和参数收敛。 |
| [优化/tavily/tavily - 第2次 优化.md](优化/tavily/tavily%20-%20第2次%20优化.md) | TavilySearcher 使用 AsyncTavilyClient 并封装错误。 |

## 修复线索

| 文档 | 主题 |
|---|---|
| [修复/web_search - 第1次 修复.md](修复/web_search%20-%20第1次%20修复.md) | 日志驱动的 SearXNG 图片搜索等问题修复。 |

## 追溯提示

如果要理解当前架构，优先读重构第 4 次和优化第 4 次；如果要理解稳定性，读优化第 5 次、第 7 次和修复第 1 次；如果要理解 Tavily 供应商适配，读 `优化/tavily/`。
