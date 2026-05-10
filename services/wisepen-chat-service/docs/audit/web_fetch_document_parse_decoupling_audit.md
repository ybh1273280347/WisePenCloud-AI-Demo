# Web Fetch / Document Parse 解耦排查报告

## 结论
- FAIL
- 阻塞问题数量：1
- 非阻塞问题数量：6

## P0 阻塞问题
| 编号 | 问题 | 文件 | 证据 | 建议修复方向 |
|---|---|---|---|---|
| P0-1 | 文件直链二进制流没有独立 handoff 路径，会被当成静态抓取失败并继续触发浏览器 fallback。 | `src/chat/application/web_fetch/fetcher/static_fetcher.py`; `src/chat/application/web_fetch/fetch_coordinator.py`; `src/chat/application/web_fetch/content_processor.py`; `src/chat/application/tools/web_fetch_tool.py` | `StaticFetcher` 对 document-like 响应返回 `bytes`；`FetchCoordinator` 对 static 链路调用 `ContentProcessor.process_async(content)`；`ContentProcessor.process(bytes)` 直接返回 `None`；随后 `FetchCoordinator` 记录“内容处理失败，触发降级”并继续 Steel / LocalScript fallback。`WebFetchTool` 只缓存 `content_kind=web_page` 的 markdown，没有返回 `file_ref` 或 binary handoff。 | 为文档直链引入显式二进制结果类型和文件缓存/引用输出，例如 `file_ref`；`FetchCoordinator` 识别文档二进制后立即返回 handoff 结果或明确终止，不再进入 `ContentProcessor`，也不再触发浏览器 fallback。 |

## P1 需要修复
| 编号 | 问题 | 文件 | 证据 | 建议修复方向 |
|---|---|---|---|---|
| P1-1 | 真实 web_fetch 测试脚本仍停留在旧架构和旧边界，会误导后续验证。 | `test/test_web_fetch_real.py` | 仍 import 不存在的 `FetchCoordinatorConfig`，并构造 `FetchCoordinator(FetchCoordinatorConfig(...))`；测试用例仍把 PDF / DOCX / XLSX / PPTX 文档直链放在 web_fetch 成功路径中。 | 重写该脚本：web_fetch 只验证 HTML / text-like / JSON / XML / CSV 等网络抓取和自动 fallback；文档直链只验证 binary handoff 或明确拒绝；文档内容解析移动到 document_parse 测试。 |
| P1-2 | document_parse metadata 可定位性还不够。 | `src/chat/application/document_parse/pdf/pdf_parser.py`; `src/chat/application/document_parse/spreadsheet/pandas_spreadsheet_parser.py` | `PdfParser` metadata 只有 `parser=pymupdf` 和 `page_count`；缺少建议字段 `pdf_backend`、`table_backends`、`ocr_backend`、`page_type_counts`。Spreadsheet metadata 只有 `parser=pandas`，缺少 `sheet_count`。 | PDF parser 汇总页面分类计数和后端字段；spreadsheet parser 写入 `sheet_count`。保持 `DocumentParseTool` 现有 `file_type/source/page_count/table_count/warnings` 汇总格式。 |
| P1-3 | document_parse OCR 配置仍使用 `WEB_FETCH_OCR_*` 命名，职责边界在配置层不清晰。 | `src/chat/container.py`; `src/chat/application/document_parse/factory.py` | `document_parse_ocr_processor` 使用 `settings.WEB_FETCH_OCR_TIMEOUT_SECONDS`、`WEB_FETCH_ENABLE_OCR`；PDF renderer 使用 `WEB_FETCH_OCR_RENDER_DPI`、`WEB_FETCH_OCR_MAX_IMAGE_PIXELS`。 | 新增或迁移为 `DOCUMENT_PARSE_OCR_*` / `DOCUMENT_PARSE_PDF_RENDER_*` 配置名，保留兼容别名即可，避免后续误以为 OCR 仍属于 web_fetch。 |

