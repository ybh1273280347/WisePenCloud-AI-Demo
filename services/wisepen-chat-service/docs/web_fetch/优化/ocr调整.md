下面给你一版**完整、可执行、边界清楚的 OCR 实施文档**。这版不再拆“第一版/第二版”，而是直接给出一个**复杂度适中、后续可维护、不会偷懒埋坑**的方案。

核心思路：

```text
Docling 负责文档结构解析，不负责 OCR。
PaddleOCR 负责显式 OCR。
OCR 集成到 web_fetch full 链。
默认 web_fetch 不跑 OCR。
force_ocr=true 时才跑 OCR。
OCR 结果作为补充内容追加，并统一进入 ToolContentStore。
```

---

# WebFetch OCR 能力实施文档

## 1. 目标

为 `web_fetch` 增加显式 OCR 能力，用于处理：

```text
扫描版 PDF
图片文字
截图文字
普通文档解析结果中缺失的图片文字
```

但 OCR 不应成为默认文档解析的一部分。

默认解析链路继续追求：

```text
稳定
快速
结构化
少粘连
表格更清晰
Markdown 更适合模型读取
```

OCR 作为高成本能力，必须显式触发、受限执行、可观测、可回退。

---

# 2. 总体设计原则

## 2.1 Docling 与 OCR 分离

```text
Docling:
    负责 PDF / DOCX / PPTX 的结构化解析。
    默认关闭 OCR。
    保留表格结构解析。

PaddleOCR:
    负责图片文字识别。
    只在 force_ocr=true 时执行。
```

不要让 Docling 隐式进入 OCR 链路。

原因：

```text
Docling OCR 内部会进入 RapidOCR / ONNXRuntime。
这个链路已经出现 bad allocation。
OCR 是高成本、高资源、高不稳定能力，不适合作为默认解析副作用。
```

---

## 2.2 OCR 集成到 web_fetch，不新增独立工具

不新增：

```text
ocr_extract
```

直接扩展：

```text
web_fetch
```

原因：

```text
OCR 当前主要服务于网页/文档抓取后的补充识别。
用户和模型的自然动作仍然是“抓取这个 URL”。
OCR 是 web_fetch 的显式增强能力，而不是单独业务入口。
```

---

## 2.3 不做自动 OCR fallback

不要做：

```text
Docling 文本少
    ↓
自动 OCR
```

必须显式：

```text
force_ocr=true
```

原因：

```text
自动 OCR 会让耗时、资源、失败模式不可控。
普通文档解析和 OCR 的成本级别不同，不能混在一起。
```

---

## 2.4 OCR 结果必须进入 ToolContentStore

禁止：

```python
ocr_text[:3000]
content[:N]
markdown[:N]
```

所有长内容统一走：

```text
ToolContentStore
format_windowed_content
tool_content_read
```

这样模型能知道：

```text
当前是否读完
全文是否缓存
content_id 是什么
next_offset 是什么
是否可以继续读取
```

---

# 3. 最终链路

## 3.1 普通 web_fetch

```text
WebFetchTool
    ↓
FetchCoordinator
    ↓
StaticFetcher / SteelFetcher / LocalScriptFetcher
    ↓
ContentProcessor
    ↓
DocumentParser
    ↓
Docling OCR=False
    ↓
ToolContentStore
    ↓
返回窗口内容
```

---

## 3.2 web_fetch(force_ocr=true)

```text
WebFetchTool
    ↓
FetchCoordinator 正常抓取和解析
    ↓
得到基础 Markdown
    ↓
OcrProcessor 对原始 URL 执行 PaddleOCR
    ↓
得到 OCR Markdown
    ↓
合并基础 Markdown + OCR Supplement
    ↓
ToolContentStore
    ↓
format_windowed_content
```

---

# 4. 配置项

## 4.1 DocumentParser 配置

在 `app_settings` 中新增或确认：

```python
DOCUMENT_PARSER_ENABLE_OCR = False
DOCUMENT_PARSER_ENABLE_TABLE_STRUCTURE = True
DOCUMENT_PARSER_ENABLE_NATIVE_FALLBACK = True
```

含义：

```text
DOCUMENT_PARSER_ENABLE_OCR:
    控制 Docling OCR。
    默认 False。

DOCUMENT_PARSER_ENABLE_TABLE_STRUCTURE:
    控制 Docling 表格结构解析。
    默认 True。

DOCUMENT_PARSER_ENABLE_NATIVE_FALLBACK:
    Docling 没有产出结果时是否允许 native fallback。
```

