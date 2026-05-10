# web_fetch 补丁变更记录

## 一、背景与目标

`web_fetch` 模块原有链路：**StaticFetcher → SteelFetcher → LocalScriptFetcher**，只处理 HTML 网页。当抓取到 PDF 等二进制文档时，内容直接丢失，无日志、无降级提示。

补丁目标：让整条抓取链路能**识别并提取二进制文档的文本内容**，同时保持架构简洁、日志可追踪。

## 二、变更时间线

### 第一阶段：打基础补丁（web_fetch补丁.md）

| 改动 | 内容 |
|------|------|
| `static_fetcher.py` | 新增 Content-Type 检测逻辑：`text/*` 和文本友好型 MIME 返回 `str`，文档型 MIME 返回 `bytes`，不支持的类型记录日志并返回 `None` |
| `content_cleaner.py` | 原 `ContentCleaner` 的 `clean()` 方法新增 `bytes` 分支，遇到二进制内容走文档解析路径 |
| `pdf_extractor.py` | 新建 PDF 文本提取模块，用 `pdfplumber` 逐页提取 |
| `fetch_coordinator.py` | 适配 `str | bytes` 返回值，`raw` 类型内容统一经过 `ContentProcessor` 处理 |
| `pyproject.toml` | 添加 `pdfplumber` 依赖 |

关键决策：PDF 处理路径全程打日志（`log_ok` / `log_fail`），确保降级链路可观测。

### 第二阶段：扩展文档格式（web_fetch补丁2.0.md）

补丁 2.0 建议支持 Word / Excel / PowerPoint，但其中部分建议经网络验证后做了调整：

| 补丁建议 | 验证结果 | 最终决策 |
|----------|----------|----------|
| 用 `PyPDF2` 提取 PDF | `PyPDF2` 文本提取能力弱，已停止维护 | 沿用 `pdfplumber` |
| 用 `textract` 做统一解析 | 依赖 C 库（antiword 等），跨平台差 | 按格式分别用 `python-docx` / `openpyxl` / `python-pptx` |
| Content-Type 用 `in` 匹配 | 忽略了 `charset` 参数（如 `text/html; charset=utf-8`） | 改用 `split(";")[0].strip()` 提取 media type |

新增能力：
- 文档类型自动检测：魔数（`%PDF-`、OLE 头）+ ZIP 内部文件名（`word/document.xml` 等）
- ZIP 互斥校验：若 ZIP 内同时包含多种 Office 特征文件（如 `word/document.xml` + `xl/workbook.xml`），不猜测类型，直接 `log_fail` 返回 `None`，堵住构造样本的理论漏洞
- OLE 文档识别：检测到旧版 `.doc/.xls/.ppt` 时明确报错"暂不支持"，而非静默失败
- 安全防护：`_MAX_DOCUMENT_SIZE = 50MB`，防止压缩炸弹

### 第三阶段：架构整合

将 `ContentCleaner` 和 `DocumentParser` 合并为 `ContentProcessor`：

| 合并前 | 合并后 |
|--------|--------|
| `ContentCleaner.clean(str)` → 清洗 HTML | `ContentProcessor.process(str | bytes)` → 统一入口 |
| `DocumentParser.parse(bytes)` → 提取文档文本 | 内部自动分发：`_process_html` / `_process_document` |
| 两个类、两个调用点 | 一个类、一个调用点 |

文件从 `content_cleaner.py` + `pdf_extractor.py` → 合并为 `content_processor.py`。

### 第四阶段：边界场景补全

| 场景 | 问题 | 修复 |
|------|------|------|
| 纯文本响应 | 可能是反爬页面（Cloudflare 验证等），却被当作有效内容 | 新增 `_ANTI_CRAWL_KEYWORDS` 关键词检测，命中则触发降级 |
| Excel 多 Sheet | 提取后丢失 Sheet 上下文，数据混在一起 | 每个 Sheet 前插入 `Sheet: {title}` 标题行 |
| 非 HTML 纯文本 | 短文本（如 404 页面）不应进入 HTML 清洗流程 | 先判断是否以 `<` 开头，非 HTML 直接走纯文本分支 |

### 第五阶段：命名与 code_smell 优化

| 改动 | 原因 |
|------|------|
| `content_cleaner.py` → `content_processor.py` | 类已合并，文件名应与类名一致 |
| `callable` → `Callable` | §12.2：内建函数不是类型注解 |
| `fetch` 方法拆出 `_route_response` | §3：核心方法不超过 30 行 |
| 内部魔法数字提取为常量 | §5：`10`、`500`、`10*1024*1024` 等提取为 `_MAX_DIR_TRAVERSAL`、`_MAX_ERROR_SNIPPET`、`_MAX_SUBPROCESS_BUFFER` |
| 函数签名默认值保留字面量 | 签名本身就是文档，不需要多绕一层常量 |
| 所有模块常量添加行尾注释 | 解释"为什么是这个值"，而非"这个值是什么" |

## 三、最终架构

```
FetchCoordinator
  ├── StaticFetcher          → 返回 str | bytes | None
  ├── SteelFetcher           → 返回 str (markdown) | None
  └── LocalScriptFetcher     → 返回 str (markdown) | None
          │
          ▼
  ContentProcessor.process(str | bytes)
      ├── _process_html()    → readability 提取 → markdownify 转换
      │     ├── 纯文本分支：反爬检测 + 最小长度校验
      │     └── HTML 分支：提取主体 → 转 Markdown
      └── _process_document(bytes)
            ├── 大小检查（50MB 上限）
            ├── _detect_doc_type() → 魔数 + ZIP 内部文件名
            └── _parse_pdf / _parse_docx / _parse_xlsx / _parse_pptx
```

## 四、最终效果

| 维度 | 效果 |
|------|------|
| 功能 | 抓取链路从"只认 HTML"扩展为"HTML + PDF + Word + Excel + PPT"，5 种格式全覆盖 |
| 可观测性 | 每个关键节点都有 `log_ok` / `log_fail` 日志，PDF 处理路径全程可追踪 |
| 安全性 | 50MB 文档上限防压缩炸弹，OLE 旧格式明确报错而非静默失败 |
| 健壮性 | 反爬页面检测、短内容降级、异常兜底返回原文，整条链路无裸异常 |
| 可维护性 | 单一 `ContentProcessor` 统一入口，新增文档格式只需加一个 `_parse_xxx` 方法并注册到字典 |
| 代码质量 | 无魔法数字、方法职责单一不超过 30 行、类型注解规范、常量有注释 |
