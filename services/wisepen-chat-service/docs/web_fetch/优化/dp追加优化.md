
```text
 文档解析：让 PDF / Office / Spreadsheet / EPUB 的正文质量更好
```

---

# 总原则

继续做：

```text
1. 提升内容完整性
2. 提升阅读顺序
3. 提升表格可读性
4. 减少乱码 / 误删 / 误判空文档
5. 降低模型续读时断上下文的问题
```

不做：

```text
1. 不新增架构层
2. 不改 ToolContentStore 协议
3. 不做失败信息优化
4. 不做错误码
5. 不做开发者诊断体验
6. 不让 web_fetch 和 document_parse 职责回流
7. 不新增重依赖
```

---

# 一、文档解析可以增强什么

## P0：PDF 文本提取使用 `sort=True`

PyMuPDF 官方文档说明，`Page.get_text("text")` 默认按 PDF 创建者存储顺序提取，可能不是自然阅读顺序；可以用 `page.get_text("text", sort=True)` 按 top-left 到 bottom-right 做重排。这个对 PDF 用户体验影响非常直接。([PyMuPDF 文档][1])

当前 `TextExtractor` 类似：

```python
text = page.get_text("text")
```

建议改成：

```python
text = page.get_text("text", sort=True)
```

收益：

```text
1. 多栏 PDF 阅读顺序更接近人类阅读
2. 页眉页脚乱入正文的概率降低
3. 提取后的 Markdown 更容易被模型理解
```

风险：

```text
极少数特殊布局 PDF 可能原始顺序更好，但大多数用户更希望自然阅读顺序。
```

这是我认为 **document_parse P0 第一项**。

---

## P0：Spreadsheet 使用 `keep_default_na=False`

pandas 官方文档说明：`keep_default_na=False` 且未指定 `na_values` 时，不会把默认 NA 字符串解析为 NaN。([Pandas][2])

当前如果 Excel 单元格里真实写了：

```text
NA
N/A
NULL
```

pandas 可能会当成缺失值，然后你后面 `fillna("")` 会把它变成空字符串。用户侧表现就是“表格内容丢了”。

建议：

```python
sheets = pd.read_excel(
    path,
    sheet_name=None,
    dtype=object,
    keep_default_na=False,
)
```

收益：

```text
1. 保留 NA / N/A / NULL 等真实文本
2. 减少表格内容被误清空
3. 对 spreadsheet 用户体验提升明显
```

这是 **Spreadsheet P0**。

---

## P0：Spreadsheet 单元格文本做 TSV 安全清洗

现在表格输出是：

```python
"\t".join(row)
```

如果单元格里有：

```text
换行
制表符
连续空白
```

Markdown 的 TSV code block 会变乱，模型读表会困难。

建议每个 cell 输出前做轻量清洗：

```python
def _format_cell(value: object) -> str:
    text = str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", " / ", text)
    return text.strip()
```

注意：这只影响 `result.text` 的可读输出。`ParsedTable.rows` 也可以用同样清洗后的文本，因为模型消费结构化 rows 时也不希望 cell 里嵌套换行破坏表格。

收益：

```text
1. 表格 Markdown 更稳定
2. 多行单元格不会打断行结构
3. 模型读表准确性更好
```

不需要新依赖。

---

## P1：Camelot 先 lattice，空结果再 stream

Camelot 官方 API 里 `read_pdf()` 默认 `flavor='lattice'`，官方 quickstart 也说明可以用 `flavor='stream'` 处理另一类表格。([Camelot][3]) ([Camelot][4])

当前如果只用默认 lattice，会漏掉无边框表格。建议：

```text
1. 先 camelot.read_pdf(..., flavor="lattice")
2. 如果没有表格，再 camelot.read_pdf(..., flavor="stream")
3. 不同时合并 lattice + stream，避免重复表格
```

收益：

```text
1. 无边框 PDF 表格提取率提升
2. 不引入新依赖
3. 不改变 parser 架构
```

风险：