## P2 可后续优化
| 编号 | 问题 | 文件 | 证据 | 建议修复方向 |
|---|---|---|---|---|
| P2-1 | `__all__` 仍广泛存在，不符合本轮文档中的新风格约束。 | `src/chat/application/web_fetch/__init__.py`; `src/chat/application/document_parse/__init__.py`; `src/chat/application/document_parse/pdf/__init__.py`; `src/chat/application/tool_content_store.py`; others | `rg "__all__" src/chat/application` 有多处命中，包括新增 document_parse 和 tool_content_store 模块。 | 若团队确认该约束生效，后续单独做机械清理；不要和 P0 修复混在一起。 |
| P2-2 | `force_ocr` / `force_browser` 字面量仍出现在测试断言中。 | `test/test_web_fetch_ocr_e2e.py`; `test/test_web_fetch_security_unit.py` | 测试通过断言确认 schema 不包含 `force_ocr` / `force_browser`；这不是生产残留，但按排查文档的严格口径，测试断言中也不应残留旧参数名。 | 可改成更抽象的 schema 快照断言或保留现状并在排查标准中明确“负向断言允许出现”。 |
| P2-3 | ToolContentStore 协议行为通过，但当前文件是未跟踪新增文件，无法用 `git diff -- src/chat/application/tool_content_store.py` 证明“未变更”。 | `src/chat/application/tool_content_store.py` | `git status --short` 显示该文件为 `??`；协议单测通过，`WebFetchTool` 和 `DocumentParseTool` 均通过 `cache_and_window` + `format_windowed_content` 返回。 | 在该轮代码正式落库后再做一次基线 diff；当前不视为行为阻塞。 |

## 已确认通过项
- web_fetch 生产代码未命中 `force_browser` / `force_ocr`；`WebFetchTool` schema 只有 `url`。
- web_fetch 生产代码未 import / 调用 `DocumentParser`、`LocalOcrProcessor`、`OcrResult`、Docling、PyMuPDF、Camelot、PP-Structure、PaddleOCR。
- `FetchCoordinator.fetch` 签名为 `fetch(self, url: str)`；自动链路为 `StaticFetcher -> SteelFetcher -> LocalScriptFetcher`；缓存 key 只使用 `url`。
- `UrlSecurityError` 在入口和 fetcher 链路中直接抛出/返回 Tool Error，不会继续 fallback；redirect 后也会重新校验 URL。
- document_parse 主路由只按后缀分发：PDF、DOCX/PPTX、EPUB、XLSX/XLS/ODS；HTML / TXT / MD / CSV / JSON / XML / 图片 / 音视频均不在支持后缀内。
- document_parse 没有反向 import web_fetch；`LocalDocumentFileResolver` 明确拒绝 http/https URL 下载。
- PDF parser 目录未命中 Docling / MarkItDown / 旧 DocumentParser / pdfplumber；使用 PyMuPDF 逐页分类、文字页 PyMuPDF + Camelot、扫描页渲染后 PaddleOCR + PP-Structure。
- PP-Structure 表格抽取失败会追加 `page_N_table_parse_failed` warning，并保留已获得的 OCR 文本。
- Office parser 通过主路由只处理 DOCX / PPTX；未配置 Docling PDF pipeline，未启用 OCR，未处理 XLSX。
- EPUB 走 MarkItDown；XLSX / XLS / ODS 走 pandas；未新增 textract / tika / pdf2image / ResourceManager。
- ToolContentStore 格式包含 `[ToolContent Metadata]`、`content_id`、`content_cached`、`truncated`、`next_offset`；`web_fetch` 和 `document_parse` 均走 `cache_and_window` + `format_windowed_content`。

## 本轮执行的验证
- `python` AST parse：`src/chat/application/web_fetch`、`src/chat/application/document_parse`、`src/chat/application/tools/web_fetch_tool.py`、`test` 均通过。
- `uv run python test/test_web_fetch_security_unit.py`：8/8 passed。
- `uv run python test/test_web_fetch_ocr_e2e.py`：PASS。
- `uv run python test/test_doc_parse.py`：PASS。
- `PYTHONIOENCODING=utf-8 uv run python test/test_tool_content_read_unit.py`：PASS。
- 未跑真实网络 E2E；该类用例依赖外网、Steel 服务和旧测试脚本修复。
