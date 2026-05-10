## 结论

`pdf/parser.py` 是目前 review 到现在最需要认真改的文件。整体结构是对的：**PDF 总控负责页面循环、页面分类、文字页 / 扫描页分流、warnings、metadata 汇总**。这符合我们定的 PDF 管线。

但当前有一个明显逻辑问题：

```text
当前代码把所有非 "text" 页面都当成 scanned 页处理。
```

也就是说：

```text
mixed → 被 OCR
empty → 被 OCR
```

这和你最初定的“页面级判断”不一致。这里要优先修。PyMuPDF 本身是用于 PDF 文档提取、分析和转换的库，官方也提供了 `Page.get_text("text")` 等页面文本提取能力；PDF 总控层应该尊重页面分类结果，而不是把 mixed / empty 粗暴归入扫描页。([PyMuPDF 文档][1])

---

## 第三方库 API 确认

这个文件直接使用第三方库的地方只有：

```python
import fitz
with fitz.open(str(path)) as doc:
    return len(doc)
```

这属于 PyMuPDF 的基础文档打开 / 计页能力。当前写法可以接受。后续若统一到新版官方推荐导入名，可以考虑 `import pymupdf`，但这会影响其他 PDF 子模块，不在当前文件单独改。

PDF 子模块实际依赖的 PyMuPDF / Camelot / PaddleOCR / PP-Structure 都是通过注入的 extractor / adapter 调用，这个文件不用直接校验它们的 API。

---

# 我确定要改的点

## 1. `format_tables` 改成内部函数

当前：

```python
def format_tables(tables: List[ParsedTable]) -> str:
```

它只在当前模块内部使用，应该改成：

```python
def _format_tables(tables: List[ParsedTable]) -> str:
```

并同步：

```python
table_text = _format_tables(tables)
```

---

## 2. 补全 typing

当前 `__init__` 依赖没有类型标注：

```python
def __init__(
    self,
    *,
    classifier,
    text_extractor,
    page_renderer,
    table_extractor,
    ocr_adapter,
    scanned_table_extractor,
):
```

按项目风格，先用 `Any`：

```python
from typing import Any, Dict, List, Tuple
```

```python
classifier: Any
text_extractor: Any
page_renderer: Any
table_extractor: Any
ocr_adapter: Any
scanned_table_extractor: Any
```

后续如果这些组件稳定成 public protocol，再替换成具体类型。

---

## 3. 抽内部常量

当前散落了很多稳定字符串：

```python
"text"
"mixed"
"scanned"
"empty"
"pymupdf"
"paddleocr"
"camelot"
"pp_structure"
"none"
"PdfParser"
"pdf"
```

建议加：

```python
_PARSER_NAME = "PdfParser"
_FILE_TYPE_PDF = "pdf"

_PAGE_TYPE_TEXT = "text"
_PAGE_TYPE_MIXED = "mixed"
_PAGE_TYPE_SCANNED = "scanned"
_PAGE_TYPE_EMPTY = "empty"

_TEXT_BACKEND_PYMUPDF = "pymupdf"
_TEXT_BACKEND_PADDLEOCR = "paddleocr"
_TABLE_BACKEND_CAMELOT = "camelot"
_TABLE_BACKEND_PP_STRUCTURE = "pp_structure"
_BACKEND_NONE = "none"
```

这样后面分流逻辑和 metadata 不会到处散字符串。

---

## 4. 修正页面分流：`mixed` 不应 OCR，`empty` 不应 OCR

当前：

```python
if page_type == "text":
    page_text, page_tables = self._parse_text_page(path, page_index=page_index)
else:
    page_text, page_tables = await self._parse_scanned_page(...)
```

建议改成：

```python
if page_type in {_PAGE_TYPE_TEXT, _PAGE_TYPE_MIXED}:
    page_text, page_tables = self._parse_text_page(path, page_index=page_index)
elif page_type == _PAGE_TYPE_SCANNED:
    page_text, page_tables = await self._parse_scanned_page(
        path,
        page_index=page_index,
        render_dir=render_dir,
        warnings=warnings,
    )
elif page_type == _PAGE_TYPE_EMPTY:
    page_text, page_tables = "", []
else:
    raise ValueError(f"Unknown PDF page type: {page_type}")
```

这个是当前文件最重要的修复。

---

## 5. `_format_page` 不要给空页生成标题

当前：

```python
parts = [f"## Page {page_index + 1}"]
...
return "\n\n".join(parts).strip()
```

这会导致空页也生成：

```text
## Page 3
```

如果一个 PDF 全是空页，最终 `result.text` 也可能不为空，从而绕过 `DocumentParseService` 的空文本校验。

建议：

```python
def _format_page(self, page_index: int, text: str, tables: List[ParsedTable]) -> str:
    content_parts: List[str] = []

    if text.strip():
        content_parts.append(text.strip())

    table_text = _format_tables(tables)
    if table_text:
        content_parts.append(table_text)

    if not content_parts:
        return ""

    return "\n\n".join([f"## Page {page_index + 1}", *content_parts]).strip()
```

---

## 6. metadata 生成逻辑抽出来，避免三元表达式过密

当前：

```python
"table_backend": "camelot" if page_type == "text" else "pp_structure" if page_tables else "none",
```

这行可读性差，而且在 `mixed / empty` 修复后会更复杂。

建议新增内部方法：

