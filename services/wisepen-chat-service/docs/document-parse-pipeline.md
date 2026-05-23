# Document Parse Pipeline

本文档描述当前 `document_parse` 的真实执行链路，覆盖工具入口、文件类型路由、各 parser 的处理方式、统一输出模型和错误边界。

## 入口

`document_parse` 工具只接受 `file_ref`：

- 入口类：`src/chat/application/tools/document/document_parse_tool.py`
- 服务类：`src/chat/application/tools/services/document_parse/document_parse_service.py`
- Provider：`src/chat/container_providers/document_parse.py`
- 构建函数：`src/chat/application/tools/services/document_parse/factory.py`

工具执行步骤：

1. 校验 `session_id`、`user_id` 和非空 `file_refs`。
2. 拒绝 `cnt_*` content id 和 `http://` / `https://` URL。
3. 通过 `DocumentTempFileResolver` 把每个 `file_ref` 解析为本地文件路径。
4. 调用 `DocumentParseService.parse_many()` 批量解析。
5. 成功结果写入 ToolContent cache，返回 `content_id` 和预览文本。

批量解析并发度固定为 `_PARSE_CONCURRENCY = 4`。单个文件失败不会中断同批次其他文件。

## 支持格式

文件类型只按后缀判断，入口在 `suffixes.py`。

| 类型 | 后缀 | Parser |
| --- | --- | --- |
| PDF | `.pdf` | `PdfParser` |
| Office 文档 | `.docx`, `.docm` | `OfficeParser` |
| Office 演示文稿 | `.pptx`, `.pptm` | `OfficeParser` |
| EPUB | `.epub` | `EpubParser` |
| Spreadsheet | `.xlsx`, `.xls`, `.xlsm`, `.ods` | `SpreadsheetParser` |

明确不走 `document_parse` 的格式包括 HTML、TXT、MD、CSV、JSON、XML、图片、音频和视频。它们会抛出 `UnsupportedDocumentFormatError`，并给出相应 guidance。

## 统一输出

所有 parser 返回 `DocumentParseResult`：

```python
DocumentParseResult(
    text: str,
    source: str,
    file_type: str,
    pages: List[ParsedPage],
    tables: List[ParsedTable],
    metadata: Dict[str, Any],
    warnings: List[str],
)
```

`DocumentParseService.parse_path()` 会在 parser 返回后检查 `result.text.strip()`。如果为空，抛出 `EmptyParsedContentError`。

`ParsedPage` 记录页级文本、页类型、页级表格和 metadata。`ParsedTable` 是统一模型，目前 Spreadsheet 会生成结构化表格；PDF 和 Office 不生成结构化表格。

## PDF 链路

PDF 当前固定为：

```text
PDF
  primary: marker-pdf
    -> 输出 markdown 文本
    -> 文本长度 >= PDF_MARKER_MIN_TEXT_CHARS 时直接返回

  fallback: PyMuPDF
    -> PageClassifier 判断 text / mixed / scanned / empty
    -> text/mixed 页用 PyMuPDF 文本抽取
    -> text/mixed 页 best-effort 调用 page.find_tables() 并转成 markdown 表格文本
    -> scanned 页用 PageRenderer 渲染图片
    -> OcrImageAdapter 做 OCR
    -> scanned 页 best-effort 调用 PP-Structure 并转成 markdown 表格文本
```

### marker primary

实现文件：

- `pdf/parser.py`
- `pdf/marker_extractor.py`

`MarkerPdfExtractor` 固定调用：

```python
PdfConverter(artifact_dict=create_model_dict())
rendered = converter(str(path))
text, _, images = text_from_rendered(rendered)
```

策略：

- marker 始终是 PDF primary parser。
- 输出固定按 markdown 文本使用。
- marker 成功且文本长度达到 `PDF_MARKER_MIN_TEXT_CHARS` 时，直接返回。
- marker 抛异常、输出为空或输出过短时，进入 PyMuPDF fallback。
- marker metadata 会放入 `metadata["marker_metadata"]`。
- marker 成功时 `pages` 用单个 `ParsedPage` 包装完整 markdown，`page_type="marker"`。
- marker 输出中的 markdown 表格作为正文保留，不再二次解析成 `ParsedTable`。

