# web_fetch 文档索引

## 近期修复

| 文档 | 主题 |
|---|---|
| [修复/web_fetch - 第1次 修复.md](修复/web_fetch%20-%20第1次%20修复.md) | local_web_fetcher 移除强制直连，继承用户电脑代理环境。 |

## 功能范围

`web_fetch` 负责网页和文档抓取能力，核心线索包括 `WebFetchTool`、`FetchCoordinator` 降级链、`ContentCleaner`、静态/Steel/本地脚本抓取器，以及文档格式补丁。

## 当前可用结论

- 工具层负责参数校验和结果封装，抓取链路交给 coordinator。
- 抓取能力按 StaticFetcher、SteelFetcher、LocalScriptFetcher 分层降级。
- 内容清洗应集中到 ContentCleaner，调度器不承担正文清洗细节。
- 本地反爬脚本是兜底能力，不应和 Python 调度器强耦合。
- PDF/Office/XML 等非 HTML 文档属于补丁和扩展线索。

## 推荐阅读顺序

| 顺序 | 文档 | 用途 |
|---|---|---|
| 1 | [新功能/web_fetch - 第1次 新功能.md](新功能/web_fetch%20-%20第1次%20新功能.md) | web_fetch 初次功能设计。 |
| 2 | [重构/web_fetch - 第1次 重构.md](重构/web_fetch%20-%20第1次%20重构.md) | 从浏览器工具总重构中拆出的 web_fetch 总览。 |
| 3 | [重构/web_fetch - 第2次 重构.md](重构/web_fetch%20-%20第2次%20重构.md) | web_fetch 独立重构意见。 |
| 4 | [重构/web_fetch - 第3次 重构.md](重构/web_fetch%20-%20第3次%20重构.md) | 本地反爬脚本重构。 |

## 优化线索

| 文档 | 主题 |
|---|---|
| [优化/web_fetch - 第1次 优化.md](优化/web_fetch%20-%20第1次%20优化.md) | local_web_fetcher 最终优化。 |
| [优化/web_fetch - 第2次 优化.md](优化/web_fetch%20-%20第2次%20优化.md) | Python 调用本地 JS fetcher 的适配优化。 |
| [优化/web_fetch - 第3次 优化.md](优化/web_fetch%20-%20第3次%20优化.md) | ContentProcessor 优化。 |
| [优化/web_fetch - 第4次 优化.md](优化/web_fetch%20-%20第4次%20优化.md) | SteelFetcher 与 FetchCoordinator 优化。 |
| [优化/web_fetch - 第5次 优化.md](优化/web_fetch%20-%20第5次%20优化.md) | WebFetchTool 最小必要优化。 |
| [优化/web_fetch - 第6次 优化.md](优化/web_fetch%20-%20第6次%20优化.md) | StaticFetcher 稳定性增强。 |
| [优化/web_fetch - 第7次 优化.md](优化/web_fetch%20-%20第7次%20优化.md) | 多轮追问缓存、文档 URL 静态链路、耗时日志和文档 Markdown 结构化。 |

## 补丁线索

| 文档 | 主题 |
|---|---|
| [补丁/web_fetch - 第1次 补丁.md](补丁/web_fetch%20-%20第1次%20补丁.md) | 非 HTML 和文档解析的第一轮补丁。 |
| [补丁/web_fetch - 第2次 补丁.md](补丁/web_fetch%20-%20第2次%20补丁.md) | 扩展文档格式处理。 |
| [补丁/web_fetch - 第3次 补丁.md](补丁/web_fetch%20-%20第3次%20补丁.md) | 补丁时间线总结。 |

## 追溯提示

如果要了解当前设计，先读“新功能”再读“第1次重构”和“第4次/第5次优化”。如果要追非 HTML 文档处理，直接看补丁目录。