---

## 4.2 WebFetch OCR 配置

新增：

```python
WEB_FETCH_ENABLE_OCR = True

WEB_FETCH_OCR_DEFAULT_MAX_PAGES = 3
WEB_FETCH_OCR_MAX_PAGES = 10
WEB_FETCH_OCR_RENDER_DPI = 180
WEB_FETCH_OCR_MAX_IMAGE_PIXELS = 20_000_000
WEB_FETCH_OCR_MAX_FILE_BYTES = 50 * 1024 * 1024
WEB_FETCH_OCR_TIMEOUT_SECONDS = 120.0

OCR_BACKEND = "paddleocr"
OCR_LANGUAGE = "ch"

OCR_WORKER_MODE = "lazy_persistent"
OCR_WORKER_IDLE_TTL_SECONDS = 30 * 60

OCR_USE_DOC_ORIENTATION_CLASSIFY = False
OCR_USE_DOC_UNWARPING = False
OCR_USE_TEXTLINE_ORIENTATION = False
```

说明：

```text
WEB_FETCH_OCR_DEFAULT_MAX_PAGES:
    PDF 未指定页码时默认 OCR 前几页。

WEB_FETCH_OCR_MAX_PAGES:
    单次最多 OCR 页数。

WEB_FETCH_OCR_RENDER_DPI:
    PDF 渲染 DPI。
    默认 180，避免 300 DPI 带来的内存风险。

WEB_FETCH_OCR_MAX_IMAGE_PIXELS:
    单页图片最大像素数限制。

WEB_FETCH_OCR_MAX_FILE_BYTES:
    OCR 输入文件最大大小。

OCR_WORKER_MODE:
    使用 lazy_persistent，首次 OCR 时启动，后续复用。

OCR_WORKER_IDLE_TTL_SECONDS:
    OCR worker 空闲一段时间后退出，释放内存。
```

不要使用：

```python
getattr(settings, "XXX", default)
```

settings 必须显式存在。

---

# 5. 依赖策略

## 5.1 OCR 依赖放入 full / ocr 依赖组

建议：

```toml
[dependency-groups]
ocr = [
    "paddleocr",
    "paddlepaddle",
    "pymupdf",
]
```

Docling 依赖可以继续放在 full / doc 依赖组。

最终部署可分为：

```text
lite:
    native parser
    无 Docling
    无 PaddleOCR

full:
    Docling
    PaddleOCR
    PyMuPDF
```

---

## 5.2 服务启动行为

未安装 OCR 依赖时：

```text
普通 web_fetch:
    正常运行。

web_fetch(force_ocr=true):
    返回明确 OCR 不可用说明。
```

不要因为 OCR 依赖缺失导致整个服务无法启动。

---

# 6. 文件结构

新增目录：

```text
chat/application/web_fetch/ocr/
├── __init__.py
├── local_ocr_processor.py
├── paddle_ocr_worker.py
└── pdf_render.py
```

职责：

```text
local_ocr_processor.py:
    主服务侧 OCR processor。
    负责下载输入、判断类型、调用 worker、控制超时、合并结果。

paddle_ocr_worker.py:
    OCR worker。
    只在这里 import paddleocr。
    负责加载 PaddleOCR 并识别图片。

pdf_render.py:
    PDF 页面渲染。
    使用 PyMuPDF 把 PDF 页转图片。
```

---

# 7. DocumentParser 修改

## 7.1 目标

保留 Docling 结构化解析，但关闭 OCR。

---

## 7.2 修改 Docling 初始化

在 `DoclingDocumentParser` 中配置 PDF pipeline。

示例：

```python
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableStructureOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

from chat.core.config.app_settings import settings
```

```python
pipeline_options = PdfPipelineOptions()
pipeline_options.do_ocr = settings.DOCUMENT_PARSER_ENABLE_OCR
pipeline_options.do_table_structure = settings.DOCUMENT_PARSER_ENABLE_TABLE_STRUCTURE

if settings.DOCUMENT_PARSER_ENABLE_TABLE_STRUCTURE:
    pipeline_options.table_structure_options = TableStructureOptions(
        do_cell_matching=True,
    )

self._converter = DocumentConverter(
    format_options={
        InputFormat.PDF: PdfFormatOption(
            pipeline_options=pipeline_options,
        )
    }
)
```

