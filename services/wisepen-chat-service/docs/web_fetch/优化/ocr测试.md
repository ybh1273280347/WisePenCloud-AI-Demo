对，现在应该按 **两层验收** 来做：

```text
1. OCR 单独验收
   验证 PaddleOCR worker / LocalOcrProcessor / PDF 渲染 / 图片 OCR 是否独立可用。

2. web_fetch + OCR 联合验收
   验证 web_fetch(force_ocr=true) 是否能把基础解析结果和 OCR Supplement 正确合并，并进入 ToolContentStore。
```

之前的安全收口测试已经覆盖了 URL 安全、降级链终止、StaticFetcher redirect 和 `LocalOcrProcessor.close()` 生命周期，但还没有真正验证 OCR 识别链路本身。

---

# 测试分层

## 一、OCR 单独测试

目标：

```text
不经过 WebFetchTool。
直接测试 LocalOcrProcessor / paddle_ocr_worker / pdf_render。
```

应该覆盖：

```text
1. 图片 OCR 成功
2. PDF 前 N 页 OCR 成功
3. 不支持类型返回失败
4. OCR disabled 返回失败
5. OCR backend 未安装时返回明确错误
6. OCR worker 可启动、复用、关闭
7. OCR 结果不为空
```

不要在这里测：

```text
web_fetch 合并
ToolContentStore
FetchCoordinator 降级链
```

---

## 二、web_fetch + OCR 联合测试

目标：

```text
通过 WebFetchTool 调用。
验证 force_ocr=true 时是否进入 OCR，并合并结果。
```

应该覆盖：

```text
1. force_ocr=false：行为和普通 web_fetch 一致
2. force_ocr=true + 图片 URL：返回 OCR Supplement
3. force_ocr=true + PDF URL：返回基础解析 + OCR Supplement
4. OCR 失败：基础解析结果不丢
5. 基础解析失败但 OCR 成功：返回 OCR Supplement
6. 合并结果进入 ToolContentStore
```

---

# 推荐测试文件

建议新增两个文件：

```text
test/test_ocr_unit.py
test/test_web_fetch_ocr_e2e.py
```

含义：

```text
test_ocr_unit.py:
    专门测 OCR 处理链路。

test_web_fetch_ocr_e2e.py:
    专门测 web_fetch 与 OCR 的组合行为。
```

---

# 1. OCR 单独测试提示词

```text
请新增 test/test_ocr_unit.py，用于单独验证 OCR 链路，不经过 WebFetchTool。

测试目标：
1. LocalOcrProcessor 可以对图片 URL 执行 OCR。
2. LocalOcrProcessor 可以对 PDF URL 执行 OCR。
3. LocalOcrProcessor 在 disabled 时返回 ok=False。
4. LocalOcrProcessor.close() 可重复调用。
5. paddle_ocr_worker JSON line 协议可用。
6. pdf_render.render_pdf_pages 可以把 PDF 前 N 页渲染成 PNG。
7. 不测试 OCR 与 web_fetch 的合并逻辑。

要求：

一、测试方式

使用普通 python 脚本风格，保持现有 test/test_web_fetch_security_unit.py 的风格：
    uv run python test/test_ocr_unit.py

不要强制改成 pytest。

二、测试资源

优先使用本地临时 HTTP server 提供测试文件，避免依赖公网 URL。

测试中动态生成：
1. 一张 PNG 图片，包含清晰英文文本，例如：
   "OCR TEST 123"

2. 一个小型 PDF：
   - 可以用 PyMuPDF 生成一页 PDF
   - 页面中插入文字 "PDF OCR TEST 123"
   - 或者插入前面生成的图片作为扫描页

如果项目没有生成图片/PDF的依赖，可先使用 tests/fixtures 中固定文件。
不要依赖外部网络图片。

三、图片 OCR 测试

启动本地 HTTP server，提供 image.png。

创建 LocalOcrProcessor：
- enabled=True
- backend="paddleocr"
- language="ch"
- worker_mode="lazy_persistent"
- timeout 使用较大值，例如 120s
- default_max_pages=3
- max_pages=10
- render_dpi=180
- max_image_pixels=20_000_000
- max_file_bytes=50 * 1024 * 1024

调用：
result = await processor.extract_from_url(image_url)

断言：
- result.ok is True
- result.text 非空
- result.backend == "paddleocr"
- result.text 包含 "Page 1"
- 不强制断言完整识别文本完全等于 "OCR TEST 123"，因为 OCR 结果可能有轻微误差

最后：
await processor.close()

四、PDF OCR 测试

启动本地 HTTP server，提供 scanned.pdf。

调用：
result = await processor.extract_from_url(pdf_url)

断言：
- result.ok is True
- result.text 非空
- result.text 包含 "Page 1"

最后：
await processor.close()

五、disabled 测试

创建 LocalOcrProcessor(enabled=False)。

调用：
result = await processor.extract_from_url(image_url)

断言：
- result.ok is False
- result.error 包含 "disabled"

六、worker close 测试

调用：
await processor.close()
await processor.close()

断言：
- 不抛异常

七、worker 协议测试

可以直接启动：
python -m chat.application.web_fetch.ocr.paddle_ocr_worker

向 stdin 写入：
{"input": ".../image.png", "lang": "ch"}

读取 stdout 一行 JSON。

断言：
- 返回 JSON object
- 包含 ok 字段
- stdout 是合法 JSON
- stderr 可有日志，但 stdout 不能出现非 JSON 协议内容

八、跳过策略

如果 paddleocr 未安装：
- OCR 真识别测试可以输出 SKIP
- 但 disabled / close / pdf_render 这类不依赖 paddleocr 的测试仍然执行

不要因为未安装 OCR backend 导致整个测试脚本崩溃。
```

