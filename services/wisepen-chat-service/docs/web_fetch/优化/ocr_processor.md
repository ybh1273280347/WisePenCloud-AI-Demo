## 结论

`ocr/processor.py` 的整体方向是对的：它只处理**本地图片 OCR 请求**、维护 OCR worker 生命周期、通过 JSON line 协议和 worker 通信，没有 URL 下载、没有 PDF 整体 OCR、没有 web_fetch 回潮。当前代码中 `recognize_image()` 只接收 `Path`，通过 `_recognize_image_text()` 向 worker 发送本地图片路径；worker 进程也通过 `chat.application.document_parse.ocr.worker` 启动，职责边界是清楚的。

这文件主要是**可读性和边界语义小修**，不是架构问题。

---

## 第三方库 API 确认

这个文件本身没有直接调用 PaddleOCR / PP-Structure API，它只是启动子进程并通过 stdin/stdout 发送 JSON 请求。这里主要涉及 Python 标准库 `asyncio.create_subprocess_exec`、`asyncio.Lock`、`asyncio.wait_for`、`json`，不需要针对 PaddleOCR API 做判断。

PaddleOCR 的真实调用应放到下一个要 review 的 `ocr/worker.py` 里确认。

---

## 我确定要改的点

### 1. `OcrResult` 加 `slots=True`

当前：

```python
@dataclass
class OcrResult:
```

建议：

```python
@dataclass(slots=True)
class OcrResult:
```

这个结果对象结构固定，和前面 `models.py` 的风格一致。

---

### 2. 给 public class 加 docstring

```python
class OcrProcessor:
    """本地 OCR worker 处理器。"""
```

这个类是 public service 组件，不是内部 helper。

---

### 3. 删除 `image_path = Path(image_path)` 宽容转换

当前：

```python
async def _recognize_image(self, image_path: Path) -> OcrResult:
    image_path = Path(image_path)
```

`recognize_image(image_path: Path)` 已经声明了输入契约，不要再次宽容转换。

改成：

```python
async def _recognize_image(self, image_path: Path) -> OcrResult:
    if not image_path.is_file():
        ...
```

---

### 4. 把用户/日志可见英文改成中文

当前文件里有大量英文错误和日志文本，例如：

```python
"OCR is disabled."
"OCR backend is not available or not installed."
"OCR processing timed out."
"OCR input file not found: ..."
"OCR produced no text."
"OCR worker request failed"
```

按新规则，应改成中文。协议字段名、backend 名称、异常类名不翻译。

建议改法示例：

```python
return OcrResult(ok=False, error="OCR 已禁用。", backend=self._backend)
```

```python
raise _OcrProcessingError(f"OCR 输入文件不存在：{image_path}")
```

```python
return OcrResult(ok=False, error="OCR 未识别出文本。", backend=self._backend)
```

```python
raise _OcrWorkerError("OCR worker 请求失败")
```

日志 stage 也建议中文：

```python
log_fail("OCR 识别", "请求超时", path=str(image_path), timeout=self._timeout)
log_ok("OCR 识别完成", backend=self._backend, length=len(text), path=str(image_path))
log_fail("OCR worker 协议", f"stdout 非 JSON 内容：{decoded}")
```

---

### 5. 不要用 `assert` 校验 subprocess pipe

当前：

```python
assert process.stdin is not None
assert process.stdout is not None
```

`assert` 可能在 Python 优化模式下被移除，不适合运行时协议校验。这里是 worker 子进程协议边界，应该显式报错。

改成：

```python
if process.stdin is None or process.stdout is None:
    raise _OcrWorkerError("OCR worker 标准输入或标准输出不可用")
```

---

### 6. 抽出几个内部常量

当前有几个 magic number / 字符串：

```python
for attempt in range(2):
for _ in range(20):
timeout=5.0
"paddleocr"
"lazy_persistent"
```

建议：

```python
_BACKEND_PADDLEOCR = "paddleocr"
_WORKER_MODE_LAZY_PERSISTENT = "lazy_persistent"
_WORKER_REQUEST_MAX_ATTEMPTS = 2
_WORKER_RESPONSE_MAX_NON_JSON_LINES = 20
_WORKER_SHUTDOWN_TIMEOUT_SECONDS = 5.0
_SHUTDOWN_REQUEST = {"shutdown": True}
```

然后替换对应位置。
这都是当前文件内部常量，用下划线。

---

## 建议关键修改片段

### 顶部