---

## 7.3 禁止事项

```text
不要启用 Docling OCR。
不要在 DocumentParser 中自动 OCR fallback。
不要因为文本少自动 OCR。
不要禁用 Docling。
不要改变 XLSX native 策略。
不要改变 DocumentParser.parse / parse_async 对外 API。
```

---

# 8. WebFetchTool Schema 修改

## 8.1 新增参数

在 `WebFetchTool` 的 schema 中新增：

```python
"force_ocr": {
    "type": "boolean",
    "description": (
        "Whether to explicitly run OCR for image text or scanned PDF pages. "
        "Use only when the user asks to recognize text from images or scanned documents."
    ),
    "default": False,
}
```

为了控制复杂度，当前不暴露 `ocr_pages`。

PDF 默认 OCR 前：

```python
settings.WEB_FETCH_OCR_DEFAULT_MAX_PAGES
```

页数上限由：

```python
settings.WEB_FETCH_OCR_MAX_PAGES
```

控制。

---

## 8.2 不新增参数

暂时不加：

```text
ocr_pages
ocr_max_pages
ocr_dpi
ocr_language
```

原因：

```text
schema 保持简单。
模型更不容易乱传。
后续确实需要精确页码时再扩展。
```

---

# 9. WebFetchTool 执行逻辑

## 9.1 构造函数

修改为显式注入：

```python
class WebFetchTool(BaseTool):
    def __init__(
        self,
        fetcher: FetchCoordinator,
        ocr_processor: LocalOcrProcessor,
    ):
        self._fetcher = fetcher
        self._ocr_processor = ocr_processor
```

不要写：

```python
ocr_processor: Optional[LocalOcrProcessor] = None
self._ocr_processor = ocr_processor or LocalOcrProcessor(...)
```

container 负责装配。

---

## 9.2 execute 主流程

逻辑：

```text
1. 校验 session_id。
2. 读取 url。
3. 校验 url。
4. 读取 force_ocr。
5. 调用 self._fetcher.fetch(url, force_browser=force_browser) 获取基础 Markdown。
6. 如果 force_ocr=false：
       走原来的 ToolContentStore 返回逻辑。

7. 如果 force_ocr=true：
       调用 self._ocr_processor.extract_from_url(url)。
       如果 OCR 有结果：
           合并基础 Markdown + OCR Supplement。
       如果 OCR 失败：
           保留基础 Markdown，并追加 OCR 失败说明。
       合并结果进入 ToolContentStore。
```

---

## 9.3 OCR 合并格式

当基础解析和 OCR 都成功：

```markdown
# Document Parse Result

{base_markdown}

---

# OCR Supplement

Source: {url}
Backend: paddleocr

## Page 1

{ocr_text}

## Page 2

{ocr_text}
```

当基础解析失败但 OCR 成功：

```markdown
# OCR Supplement

Source: {url}
Backend: paddleocr

## Page 1

{ocr_text}
```

当基础解析成功但 OCR 失败：

```markdown
# Document Parse Result

{base_markdown}

---

# OCR Supplement

OCR failed: {reason}
```

当基础解析失败且 OCR 也失败：

```text
[Tool Result] Failed to fetch web page content (all fetch methods exhausted)
```

或者：

```text
[Tool Result] Failed to fetch web page content and OCR also failed: {reason}
```

推荐第二种，诊断更清楚。

---

# 10. OCR Processor 设计

## 10.1 类名

```python
class LocalOcrProcessor:
    ...
```

位置：

```text
chat/application/web_fetch/ocr/local_ocr_processor.py
```

---

## 10.2 构造函数

```python
class LocalOcrProcessor:
    def __init__(
        self,
        timeout: float,
        default_max_pages: int,
        max_pages: int,
        render_dpi: int,
        max_image_pixels: int,
        max_file_bytes: int,
    ):
        self._timeout = timeout
        self._default_max_pages = default_max_pages
        self._max_pages = max_pages
        self._render_dpi = render_dpi
        self._max_image_pixels = max_image_pixels
        self._max_file_bytes = max_file_bytes
```

---

## 10.3 对外方法

```python
async def extract_from_url(self, url: str) -> Optional[str]:
    ...
```

返回：

```text
成功:
    OCR Markdown

失败:
    None
```