```python
def _build_page_metadata(self, page_type: str, page_tables: List[ParsedTable]) -> Dict[str, str]:
    if page_type in {_PAGE_TYPE_TEXT, _PAGE_TYPE_MIXED}:
        return {
            "page_type": page_type,
            "ocr_used": False,
            "text_backend": _TEXT_BACKEND_PYMUPDF,
            "table_backend": _TABLE_BACKEND_CAMELOT if page_tables else _BACKEND_NONE,
        }

    if page_type == _PAGE_TYPE_SCANNED:
        return {
            "page_type": page_type,
            "ocr_used": True,
            "text_backend": _TEXT_BACKEND_PADDLEOCR,
            "table_backend": _TABLE_BACKEND_PP_STRUCTURE if page_tables else _BACKEND_NONE,
        }

    return {
        "page_type": page_type,
        "ocr_used": False,
        "text_backend": _BACKEND_NONE,
        "table_backend": _BACKEND_NONE,
    }
```

这里返回类型严格一点可以写 `Dict[str, Any]`，因为 `ocr_used` 是 bool。

---

# 建议核心修改片段

不是完整文件，只列最关键改法：

```python
from typing import Any, Dict, List, Tuple
```

```python
def _format_tables(tables: List[ParsedTable]) -> str:
    ...
```

```python
page_type_counts: Dict[str, int] = {
    _PAGE_TYPE_TEXT: 0,
    _PAGE_TYPE_MIXED: 0,
    _PAGE_TYPE_SCANNED: 0,
    _PAGE_TYPE_EMPTY: 0,
}
```

```python
if page_type in {_PAGE_TYPE_TEXT, _PAGE_TYPE_MIXED}:
    page_text, page_tables = self._parse_text_page(path, page_index=page_index)
elif page_type == _PAGE_TYPE_SCANNED:
    page_text, page_tables = await self._parse_scanned_page(
        path,
        page_index=page_index,
        render_dir=render_dir,
        warnings=warnings,
    )
elif page_type == _PAGE_TYPE_EMPTY:
    page_text, page_tables = "", []
else:
    raise ValueError(f"Unknown PDF page type: {page_type}")
```

```python
page_metadata = self._build_page_metadata(page_type, page_tables)
```

```python
def _format_page(self, page_index: int, text: str, tables: List[ParsedTable]) -> str:
    content_parts: List[str] = []

    if text.strip():
        content_parts.append(text.strip())

    table_text = _format_tables(tables)
    if table_text:
        content_parts.append(table_text)

    if not content_parts:
        return ""

    return "\n\n".join([f"## Page {page_index + 1}", *content_parts]).strip()
```

---

# 需要人工确认后再小修的点

## 1. OCR 失败是否必须记录为 `ocr_failed`

当前 `_parse_scanned_page()` 中，如果：

```python
ocr_text = await self.ocr_adapter.extract_text(image_path)
```

失败，会被外层捕获成：

```text
page_parse_failed: page=...
```

这能工作，但诊断不够精确。

如果你希望 warnings 更细，可以在 `_parse_scanned_page()` 里包一层：

```python
try:
    ocr_text = await self.ocr_adapter.extract_text(image_path)
except Exception as e:
    warnings.append(f"ocr_failed: page={page_index + 1}: {type(e).__name__}: {e}")
    raise
```

但这样外层还会追加 `page_parse_failed`，会有两条 warning。是否接受要确认。

我建议当前先不改，除非你明确希望区分 `ocr_failed` 和普通 `page_parse_failed`。

---

## 2. `mixed` 页是否需要“文本 + OCR”双路径

我目前建议 `mixed` 走文字页路径：

```text
PyMuPDF text + Camelot
```

原因：mixed 页已有文字层，不应默认 OCR。
如果未来实测某些 mixed 页文字层质量很差，再加质量评估策略，而不是现在就 OCR。

---

## 3. 是否共享 PyMuPDF document 对象

当前 `_page_count()` 打开一次 PDF；后续 classifier / extractor / renderer 可能各自再打开文件。性能上不一定最优。

但如果要共享 `doc`，会影响：

```text
PageClassifier
TextExtractor
PageRenderer
TableExtractor
ScannedTableExtractor
```

这已经超出当前文件 review 范围。当前保持 path + page_index 接口即可。

---

# 暂不建议改的点

## 1. 不建议把整个 `_parse()` 拆成很多函数

当前 `_parse()` 虽然稍长，但流程还清楚：

```text
page_count
↓
for page
↓
classify
↓
parse text / scanned
↓
format page
↓
metadata
↓
result
```

先修分流 bug 和 metadata 可读性，不要为了短函数大拆。

## 2. 不建议改 `fitz` 为 `pymupdf`

虽然新版官方文档更常展示 `import pymupdf`，但项目其他 PDF 子模块可能已经统一使用 `fitz`。这属于全局风格一致性问题，不在当前文件单独改。

---

# 本文件验收标准

```bash
rg "def format_tables" src/chat/application/document_parse/pdf/parser.py
```

应无结果，应改为：

```text
def _format_tables
```

```bash
rg "else:\\s*$" src/chat/application/document_parse/pdf/parser.py
```

人工确认不再存在“非 text 全部走 scanned”的分支。

```bash
rg "\"text\"|\"mixed\"|\"scanned\"|\"empty\"|\"pymupdf\"|\"paddleocr\"|\"camelot\"|\"pp_structure\"" src/chat/application/document_parse/pdf/parser.py
```

这些字符串应主要出现在内部常量定义中。

人工确认：

```text
mixed 页走 _parse_text_page。
empty 页不 OCR。
空页不会生成只有标题的 page text。
PP-Structure 失败仍保留 OCR 文本。
单页失败仍进入 page_parse_failed warning，并继续后续页。
```

---

#