```python
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from chat.core.config.app_settings import settings
from common.logger import log_fail, log_ok


@dataclass(slots=True)
class OcrResult:
    ok: bool
    text: str = ""
    error: Optional[str] = None
    backend: str = "paddleocr"


class _OcrProcessingError(RuntimeError):
    pass


class _OcrWorkerError(RuntimeError):
    pass


_BACKEND_PADDLEOCR = "paddleocr"
_WORKER_MODE_LAZY_PERSISTENT = "lazy_persistent"
_WORKER_REQUEST_MAX_ATTEMPTS = 2
_WORKER_RESPONSE_MAX_NON_JSON_LINES = 20
_WORKER_SHUTDOWN_TIMEOUT_SECONDS = 5.0
_SHUTDOWN_REQUEST = {"shutdown": True}
```

---

### `__init__`

```python
class OcrProcessor:
    """本地 OCR worker 处理器。"""

    def __init__(
        self,
        timeout: float,
        enabled: bool = settings.ENABLE_OCR,
        backend: str = settings.OCR_BACKEND,
        language: str = settings.OCR_LANGUAGE,
        worker_mode: str = settings.OCR_WORKER_MODE,
        worker_idle_ttl_seconds: int = settings.OCR_WORKER_IDLE_TTL_SECONDS,
        use_doc_orientation_classify: bool = settings.OCR_USE_DOC_ORIENTATION_CLASSIFY,
        use_doc_unwarping: bool = settings.OCR_USE_DOC_UNWARPING,
        use_textline_orientation: bool = settings.OCR_USE_TEXTLINE_ORIENTATION,
    ):
        if worker_mode != _WORKER_MODE_LAZY_PERSISTENT:
            raise ValueError(f"不支持的 OCR worker 模式：{worker_mode}")
```

---

### `recognize_image`

```python
    async def recognize_image(self, image_path: Path) -> OcrResult:
        if not self._enabled:
            return OcrResult(ok=False, error="OCR 已禁用。", backend=self._backend)

        if self._backend != _BACKEND_PADDLEOCR:
            return OcrResult(
                ok=False,
                error="OCR 后端不可用或未安装。",
                backend=self._backend,
            )

        try:
            return await asyncio.wait_for(self._recognize_image(image_path), timeout=self._timeout)
        except asyncio.TimeoutError:
            await self._reset_worker_locked()
            log_fail("OCR 识别", "请求超时", path=str(image_path), timeout=self._timeout)
            return OcrResult(ok=False, error="OCR 处理超时。", backend=self._backend)
        except _OcrProcessingError as e:
            log_fail("OCR 识别", e, path=str(image_path))
            return OcrResult(ok=False, error=str(e), backend=self._backend)
        except Exception as e:
            log_fail("OCR 识别", e, path=str(image_path))
            return OcrResult(
                ok=False,
                error=f"{e.__class__.__name__}: {e}",
                backend=self._backend,
            )
```

---

### `_recognize_image`

```python
    async def _recognize_image(self, image_path: Path) -> OcrResult:
        if not image_path.is_file():
            raise _OcrProcessingError(f"OCR 输入文件不存在：{image_path}")

        text = await self._recognize_image_text(image_path)
        if not text:
            return OcrResult(ok=False, error="OCR 未识别出文本。", backend=self._backend)

        log_ok("OCR 识别完成", backend=self._backend, length=len(text), path=str(image_path))
        return OcrResult(ok=True, text=text, backend=self._backend)
```

---

### `_recognize_image_text`

这里可以保留 `response.get(...)`，因为 worker 是独立进程，JSON 协议边界需要解析和容错；这不是模型参数宽容。

```python
        error = response.get("error")
        if error == "OCR_BACKEND_UNAVAILABLE":
            log_fail("OCR 识别", response.get("message", "后端不可用"))
            raise _OcrProcessingError("OCR 后端不可用或未安装。")

        message = response.get("message") or error or "未知 OCR worker 错误"
        raise _OcrProcessingError(str(message))
```

---

### `_request_worker`

```python
    async def _request_worker(self, request: Dict[str, Any]) -> Dict[str, Any]:
        for attempt in range(_WORKER_REQUEST_MAX_ATTEMPTS):
            async with self._worker_lock:
                process = await self._ensure_worker()

                try:
                    if process.stdin is None or process.stdout is None:
                        raise _OcrWorkerError("OCR worker 标准输入或标准输出不可用")

                    process.stdin.write(json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n")
                    await process.stdin.drain()
                    response = await self._read_worker_response(process)
                    self._last_worker_use = time.monotonic()
                    self._ensure_idle_reaper()
                    return response
                except Exception as e:
                    await self._reset_worker()
                    if attempt == 0:
                        log_fail("OCR worker", e)
                        continue
                    raise

        raise _OcrWorkerError("OCR worker 请求失败")
```

