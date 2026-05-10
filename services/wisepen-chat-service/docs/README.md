# wisepen-chat-service 文档索引

本文档用于把整理后的 Markdown 文档串成一条大致可追溯的工程线索。由于整理时没有完整参与每一次开发，本索引只做保守归纳：按现有文档内容、文件名、目录和上下文标出阅读顺序，不强行补充不存在的结论。

## 使用方式

优先按功能目录阅读：

| 功能 | 入口 | 说明 |
|---|---|---|
| browse_interact | [browse_interact/README.md](browse_interact/README.md) | 浏览器交互、snapshot/ref、协议与会话清理。 |
| web_fetch | [web_fetch/README.md](web_fetch/README.md) | 网页抓取、降级链、反爬脚本、内容处理与补丁。 |
| web_search | [web_search/README.md](web_search/README.md) | 搜索工具、搜索协调器、多引擎、缓存、日志与 Tavily 子模块。 |
| rag_tool | [rag_tool/README.md](rag_tool/README.md) | RAG 首次设计与后续重构。 |
| note_tool | [note_tool/README.md](note_tool/README.md) | NoteTool 首次设计。 |
| webfetch_cli | [webfetch_cli/README.md](webfetch_cli/README.md) | WebFetch CLI 独立工具说明。 |

总规范：

- [code_style.md](code_style.md)：工程协作与代码风格总规范。
- [整理报告.md](整理报告.md)：文档整理过程、删除/保留依据、目录变更记录。

## 追溯规则

每个功能目录按以下顺序理解：

1. `新功能/`：首次设计或首次接入，回答“为什么要做、最初怎么设计”。
2. `重构/`：架构边界、目录职责、协议或抽象调整，回答“为什么改结构”。
3. `优化/`：性能、稳定性、日志、测试增强、工程成熟度改进，回答“哪里变得更稳或更清晰”。
4. `补丁/`：针对明确问题或短期缺口的修补，回答“当时修了什么洞”。
5. `修复/`：明确 bug 或异常路径修复，回答“哪个问题被修掉了”。

文件名中的“第 n 次”仅表示该功能在同一类型下的大致迭代顺序。若需要判断当前结论，优先看同目录中序号更靠后的文档，再结合功能 README 的“当前可用结论”。

## 当前有效线索

| 功能 | 当前主要结论 |
|---|---|
| browse_interact | 以本地 Playwright、单 action、snapshot/ref、显式错误协议和精简会话状态为主线。 |
| web_fetch | 以 WebFetchTool + FetchCoordinator 降级链、ContentCleaner、Static/Steel/Local fetcher 分层为主线。 |
| web_search | 以 WebSearchTool + SearchCoordinator、多 searcher/stage、缓存、日志增强和 Tavily 子模块隔离为主线。 |
| rag_tool | 先有混合检索增强设计，后续重构侧重元数据解耦。 |
| note_tool | 当前只有首次设计文档，暂无后续重构线索。 |
| webfetch_cli | 当前只有独立 CLI 首次说明，暂无后续迭代线索。 |
