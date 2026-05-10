## 结论

`ocr/worker.py` 的整体职责是正确的：它是独立 OCR 子进程，负责 stdin/stdout JSON 协议、PaddleOCR lazy import、OCR 推理、stdout 重定向、stderr drain 配合。当前文件没有 URL 下载，也没有 PDF 解析职责，边界是干净的。

但这个文件需要比前面更谨慎，因为它直接碰 **PaddleOCR 版本 API**。我建议分成两类处理：

```text
我确定要改：
1. 内部 helper 加下划线。
2. 日志 / message 文本中文化。
3. 删除明显的 str(...) 宽容转换。
4. 保留 worker 协议韧性：非 JSON stdout 跳过、stderr drain、stdout 重定向。
5. 给 main / protocol 相关函数保持清晰边界。

需要人工确认后再小修：
1. PaddleOCR 当前项目锁定版本是 v3 还是 v2 legacy。
2. 确认后再决定是否删除 TypeError fallback / legacy ocr(...) 路径。
```

---

## 第三方库 API 确认

PaddleOCR 当前官方 quick start 使用的是：

```python
ocr = PaddleOCR(
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
)
result = ocr.predict("./general_ocr_002.png")
```

这和你当前 `get_engine()` 里新参数、`recognize_image()` 里优先 `engine.predict(...)` 的方向一致。([PaddlePaddle][1])

但 PaddleOCR 旧版文档里也存在 `use_angle_cls` 和 `ocr(..., cls=...)` 这套 legacy API。([GitHub][2]) 所以当前代码里的：

```python
except TypeError:
    legacy_kwargs = {
        "lang": language,
        "use_angle_cls": use_textline_orientation,
    }
```

以及：

```python
raw_result = engine.ocr(str(input_path), cls=use_textline_orientation)
```

不是完全乱写，而是在兼容 v2 / v3 两套 API。问题是：**如果项目依赖已经固定 v3，这些 fallback 就应该删掉；如果仍支持 v2，则应该明确写成兼容策略，而不是隐藏在 TypeError 里。**

---

# 我确定要改的点

## 1. 内部 helper 加下划线

当前这些函数都是 worker 模块内部实现，不是 public API：

```python
redirect_stdout_to_stderr
write_response
get_engine
extend_strings
collect_texts
normalize_lines
recognize_image
handle_request
run_protocol
run_single
parse_args
```

建议改为：

```python
_redirect_stdout_to_stderr
_write_response
_get_engine
_extend_strings
_collect_texts
_normalize_lines
_recognize_image
_handle_request
_run_protocol
_run_single
_parse_args
```

`main()` 可以保持 public，因为它是脚本入口。

注意：这会影响直接 import worker helper 的测试。如果测试只通过子进程 / main 调用，就直接改；如果测试 import 了这些 helper，测试同步改成调用 `_xxx` 或只测 `main()` / protocol 行为。

---

## 2. `OcrResult` 不在这个文件里；这里不用动

`worker.py` 返回的是 JSON dict，不定义 `OcrResult`。这个文件不要引入 dataclass，也不要把 processor 的结果模型挪过来。worker 协议保持 JSON dict 就好。

---

## 3. `handle_request()` 里删除 `Path(str(input_value))`

当前：

```python
input_value = request.get("input", "")
if not input_value:
    return {"ok": False, "error": "INVALID_REQUEST", "message": "missing input"}

input_path = Path(str(input_value))
```

这里 `str(input_value)` 是宽容转换。worker 协议已经由 processor 控制，`input` 应该是字符串路径。建议改成：

```python
input_value = request.get("input")
if not input_value:
    return {"ok": False, "error": "INVALID_REQUEST", "message": "缺少 input 参数"}

input_path = Path(input_value)
```

如果要更严格，可以确认 `isinstance(input_value, str)`，但我不建议在这一轮扩大协议校验。至少不要 `str(...)` 修正错误类型。

---

## 4. `language=str(request.get("lang") or "ch")` 改掉

当前：

```python
language=str(request.get("lang") or "ch")
```