---

### `_read_worker_response`

```python
    async def _read_worker_response(self, process: asyncio.subprocess.Process) -> Dict[str, Any]:
        if process.stdout is None:
            raise _OcrWorkerError("OCR worker 标准输出不可用")

        for _ in range(_WORKER_RESPONSE_MAX_NON_JSON_LINES):
            line = await asyncio.wait_for(process.stdout.readline(), timeout=self._timeout)
            if not line:
                raise _OcrWorkerError("OCR worker 在返回响应前退出")

            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded:
                continue

            try:
                parsed = json.loads(decoded)
            except json.JSONDecodeError:
                log_fail("OCR worker 协议", f"stdout 非 JSON 内容：{decoded}")
                continue

            if not isinstance(parsed, dict):
                raise _OcrWorkerError("OCR worker 返回的 JSON 不是对象")

            return parsed

        raise _OcrWorkerError("OCR worker 返回了过多非 JSON 行")
```

---

### `_start_worker` / `_idle_reaper`

```python
log_ok("OCR worker 启动", backend=self._backend, mode=self._worker_mode, pid=process.pid)
```

```python
log_ok("OCR worker 空闲退出", backend=self._backend, idle_seconds=round(idle_seconds, 2))
```

---

### `_stop_worker`

```python
process.stdin.write(json.dumps(_SHUTDOWN_REQUEST).encode("utf-8") + b"\n")
await asyncio.wait_for(process.wait(), timeout=_WORKER_SHUTDOWN_TIMEOUT_SECONDS)
```

```python
process.terminate()
await asyncio.wait_for(process.wait(), timeout=_WORKER_SHUTDOWN_TIMEOUT_SECONDS)
```

---

## 需要人工确认后再小修的点

### 1. `OcrResult` 是否应该进入 `models.py`

`OcrResult` 当前只属于 OCR processor / adapter 协议，放在 `processor.py` 里可以接受。
如果后续 `OcrResult` 被多个模块直接 import，才考虑放到 `ocr/models.py`。

当前不改。

---

### 2. `_PADDLE_WORKER_ENV` 是否仍然必要

这些环境变量：

```python
"FLAGS_use_mkldnn": "0"
"FLAGS_enable_onednn": "0"
"FLAGS_cpu_math_library_num_threads": "1"
"DNNL_MAX_CPU_ISA": "NONE"
```

看起来是为了规避 Paddle / oneDNN / CPU 指令集相关问题。这个属于运行稳定性配置，不要在 review 中删除。等 worker 压测后再判断。

---

### 3. worker stdout 非 JSON 是否继续忽略

当前策略是最多跳过 20 行非 JSON stdout。这个是为了防止第三方库污染 stdout，属于 worker 协议韧性。
不要因为“不做防御性编程”就删掉。这里不是模型参数，而是独立子进程协议边界。

---

## 暂不建议改的点

### 1. 不建议改 worker 生命周期机制

`_ensure_worker / _idle_reaper / close / _stop_worker` 结构完整，先不要重构。

### 2. 不建议把 worker request 改成 request_id 多路复用

当前 `_worker_lock` 串行化 write/read pair 是对的。worker 协议没有 request id，不应并发写同一个 worker。

### 3. 不建议删除 stderr drain

PaddleOCR / 底层库可能写 stderr，不 drain 可能导致子进程阻塞。保留。

---

## 本文件验收标准

```bash
rg "Path\\(image_path\\)|assert process\\.stdin|assert process\\.stdout" src/chat/application/document_parse/ocr/processor.py
```

应无结果。

```bash
rg "OCR is disabled|OCR failed|OCR produced no text|OCR worker request failed|request timed out" src/chat/application/document_parse/ocr/processor.py
```

应无英文错误文本残留。

```bash
rg "_WORKER_REQUEST_MAX_ATTEMPTS|_WORKER_RESPONSE_MAX_NON_JSON_LINES|_WORKER_SHUTDOWN_TIMEOUT_SECONDS" src/chat/application/document_parse/ocr/processor.py
```

应有结果。

人工确认：

```text
OcrProcessor 不下载 URL。
OcrProcessor 不处理 PDF。
OcrProcessor 只把本地图片路径发给 worker。
worker write/read 仍在同一个 lock 内。
close() 仍可重复调用。
```

---