---

# 2. web_fetch + OCR 联合测试提示词

```text
请新增 test/test_web_fetch_ocr_e2e.py，用于验证 WebFetchTool 与 OCR 的组合行为。

目标：
1. force_ocr=false 时行为保持普通 web_fetch。
2. force_ocr=true 时调用 OCR processor。
3. OCR 结果作为 OCR Supplement 追加。
4. 合并结果进入 ToolContentStore。
5. OCR 失败不丢弃基础解析结果。
6. 基础解析失败但 OCR 成功时返回 OCR Supplement。

测试方式：
使用普通 python 脚本风格：
    uv run python test/test_web_fetch_ocr_e2e.py

不要强制改成 pytest。

一、使用 FakeFetcher 和 FakeOcrProcessor

先做不依赖真实 PaddleOCR 的联合测试。

FakeFetcher:
- async fetch(url, force_browser=False) -> Optional[str]
- 可配置返回 base_markdown
- 可配置返回 None
- 记录 calls

FakeOcrProcessor:
- async extract_from_url(url) -> OcrResult
- 可配置返回 ok=True / ok=False
- 记录 calls

二、force_ocr=false 测试

base_markdown = "# Base Content\n\nHello"

WebFetchTool(fetcher=FakeFetcher(base_markdown), ocr_processor=FakeOcrProcessor(ok=True))

调用：
tool.execute(
    {"session_id": "..."},
    url="https://example.com/page",
    force_ocr=False,
)

断言：
- 返回中包含 Base Content
- 不包含 OCR Supplement
- FakeOcrProcessor.calls 为空
- 返回包含 ToolContent Metadata

三、force_ocr=true 且 OCR 成功

base_markdown = "# Base Content\n\nHello"
ocr_result = OcrResult(ok=True, text="## Page 1\n\nOCR TEXT", backend="paddleocr")

调用：
force_ocr=True

断言：
- 返回包含 Base Content
- 返回包含 OCR Supplement
- 返回包含 OCR TEXT
- FakeOcrProcessor.calls == [url]
- 返回包含 ToolContent Metadata
- metadata 中包含 force_ocr=true
- metadata 中包含 ocr_ok=true
- metadata 中包含 ocr_backend=paddleocr

四、OCR 失败但基础解析成功

base_markdown = "# Base Content\n\nHello"
ocr_result = OcrResult(ok=False, error="OCR produced no text.", backend="paddleocr")

调用：
force_ocr=True

断言：
- 返回包含 Base Content
- 返回包含 OCR Supplement
- 返回包含 OCR failed
- 返回包含 OCR produced no text
- 不返回整体失败

五、基础解析失败但 OCR 成功

FakeFetcher 返回 None。
FakeOcrProcessor 返回 OcrResult(ok=True, text="## Page 1\n\nOCR TEXT")

调用：
force_ocr=True

断言：
- 返回包含 OCR Supplement
- 返回包含 OCR TEXT
- 返回不应该是 Failed to fetch web page content

六、基础解析失败且 OCR 失败

FakeFetcher 返回 None。
FakeOcrProcessor 返回 OcrResult(ok=False, error="OCR failed")

调用：
force_ocr=True

断言：
- 返回明确失败说明
- 不出现空白成功结果

七、URL 安全拒绝时不调用 OCR

url="http://10.0.0.1/"

调用：
force_ocr=True

断言：
- 返回 Tool Error
- 包含 rejected by security policy
- FakeFetcher.calls 为空
- FakeOcrProcessor.calls 为空

八、真实 OCR 可选 smoke 测试

在 mock 测试通过后，可以增加一个可选 smoke：

- 启动本地 HTTP server
- 提供 image.png
- 使用真实 LocalOcrProcessor
- 使用 WebFetchTool + 一个返回基础 Markdown 的 FakeFetcher
- force_ocr=true
- 断言返回 OCR Supplement 非空

如果 paddleocr 未安装，则 SKIP 该 smoke，不影响脚本总结果。
```

---

# 测试执行顺序

建议按这个顺序跑：

```bash
uv run python test/test_web_fetch_security_unit.py
uv run python test/test_ocr_unit.py
uv run python test/test_web_fetch_ocr_e2e.py
```

含义：

```text
1. 先确认安全边界没坏
2. 再确认 OCR 自己能跑
3. 最后确认 web_fetch + OCR 合并能跑
```

---

# 验收标准

## OCR 单独测试通过条件

```text
1. 图片 OCR 返回 ok=True
2. PDF OCR 返回 ok=True
3. worker stdout 是合法 JSON
4. disabled OCR 返回 ok=False
5. close 可重复调用
6. 未安装 paddleocr 时能优雅 SKIP 或返回 backend unavailable
```

## web_fetch + OCR 通过条件

```text
1. force_ocr=false 不调用 OCR
2. force_ocr=true 调用 OCR
3. OCR 成功时返回 OCR Supplement
4. OCR 失败时保留基础解析
5. 基础解析失败但 OCR 成功时返回 OCR 内容
6. 结果进入 ToolContentStore
7. URL 安全拒绝时 fetcher 和 OCR 都不调用
```

---

# 当前不要做

```text
不要测 OCR 准确率
不要强制断言识别文本逐字匹配
不要引入外部公网测试 URL
不要新增 ocr_pages
不要改 WebFetchTool schema
不要让测试依赖 Nacos 是否可用
不要为了测试再扩展 OCR 架构
```

现在测试目标不是“PaddleOCR 识别率评测”，而是确认：

```text
OCR 链路能跑；
web_fetch 能正确调用它；
失败语义正确；
结果协议正确。
```