```text
stream 有时会误检，所以只在 lattice 结果为空时 fallback。
```

这个是 P1，因为它会增加每页表格提取成本，但用户可感知收益比较明显。

---

## P1：DOCX native fallback 保持段落和表格顺序

当前 native fallback 如果还是：

```text
先读所有 paragraphs
再读所有 tables
```

就会打乱 Word 原始结构。虽然主路径有 Docling / MarkItDown，但 fallback 一旦生效，用户会看到顺序错乱。

python-docx 官方表格 API 能拿到 cell 内 paragraphs / tables 等内容，但文档对象层面想保持段落和表格原始顺序通常需要遍历 body XML children；这不是新增依赖，只是 native fallback 的质量增强。([python-docx.readthedocs.io][5])

建议作为 P1：

```text
1. 只改 OfficeNativeParser._parse_docx
2. 遍历 document.element.body.iterchildren()
3. 遇到 paragraph 输出段落
4. 遇到 table 输出表格
5. 不处理图片
6. 不改变主链路
```

收益：

```text
Docling / MarkItDown 都失败时，native fallback 仍然更像原文顺序。
```

这个不是第一优先级，因为它只影响 fallback。

---

## P1：PDF mixed 页面不要直接整页 OCR 替代文本层

如果 PageClassifier 已经有：

```text
text
mixed
scanned
empty
```

要确认 `PdfParser` 对 mixed 页的处理。

理想策略：

```text
text 页：
  PyMuPDF text + Camelot table

mixed 页：
  PyMuPDF text + Camelot table
  必要时再 OCR，但不要直接用 OCR 替代已有文本层

scanned 页：
  render + OCR + PP-Structure

empty 页：
  可跳过或返回空页标题
```

如果现在 mixed 走 scanned 路径，会有两个问题：

```text
1. OCR 可能比原生文本层差
2. 原生可复制文本被浪费
```

这个是用户效果项，但需要看当前 parser 逻辑是否已处理。若已处理，不动；若 mixed 直接 OCR，则值得改。

---

# 三、不建议做的增强

这些不符合你当前规则：

```text
1. 不做新的 document_parse result 协议
2. 不做 disk-backed ToolContentStore
3. 不引入新 OCR / layout 模型
4. 不引入 unstructured / mammoth / pdfplumber 等新依赖
5. 不做完整 Markdown AST chunker
6. 不做复杂错误分类
7. 不做解析失败报告优化
8. 不做开发者日志增强
9. 不做自动摘要
10. 不让 document_parse 自动联网下载
```

---

# 我推荐的最终落地顺序

## 第一轮：最小收益最大

```text
1. ToolContentStore 分段改为确定性原文 offset 切分
2. PyMuPDF text extraction 改为 sort=True
3. Spreadsheet read_excel 增加 keep_default_na=False
4. Spreadsheet cell 输出做 TSV 安全清洗
```

这四项都是用户直接受益，风险低，不改架构，不加依赖。

## 第二轮：PDF / Office 质量增强

```text
5. Camelot lattice 为空时 fallback stream
6. PDF mixed 页优先保留文本层
7. DOCX native fallback 保持段落 / 表格原始顺序
```

## 暂缓

```text
1. 分段 overlap
2. disk-backed oversized content cache
3. PPTX speaker notes
4. RSS / GitHub / sitemap 类特化
5. 新依赖
```

---

# 给 Codex 的提示词