marker 成功 metadata 关键字段：

```text
parser = "PdfParser"
selected_parser = "PdfParser"
pdf_backend = "marker-pdf"
fallback_used = False
page_count = fitz 读取到的页数，失败则为 None
parsed_page_count = page_count 或 1
marker_metadata = marker adapter 返回的 metadata
```

### PyMuPDF fallback

实现文件：

- `pdf/parser.py`
- `pdf/page_classifier.py`
- `pdf/text_extractor.py`
- `pdf/page_renderer.py`
- `tools/common/ocr/image_adapter.py`

fallback 流程：

1. 用 `fitz.open()` 获取总页数。
2. 最多解析 `PDF_MAX_PAGES` 页，超出时追加 `page_truncated` warning。
3. 每页先由 `PageClassifier.probe_page()` 分类。
4. `text` / `mixed` 页使用 `TextExtractor.extract_page_text_from_page()`。
5. `text` / `mixed` 页直接尝试 `page.find_tables()`，把 `table.extract()` rows 转成 markdown 表格文本并追加到当前页文本。
6. `scanned` 页进入扫描页阶段。
7. `empty` 页保留空文本页。
8. 汇总页文本并返回 `DocumentParseResult`。

`PageClassifier` 只判断页类型：

- `text`：文本长度达到 `PDF_PAGE_MIN_TEXT_CHARS`，且不是 mixed。
- `mixed`：文本不足但有少量文本，或文本页同时含图片且 `PDF_ENABLE_MIXED_PAGE_TYPE=True`。
- `scanned`：文本不足，图片面积占比达到 `PDF_SCANNED_IMAGE_AREA_RATIO`，或图片面积无法计算但存在图片。
- `empty`：无有效文本且无扫描页信号。

扫描页 OCR：

- 只在 PyMuPDF fallback 中运行。
- `PageRenderer` 按 `PDF_RENDER_DPI` 渲染页面 PNG。
- 渲染前后都会检查 `PDF_MAX_IMAGE_PIXELS`。
- `OcrImageAdapter` 调用共享 `OcrProcessor.recognize_image()`。
- OCR 最大页数由 `PDF_SCANNED_OCR_MAX_PAGES` 限制。
- OCR 并发由 `PDF_SCANNED_OCR_CONCURRENCY` 控制。
- 已渲染的 scanned 页会 best-effort 调用 PP-Structure。
- PP-Structure 抽到的 HTML table 会转成 markdown 表格文本并追加到当前页文本。
- PP-Structure 失败只追加 warning/log，不再进入额外 fallback。

PDF fallback 表格策略：

- 表格文本必须进入 `ParsedPage.text` 和 `DocumentParseResult.text`。
- PyMuPDF `page.find_tables()` 和 PP-Structure 都只作为轻量文本增强。
- 当前实现会为直接抽到的 fallback 表格生成 minimal `ParsedTable`，用于兼容统一模型。
- 不维护 table backend 优先级、候选页状态、质量评分或批处理调度。

fallback metadata 关键字段：

```text
parser = "PdfParser"
selected_parser = "PdfParser"
pdf_backend = "pymupdf"
fallback_used = True
ocr_backend = "paddleocr"
page_type_counts = 各 page_type 计数
page_count = PDF 总页数
parsed_page_count = 实际解析页数
pdf_parse_metrics = fallback 阶段指标
```

PDF 不再包含以下内容：

- Camelot
- lattice / stream flavor 分支
- table candidate 状态机
- scanned table candidate gate
- 表格质量评分
- 表格 batch 调度
- PDF table backend metadata
- PDF table metrics

## Office 链路

Office 当前固定为：