这也是宽容转换。建议：

```python
language=request.get("lang", "ch")
```

因为 processor 发过来的就是字符串。

---

## 5. message 文本改中文，error code 保持英文

保留这些协议字段：

```text
INVALID_JSON
INVALID_REQUEST
INPUT_NOT_FOUND
OCR_BACKEND_UNAVAILABLE
OCR_FAILED
OCR_NO_TEXT
paddleocr
```

这些是机器协议，不翻译。

但 `message` 改中文：

```python
{"ok": False, "error": "INVALID_REQUEST", "message": "缺少 input 参数"}
```

```python
{
    "ok": False,
    "error": "INPUT_NOT_FOUND",
    "message": f"输入图片文件不存在：{input_path}",
}
```

```python
{
    "ok": False,
    "error": "OCR_NO_TEXT",
    "message": "OCR 未识别出文本。",
    ...
}
```

`processor.py` 会把 `message` 作为人类可读错误内容，中文更符合现在规则。

---

## 6. `redirect_stdout_to_stderr()` 保留

这段非常重要：

```python
_PROTOCOL_STDOUT = sys.stdout

@contextlib.contextmanager
def redirect_stdout_to_stderr():
    original_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = original_stdout
```

以及：

```python
def main() -> int:
    sys.stdout = sys.stderr
```

这能保证 PaddleOCR / Paddle 内部打印不会污染 stdout JSON 协议。`_PROTOCOL_STDOUT` 在模块加载时保存了原始 stdout，之后协议响应仍写到原始 stdout。这个设计是对的，不要改掉。

---

## 7. `collect_texts()` 不要强拆，但改成内部函数

`collect_texts()` 当前比较宽，是为了兼容 PaddleOCR 不同版本 / 不同结果对象：

```python
preferred_keys = ("rec_texts", "text", "transcription", "texts")
```

这类“对第三方输出结构做解析”不同于对模型参数做宽容转换。PaddleOCR v3 的 `predict()` 返回对象和 legacy `ocr()` 返回结构差异较大，当前递归收集文本有现实意义。

建议保留逻辑，只改成 `_collect_texts()`，并把内部调用同步更新。

---

# 建议核心修改片段

只贴关键片段，避免整文件太长。

## 内部函数命名

```python
@contextlib.contextmanager
def _redirect_stdout_to_stderr():
    original_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = original_stdout


def _write_response(response: Dict[str, Any]) -> None:
    _PROTOCOL_STDOUT.write(json.dumps(response, ensure_ascii=False) + "\n")
    _PROTOCOL_STDOUT.flush()
```

---

## `handle_request`

```python
def _handle_request(request: Dict[str, Any]) -> Dict[str, Any]:
    if request.get("shutdown", False):
        return {"ok": True, "backend": "paddleocr", "shutdown": True}

    input_value = request.get("input")
    if not input_value:
        return {"ok": False, "error": "INVALID_REQUEST", "message": "缺少 input 参数"}

    input_path = Path(input_value)
    if not input_path.is_file():
        return {
            "ok": False,
            "error": "INPUT_NOT_FOUND",
            "message": f"输入图片文件不存在：{input_path}",
        }

    try:
        return _recognize_image(
            input_path=input_path,
            language=request.get("lang", "ch"),
            use_doc_orientation_classify=request.get("use_doc_orientation_classify", False),
            use_doc_unwarping=request.get("use_doc_unwarping", False),
            use_textline_orientation=request.get("use_textline_orientation", False),
        )
    except RuntimeError as e:
        return {"ok": False, "error": "OCR_BACKEND_UNAVAILABLE", "message": str(e)}
    except Exception as e:
        return {"ok": False, "error": "OCR_FAILED", "message": f"{e.__class__.__name__}: {e}"}
```

这里我暂时没有加 `isinstance(..., str/bool)`，因为当前 processor 发送协议已经固定。若后续你想让 worker 协议更硬，可以加“严格类型错误返回”，但不在当前 review 扩大。

---

## OCR 无文本 message