```text
请在不调整架构、不新增第三方依赖、不优化开发者诊断体验的前提下，只做分段读取和 document_parse 的用户侧效果增强。

目标：
1. 提升长内容续读稳定性。
2. 提升 PDF 文本阅读顺序。
3. 提升 Excel/ODS 表格内容保真和可读性。
4. 提升 PDF 表格提取覆盖率。
5. 提升 fallback 解析时的原文顺序质量。

禁止：
1. 不新增依赖。
2. 不改 ToolContentStore 协议。
3. 不新增错误码。
4. 不做失败信息优化。
5. 不改 web_fetch / document_parse 职责边界。
6. 不让 document_parse 下载 URL。
7. 不新增架构层。
8. 不引入 disk-backed cache。
9. 不做自动摘要。

优先实现：

一、PDF 文本阅读顺序
- TextExtractor.extract_page_text 使用：
  page.get_text("text", sort=True)
- 不改变 OCR 逻辑。
- 不改变 PDF parser 架构。
- 目标：PDF 文本更接近自然阅读顺序。

二、Spreadsheet 保真和 TSV 可读性
- pd.read_excel 增加 keep_default_na=False。
- 保留 dtype=object。
- 单元格输出前做轻量格式化：
  - 统一换行
  - tab 替换为空格
  - 单元格内部换行替换为 " / "
  - strip 外层空白
- rows 和 text 输出使用同一清洗结果。
- 不截断行列。
- 不新增依赖。
- 目标：NA / N/A / NULL 不被误清空，多行单元格不破坏 TSV 结构。

三、Camelot 表格提取 fallback
- TableExtractor 先尝试 flavor="lattice"。
- 如果 lattice 没有提取到任何有效 rows，再尝试 flavor="stream"。
- 不合并 lattice 和 stream 结果，避免重复表格。
- metadata 中可以保留 flavor，但不要新增错误码。
- 不引入新库。
- 目标：提升无边框 PDF 表格提取率。

四、PDF mixed 页检查
- 检查 PdfParser 对 PAGE_TYPE_MIXED 的处理。
- 如果 mixed 页当前直接走扫描页 OCR，请改为优先使用文本层：
  - TextExtractor + TableExtractor
  - 不要用 OCR 替代已有文本层
- scanned 页仍然 OCR。
- text 页逻辑不变。
- 如果当前已经这样处理，只报告无需修改。

五、DOCX native fallback 顺序
- 检查 OfficeNativeParser._parse_docx。
- 如果当前先 paragraphs 后 tables，会打乱原文顺序。
- 在不新增依赖的前提下，遍历 document.element.body.iterchildren()，按 body 顺序输出 paragraph / table。
- 不处理图片。
- 不改变 Docling / MarkItDown 主链路。
- 只增强 native fallback。

验收：
1. python -m compileall src/chat/application/tool_content_store.py src/chat/application/document_parse
2. rg "response\\.text|force_browser|force_ocr" 不相关，不用处理。
3. ToolContentStore 协议字段不变。
4. document_parse 不下载 URL。
5. web_fetch 不受影响。
6. Spreadsheet 中字符串 NA / N/A / NULL 不被清空。
7. PDF TextExtractor 使用 sort=True。
8. Camelot stream 只在 lattice 无有效表格时执行。
9. DOCX native fallback 输出顺序不再是全部段落后全部表格。
```

---

## 最终建议

你现在最应该做的是这三个：

```text
1. PDF PyMuPDF sort=True
2. Spreadsheet keep_default_na=False
3. Spreadsheet cell TSV 安全清洗
```

这三项收益最直接，风险最低，完全符合你给 web_fetch 定下的加强规则。

[1]: https://pymupdf.readthedocs.io/en/latest/app1.html?utm_source=chatgpt.com "Appendix 1: Details on Text Extraction - PyMuPDF"
[2]: https://pandas.pydata.org/docs/reference/api/pandas.read_excel.html?utm_source=chatgpt.com "pandas.read_excel — pandas 3.0.2 documentation - PyData |"
[3]: https://camelot-py.readthedocs.io/en/master/api.html?utm_source=chatgpt.com "API Reference — Camelot 1.0.9 documentation"
[4]: https://camelot-py.readthedocs.io/en/master/user/quickstart.html?utm_source=chatgpt.com "Quickstart — Camelot 1.0.9 documentation - Read the Docs"
[5]: https://python-docx.readthedocs.io/en/latest/api/table.html?utm_source=chatgpt.com "Table objects — python-docx 1.2.0 documentation"