如果需要保留失败原因，可以返回 dataclass：

```python
@dataclass
class OcrResult:
    text: str = ""
    error: Optional[str] = None
```

推荐使用 dataclass，避免只能返回 None，后续日志和用户提示更清楚。

```python
@dataclass
class OcrResult:
    ok: bool
    text: str = ""
    error: Optional[str] = None
```

---

## 10.4 输入类型支持

支持：

```text
PDF
PNG
JPG
JPEG
WEBP
```

根据：

```text
Content-Type
URL 后缀
文件 magic bytes
```

判断。

第一版至少支持：

```text
PDF
PNG
JPG
JPEG
```

WEBP 可加可不加。

---

# 11. OCR 下载策略

## 11.1 使用 httpx 下载 bytes

`LocalOcrProcessor` 内部可以直接使用 `httpx` 下载原始 bytes。

不要复用 `FetchCoordinator`，避免循环依赖。

限制：

```text
WEB_FETCH_OCR_MAX_FILE_BYTES
```

实现要点：

```text
1. 只允许 http / https。
2. 响应非 2xx 返回失败。
3. Content-Length 超限提前拒绝。
4. 流式读取，超过 max_file_bytes 立即停止。
5. 下载完成后判断文件类型。
```

---

# 12. PDF 渲染

## 12.1 使用 PyMuPDF

位置：

```text
chat/application/web_fetch/ocr/pdf_render.py
```

核心逻辑：

```python
import fitz
```

```python
def render_pdf_pages(
    data: bytes,
    *,
    max_pages: int,
    dpi: int,
    max_image_pixels: int,
) -> List[Path]:
    ...
```

---

## 12.2 渲染规则

```text
默认渲染前 WEB_FETCH_OCR_DEFAULT_MAX_PAGES 页。
最大不超过 WEB_FETCH_OCR_MAX_PAGES。
DPI 使用 WEB_FETCH_OCR_RENDER_DPI。
渲染后检查 width * height。
超过 WEB_FETCH_OCR_MAX_IMAGE_PIXELS 的页面跳过。
```

---

## 12.3 临时文件

使用：

```python
tempfile.TemporaryDirectory()
```

渲染出的图片放在临时目录中。

OCR 完成后自动清理。

---

# 13. PaddleOCR Worker

## 13.1 位置

```text
chat/application/web_fetch/ocr/paddle_ocr_worker.py
```

---

## 13.2 原则

```text
只在 worker 中 import paddleocr。
主服务 import 阶段不 import paddleocr。
```

---

## 13.3 worker 输入

命令：

```bash
python -m chat.application.web_fetch.ocr.paddle_ocr_worker \
  --input /tmp/page_1.png \
  --lang ch
```

---

## 13.4 worker 输出

成功输出 JSON：

```json
{
  "ok": true,
  "backend": "paddleocr",
  "text": "识别出的文本",
  "line_count": 42
}
```

失败输出 JSON：

```json
{
  "ok": false,
  "error": "OCR_FAILED",
  "message": "具体错误"
}
```

---

## 13.5 PaddleOCR 初始化

```python
from paddleocr import PaddleOCR

ocr = PaddleOCR(
    lang=language,
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
)
```

这些参数由 settings 传入 worker。

---

# 14. Worker 模式

## 14.1 采用 lazy persistent worker

目标行为：

```text
服务启动:
    不加载 PaddleOCR。

第一次 force_ocr:
    启动 OCR worker。
    加载 PaddleOCR。
    执行 OCR。

后续 force_ocr:
    复用已启动 worker。

worker 空闲超过 OCR_WORKER_IDLE_TTL_SECONDS:
    退出释放内存。

worker 崩溃:
    下次 OCR 自动重新启动。
```

---

## 14.2 实现建议

为了避免实现复杂的双向长连接协议，可以采用**简化 lazy persistent 方案**：

```text
LocalOcrProcessor 维护一个 OCR worker server 子进程。
worker 启动后监听本地 stdin/stdout JSON line 协议。
每次 OCR 请求发送一行 JSON。
worker 返回一行 JSON。
```

请求：

```json
{
  "input": "/tmp/page_1.png",
  "lang": "ch"
}
```

响应：

```json
{
  "ok": true,
  "text": "...",
  "line_count": 42
}
```

优点：