```text
DOCX/DOCM/PPTX/PPTM
  primary: Docling
  fallback: MarkItDown
```

实现文件：

- `office/parser.py`
- `office/primary_parser.py`
- `office/fallback_parser.py`

### Docling primary

`OfficePrimaryParser` 懒加载并复用 `docling.document_converter.DocumentConverter`。

处理方式：

1. `converter.convert(str(path))`
2. `result.document.export_to_markdown()`
3. normalize markdown 文本
4. 返回单个 `ParsedPage(page_type="document")`

Docling primary 不生成结构化 `ParsedTable`，`tables=[]`。

### MarkItDown fallback

`OfficeFallbackParser` 懒加载并复用 `MarkItDown`。

处理方式：

1. `converter.convert(str(path))`
2. 读取 `result.text_content`
3. normalize 文本
4. 返回单个 `ParsedPage(page_type="document")`

MarkItDown fallback 不生成结构化 `ParsedTable`，`tables=[]`。

### Office metadata

`OfficeParser` 会把最终结果包装为：

```text
parser = "OfficeParser"
selected_parser = "docling" 或 "markitdown"
fallback_chain = ["docling", "markitdown"]
page_count = len(result.pages)
table_count = len(result.tables)
```

如果 Docling 和 MarkItDown 都失败，抛出 `DocumentParseError`，错误信息包含两个阶段的 warning。

## EPUB 链路

EPUB 使用 MarkItDown 单链路：

```text
EPUB
  -> MarkItDown
  -> text_content
  -> normalize
```

实现文件：`epub/parser.py`

输出：

- `file_type="epub"`
- 单个 `ParsedPage(page_type="document")`
- `tables=[]`
- `metadata.parser="markitdown"`
- `metadata.selected_parser="markitdown"`

如果 MarkItDown 输出空文本，抛出 `EmptyParsedContentError`。

## Spreadsheet 链路

Spreadsheet 使用 pandas：

```text
XLSX/XLS/XLSM/ODS
  -> pandas.read_excel(sheet_name=None, dtype=object, keep_default_na=False)
  -> 每个 sheet 转为 TSV fenced block
  -> 每个 sheet 生成一个 ParsedTable
```

实现文件：`spreadsheet/parser.py`

处理方式：

1. 读取所有 sheet。
2. 每个 sheet 的列名作为第一行。
3. 单元格格式化：
   - `None` 转空字符串。
   - 连续空白规整。
   - 单元格内换行转 `" / "`。
4. 文本输出为：

````markdown
## Sheet: SheetName

```tsv
col1    col2
...
```
````

5. 每个 sheet 生成一个 `ParsedTable(source="pandas")`。

metadata 关键字段：

```text
parser = "SpreadsheetParser"
selected_parser = "pandas"
spreadsheet_backend = "pandas"
sheet_count = sheet 数量
sheets = 每个 sheet 的 name / rows / columns
page_count = 1
table_count = sheet 数量
```

## 错误处理

`DocumentParseService.parse_many()` 对单文件错误做隔离：

- `FileNotFoundError`：返回失败项。
- `DocumentParseError`：返回失败项。
- `FrozenInstanceError`：返回明确的内部 parser 状态变更错误。
- 其他异常：记录 unexpected error，返回 `未预期异常: <ExceptionClass>`。

`DocumentParseTool` 会把失败项格式化为：

```text
--- File: <name> ---
[Parse Error] <error>
```

## 依赖关系摘要

当前 document_parse 构建关系：

```text
build_document_parse_service(local_ocr_processor)
  -> PdfParser(
       MarkerPdfExtractor,
       PageClassifier,
       TextExtractor,
       PageRenderer,
       OcrImageAdapter
     )
  -> OfficeParser(
       OfficePrimaryParser(Docling),
       OfficeFallbackParser(MarkItDown)
     )
  -> EpubParser(MarkItDown)
  -> SpreadsheetParser(pandas)
```

OCR processor 是共享组件，由 container provider 构建并注入到 `OcrImageAdapter`。
