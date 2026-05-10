你的警钟敲得非常及时。我们刚刚解决了 PDF，但互联网上还有不少常见的"二进制文本"格式，它们同样会绕过浏览器直接以文件形式存在。如果链接指向这些资源，`webfetch` 一样会栽跟头。

---

### 最可能遇到的"二进制文本"类型（按场景排序）

| 格式 | MIME 类型 | 典型场景 | 建议优先级 |
|------|-----------|----------|------------|
| **PDF** | `application/pdf` | 论文、报告、合同 | ✅ 已解决 |
| **Word 文档** | `application/msword`<br>`application/vnd.openxmlformats-officedocument.wordprocessingml.document` | 政策公告、文档共享 | 🔴 极高 |
| **Excel 表格** | `application/vnd.ms-excel`<br>`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | 统计数据、报表 | 🟡 高 |
| **PowerPoint** | `application/vnd.ms-powerpoint`<br>`application/vnd.openxmlformats-officedocument.presentationml.presentation` | 演示文稿、培训资料 | 🟡 高 |
| **纯文本** | `text/plain` | 日志、README | 🟢 已支持（直接读） |
| **富文本** | `application/rtf` | 旧式文档 | 🟢 低 |
| **压缩包** | `application/zip` 等 | 批量下载 | ⚪ 暂不考虑 |
| **图片** | `image/png`、`image/jpeg` | 截图、图表 | ⚪ 对文本 Agent 无意义 |
| **JSON/CSV/XML** | `application/json`、`text/csv`、`application/xml` | 数据接口 | 🟢 已经可以作为纯文本处理 |

---

### 应对方案：让 `StaticFetcher` 变成"文档下载器"，`ContentCleaner` 变成"文本提取中心"

我们不打算让 `webfetch` 处理图片、压缩包等非文本格式，但对于**可提取出文字的文档类二进制文件**，我们可以复用 PDF 的思路：

1. **`StaticFetcher`** 根据 `Content-Type` 判断是否属于"可提取文本的文档"。如果是，直接返回 `bytes`；如果不是，返回 `None` 降级。
2. **`ContentCleaner`** 增加一个通用入口 `clean_bytes(byte_content, mimetype)`，内部根据类型调用对应解析库。
3. **降级链优先级不变**：先静态下载+解析，失败再交给浏览器（如果浏览器能打开，比如内置的 Office 查看器）。

---

### 推荐实现的库（都是纯 Python，MIT 协议）

| 格式 | 库 | 安装 |
|------|----|------|
| Word `.docx` | `python-docx` | `pip install python-docx` |
| Excel `.xlsx` | `openpyxl` | `pip install openpyxl` |
| PowerPoint `.pptx` | `python-pptx` | `pip install python-pptx` |
| 旧版 Word `.doc` | `textract` 或 `antiword` (系统工具) | 跨平台差，暂缓 |
| RTF | `striprtf` | `pip install striprtf` |

对于 `.doc` 这种跨平台解析困难的格式，可以优先尝试用浏览器打开（浏览器有时能渲染），失败就算了。

---

### 最小化改动路径（优先 PDF + Word + Excel + PPT）

**1. 扩展 `StaticFetcher` 的白名单**

```python
SUPPORTED_DOC_TYPES = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
}
```

一旦匹配，返回 `(content_bytes, type_key)`。

**2. 编写 `document_parser.py`**

```python
import pdfplumber
from docx import Document
from openpyxl import load_workbook
from pptx import Presentation
from io import BytesIO
from typing import Optional

def parse_pdf(b: bytes) -> Optional[str]: ...
def parse_docx(b: bytes) -> Optional[str]: 
    doc = Document(BytesIO(b))
    return "\n".join(p.text for p in doc.paragraphs)
def parse_xlsx(b: bytes) -> Optional[str]:
    wb = load_workbook(BytesIO(b))
    text = []
    for sheet in wb:
        for row in sheet.iter_rows(values_only=True):
            text.append("\t".join(str(c) if c else "" for c in row))
    return "\n".join(text)
def parse_pptx(b: bytes) -> Optional[str]:
    prs = Presentation(BytesIO(b))
    return "\n".join(shape.text for slide in prs.slides for shape in slide.shapes if shape.has_text_frame)
```

**3. 调整降级链**

在 `FetchCoordinator` 中，遇上 PDF/DOCX 等链接时，直接用 `StaticFetcher` 下载 + 解析，不再调用 Steel 和 LocalScript。因为浏览器对这些文件的处理不稳定且浪费资源。

---

### 长远看，这是一个"文档智能"的起点

你现在需要考虑的，不是立即把所有格式都实现完，而是**给 `webfetch` 留一个清晰的扩展点**。很快你就能把绝大多数文本类文档一网打尽。而且，这个能力可以无缝复用到未来"用户本地文件上传并总结"的场景。

你的警惕心让我们的工具从一个简单的网页抓取器，提前进化成了一个通用的文档阅读器。这个警钟，敲得太及时了。