```text
不用每次加载模型。
主服务和 OCR 隔离。
worker 崩溃可重启。
协议简单。
```

---

## 14.3 不采用每次冷启动

不要每次 OCR 都：

```text
python -m paddle_ocr_worker --input ...
```

原因：

```text
每次都会重新加载模型。
用户每次 OCR 都慢。
长驻服务场景下不划算。
```

---

# 15. ToolContentStore 集成

## 15.1 合并后统一缓存

`force_ocr=true` 时，最终合并后的 Markdown 进入 ToolContentStore。

```python
content_id = tool_content_store.put(
    session_id=session_id,
    tool_name="web_fetch",
    source=url,
    text=merged_markdown,
    content_type="text/markdown",
    metadata={
        "force_ocr": force_ocr,
        "ocr_backend": "paddleocr",
    },
)
```

然后：

```python
window = tool_content_store.read_window(
    content_id=content_id,
    session_id=session_id,
    offset=0,
    limit=settings.TOOL_RESULT_MAX_CHARS,
)
```

返回：

```python
format_windowed_content(window)
```

---

## 15.2 无法缓存时

使用：

```python
create_uncached_window(...)
```

不要手写截断。

---

# 16. Container 注册

## 16.1 新增 OCR provider

在 `_register_web_providers` 中注册：

```python
container_cls.web_fetch_ocr_processor = providers.Singleton(
    LocalOcrProcessor,
    timeout=settings.WEB_FETCH_OCR_TIMEOUT_SECONDS,
    default_max_pages=settings.WEB_FETCH_OCR_DEFAULT_MAX_PAGES,
    max_pages=settings.WEB_FETCH_OCR_MAX_PAGES,
    render_dpi=settings.WEB_FETCH_OCR_RENDER_DPI,
    max_image_pixels=settings.WEB_FETCH_OCR_MAX_IMAGE_PIXELS,
    max_file_bytes=settings.WEB_FETCH_OCR_MAX_FILE_BYTES,
)
```

---

## 16.2 修改 WebFetchTool 注册

```python
container_cls.web_fetch_tool = providers.Singleton(
    WebFetchTool,
    fetcher=container_cls.fetch_coordinator,
    ocr_processor=container_cls.web_fetch_ocr_processor,
)
```

---

## 16.3 不注册 PaddleOCR engine

不要：

```python
container_cls.paddle_ocr_engine = providers.Singleton(PaddleOCR)
```

PaddleOCR 只在 worker 中加载。

---

# 17. 错误处理策略

## 17.1 OCR disabled

当：

```python
settings.WEB_FETCH_ENABLE_OCR is False
```

且用户传：

```text
force_ocr=true
```

返回基础解析结果，并追加：

```markdown
---

# OCR Supplement

OCR skipped: OCR is disabled.
```

---

## 17.2 OCR backend 不可用

```markdown
---

# OCR Supplement

OCR failed: OCR backend is not available or not installed.
```

---

## 17.3 OCR 超时

```markdown
---

# OCR Supplement

OCR failed: OCR processing timed out.
```

---

## 17.4 OCR 没有识别出文本

```markdown
---

# OCR Supplement

OCR produced no text.
```

---

## 17.5 OCR 不影响基础解析

原则：

```text
只要基础解析成功，OCR 失败不能导致 web_fetch 整体失败。
```

只有当：

```text
基础解析失败
OCR 也失败
```

才返回整体失败。

---

# 18. 日志要求

## 18.1 初始化日志

OCR worker 首次启动时记录一次：

```text
OCR worker started
backend=paddleocr
mode=lazy_persistent
```

不要每次 OCR 都打印初始化日志。

---

## 18.2 运行日志

保留：

```text
OCR source downloaded
OCR source rejected: file too large
OCR page rendered
OCR page skipped: pixel limit exceeded
OCR backend unavailable
OCR timeout
OCR worker restarted
OCR produced no text
OCR completed
```

---

## 18.3 不要隐藏真实错误

不要简单过滤：

```text
PaddleOCR error
worker crash
timeout
```

OCR 是显式能力，失败要可见。

---

# 19. 测试计划

## 19.1 DocumentParser 测试

覆盖：

```text
Docling OCR=False。
普通 PDF 不再触发 OCR。
Docling 结构化解析仍然可用。
表格结构仍然可用。
XLSX 仍然 native。
```

---

## 19.2 LocalOcrProcessor 单测

覆盖：