```python
if not text:
    return {
        "ok": False,
        "error": "OCR_NO_TEXT",
        "message": "OCR 未识别出文本。",
        "backend": "paddleocr",
        "text": "",
        "line_count": 0,
    }
```

---

## JSON 错误 message 保留异常文本即可

```python
except json.JSONDecodeError as e:
    _write_response({"ok": False, "error": "INVALID_JSON", "message": str(e)})
    continue
```

这里 `str(e)` 是 JSON 解析库给出的原始错误，保留英文可以接受。

---

# 需要人工确认后再小修的点

## 1. PaddleOCR 版本：v3 还是 v2 legacy

当前代码同时支持：

```text
v3:
PaddleOCR(use_doc_orientation_classify=..., use_doc_unwarping=..., use_textline_orientation=...)
engine.predict(...)

v2 legacy:
PaddleOCR(use_angle_cls=...)
engine.ocr(..., cls=...)
```

这块必须看依赖锁定情况：

```text
pyproject.toml
requirements.txt
poetry.lock / uv.lock / pip-tools lock
实际部署镜像
```

确认后处理：

### 如果项目锁定 PaddleOCR v3

删除：

```python
except TypeError:
    legacy_kwargs = ...
```

删除：

```python
else:
    raw_result = engine.ocr(...)
```

只保留：

```python
engine = PaddleOCR(
    lang=language,
    use_doc_orientation_classify=use_doc_orientation_classify,
    use_doc_unwarping=use_doc_unwarping,
    use_textline_orientation=use_textline_orientation,
)

raw_result = engine.predict(input=str(input_path))
```

### 如果项目仍可能用 PaddleOCR v2

不要隐藏在 `TypeError` 里。建议显式按配置区分 worker API 版本，例如：

```text
OCR_PADDLE_API_VERSION=v3 / legacy
```

但这会新增配置，当前不建议除非部署确实需要双版本。

我的建议：**优先确认依赖，若已是 v3，就删除 legacy fallback。**

---

## 2. 是否保留 `_ENGINES` cache

当前：

```python
_ENGINES: Dict[Tuple[str, bool, bool, bool], Any] = {}
```

worker 是单独进程，processor 侧有 `_worker_lock` 串行请求，所以 engine cache 在当前设计下问题不大。它能避免每张图重复初始化 PaddleOCR，收益明显。

暂时保留。

---

## 3. 是否清理 `argparse` single-run 模式

`run_single()` 对调试很有用，当前保留。它不污染工具主链路。

---

# 暂不建议改的点

## 1. 不要删除 stdout/stderr 重定向

这是 worker 协议稳定性的关键。

## 2. 不要删除非 JSON stdout 跳过逻辑

PaddleOCR / Paddle 可能污染 stdout。processor 侧已经允许跳过有限非 JSON 行，worker 侧也要尽量把第三方输出导向 stderr。这里的“协议韧性”是必要的，不属于无意义防御。

## 3. 不要为了简化删掉 `_ENGINES`

PaddleOCR 初始化成本高，worker 内缓存是合理的。等确认版本和线程/进程模型后再考虑更细生命周期。

---

# 本文件验收标准

```bash
rg "def redirect_stdout_to_stderr|def write_response|def get_engine|def collect_texts|def normalize_lines|def recognize_image|def handle_request|def run_protocol|def run_single|def parse_args" src/chat/application/document_parse/ocr/worker.py
```

应改成 `_xxx`，`main` 保持无下划线。

```bash
rg "Path\\(str\\(|language=str\\(" src/chat/application/document_parse/ocr/worker.py
```

应无结果。

```bash
rg "OCR produced no text|missing input|input file not found" src/chat/application/document_parse/ocr/worker.py
```

应改为中文 message。

人工确认：

```text
worker stdout 仍只输出 JSON response。
PaddleOCR import / predict 期间 stdout 被重定向到 stderr。
main() 仍然把 sys.stdout 指向 sys.stderr。
_PROTOCOL_STDOUT 仍在模块加载时捕获原 stdout。
processor.py 的 worker 协议没有改变字段名。
```

---