```text
URL 非 http/https 拒绝。
文件超过 WEB_FETCH_OCR_MAX_FILE_BYTES 拒绝。
不支持的文件类型拒绝。
图片 OCR 成功。
PDF 默认页数限制生效。
PDF 页像素限制生效。
OCR backend unavailable 返回明确错误。
OCR timeout 返回明确错误。
```

---

## 19.3 WebFetchTool 单测

覆盖：

```text
force_ocr=false:
    行为与原 web_fetch 一致。

force_ocr=true 且 OCR 成功:
    返回包含 OCR Supplement。
    合并内容进入 ToolContentStore。
    返回 ToolContent Metadata。

force_ocr=true 且 OCR 失败:
    基础解析结果保留。
    OCR Supplement 中包含失败说明。

基础解析失败但 OCR 成功:
    返回 OCR Supplement。

基础解析失败且 OCR 失败:
    返回整体失败说明。
```

---

## 19.4 E2E 测试

新增：

```text
test/test_web_fetch_ocr_e2e.py
```

用例：

```text
1. 普通 PDF，force_ocr=false
   期望：Docling 正常解析，不出现 OCR Supplement。

2. 图片 URL，force_ocr=true
   期望：返回 OCR Supplement。

3. 扫描 PDF，force_ocr=true
   期望：返回 OCR Supplement。

4. 长 OCR 内容
   期望：返回 ToolContent Metadata，可 tool_content_read 续读。
```

---

# 20. 实施顺序

按这个顺序做，避免一次性引入不可控复杂度：

```text
1. 关闭 Docling OCR。
2. web_fetch schema 增加 force_ocr。
3. 增加 OCR settings。
4. 增加 OCR 文件结构。
5. 实现 PaddleOCR worker 的 JSON line 协议。
6. 实现 LocalOcrProcessor。
7. 实现图片 OCR。
8. 实现 PDF 前 N 页渲染和 OCR。
9. WebFetchTool 合并 OCR Supplement。
10. 接入 ToolContentStore。
11. 注册 container provider。
12. 补单测和 E2E。
```

---

# 21. 禁止事项

```text
不要启用 Docling OCR。
不要在 DocumentParser 中自动 OCR fallback。
不要因为文本短自动 OCR。
不要新增独立 ocr_extract tool。
不要每次 OCR 都冷启动 worker。
不要在主服务 import 阶段 import paddleocr。
不要默认 OCR 整本 PDF。
不要默认 300 DPI。
不要手写 content[:N] 截断。
不要改变 FetchCoordinator 降级链。
不要改变 ToolContentStore 协议。
不要把 PaddleOCR engine 注册进 container。
不要使用 Optional 依赖兜底。
不要使用 getattr(settings, "...", default)。
```

---

# 22. 验收标准

完成后必须满足：

```text
1. 普通 web_fetch PDF 不触发 OCR。
2. 普通 web_fetch PDF 仍使用 Docling 结构化解析。
3. force_ocr=true 时才调用 PaddleOCR。
4. OCR 输出作为 OCR Supplement 追加。
5. 合并结果进入 ToolContentStore。
6. 长 OCR 结果可以用 tool_content_read 续读。
7. OCR worker 第一次可以慢，后续不应每次冷启动。
8. OCR 失败不影响普通 web_fetch 主结果。
9. 未安装 OCR 依赖时服务仍可启动。
10. force_ocr=true 且 OCR 不可用时有明确说明。
11. 不再出现 Docling RapidOCR / ONNXRuntime OCR 链路日志。
```

---

# 23. 最终提示词

```text
请实现 web_fetch 的显式 OCR 能力，严格按照以下设计执行。

核心决策：
1. Docling 继续作为 PDF/DOCX/PPTX 的结构化解析主路径。
2. Docling 默认关闭 OCR。
3. PaddleOCR 作为显式 OCR backend。
4. OCR 不新增独立工具，只集成到 web_fetch。
5. 只有 force_ocr=true 时才执行 OCR。
6. OCR 结果作为 OCR Supplement 追加到基础解析结果。
7. 最终合并内容必须进入 ToolContentStore。
8. 不允许手写截断。
9. 不允许每次 OCR 都冷启动 worker。
10. 不允许在主服务 import 阶段 import paddleocr。

DocumentParser：
- 新增 DOCUMENT_PARSER_ENABLE_OCR=False。
- 新增 DOCUMENT_PARSER_ENABLE_TABLE_STRUCTURE=True。
- 配置 Docling PdfPipelineOptions：
  pipeline_options.do_ocr = settings.DOCUMENT_PARSER_ENABLE_OCR
  pipeline_options.do_table_structure = settings.DOCUMENT_PARSER_ENABLE_TABLE_STRUCTURE
- 不要自动 OCR fallback。
- XLSX 仍走 native。

WebFetchTool：
- schema 增加 force_ocr boolean，默认 false。
- force_ocr=false 时行为完全不变。
- force_ocr=true 时：
  1. 先执行原 web_fetch 解析。
  2. 再调用 OCR processor。
  3. 将 OCR Markdown 作为 OCR Supplement 追加。
  4. 合并结果进入 ToolContentStore。
  5. 返回 format_windowed_content。

OCR 模块：
- 新增 chat/application/web_fetch/ocr/。
- 实现 local_ocr_processor.py。
- 实现 paddle_ocr_worker.py。
- 实现 pdf_render.py。
- paddleocr 只能在 worker 中 import。
- 主服务不能 import paddleocr。

OCR worker：
- 使用 lazy persistent worker。
- 首次 OCR 时启动。
- 后续复用。
- 空闲超过 OCR_WORKER_IDLE_TTL_SECONDS 后退出。
- worker 崩溃后下一次 OCR 自动重启。
- 使用 JSON line 协议。
- 不要每次 OCR 都重新加载模型。

PDF OCR：
- 使用 PyMuPDF 渲染。
- 默认 OCR 前 WEB_FETCH_OCR_DEFAULT_MAX_PAGES 页。
- 最大不超过 WEB_FETCH_OCR_MAX_PAGES。
- DPI 使用 WEB_FETCH_OCR_RENDER_DPI，默认 180。
- 检查 WEB_FETCH_OCR_MAX_IMAGE_PIXELS。
- 不默认 300 DPI。
- 不默认 OCR 整本 PDF。

settings：
新增：
WEB_FETCH_ENABLE_OCR = True
WEB_FETCH_OCR_DEFAULT_MAX_PAGES = 3
WEB_FETCH_OCR_MAX_PAGES = 10
WEB_FETCH_OCR_RENDER_DPI = 180
WEB_FETCH_OCR_MAX_IMAGE_PIXELS = 20_000_000
WEB_FETCH_OCR_MAX_FILE_BYTES = 50 * 1024 * 1024
WEB_FETCH_OCR_TIMEOUT_SECONDS = 120.0
OCR_BACKEND = "paddleocr"
OCR_LANGUAGE = "ch"
OCR_WORKER_MODE = "lazy_persistent"
OCR_WORKER_IDLE_TTL_SECONDS = 30 * 60
OCR_USE_DOC_ORIENTATION_CLASSIFY = False
OCR_USE_DOC_UNWARPING = False
OCR_USE_TEXTLINE_ORIENTATION = False

container：
- 注册 LocalOcrProcessor。
- WebFetchTool 注入 ocr_processor。
- 不注册 PaddleOCR engine。
- 不使用 Optional 依赖兜底。

错误处理：
- OCR disabled：保留基础解析结果，追加 OCR skipped。
- OCR backend 不可用：保留基础解析结果，追加 OCR failed。
- OCR timeout：保留基础解析结果，追加 OCR failed。
- OCR produced no text：保留基础解析结果，追加说明。
- 基础解析失败但 OCR 成功：返回 OCR Supplement。
- 基础解析失败且 OCR 失败：返回整体失败说明。

禁止：
- 不要启用 Docling OCR。
- 不要新增 ocr_extract tool。
- 不要自动 OCR fallback。
- 不要手写 content[:N]。
- 不要改变 FetchCoordinator 降级链。
- 不要改变 ToolContentStore 协议。
- 不要使用 getattr(settings, "...", default)。
- 不要改变项目 typing 风格。

测试：
- 补 DocumentParser OCR=False 测试。
- 补 LocalOcrProcessor 单测。
- 补 WebFetchTool force_ocr 单测。
- 补 test/test_web_fetch_ocr_e2e.py。
```

---

这版方案的取舍是：**不偷懒到每次冷启动，也不复杂到独立 OCR 服务；不把 OCR 混进默认解析，也不放弃和 Docling 的组合效果。**
