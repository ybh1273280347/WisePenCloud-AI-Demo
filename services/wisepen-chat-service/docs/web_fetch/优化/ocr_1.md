这版 `LocalOcrProcessor` 已经是比较完整的实现了：下载限额、图片类型检测、PDF 渲染、worker JSON line 协议、worker 复用、idle reaper、超时 reset 都覆盖到了。整体方向符合我们定的“lazy persistent worker，不在主服务 import paddleocr”的方案。

但这里有几个**必须修**，否则后面容易出现隐蔽问题。

# 必须修

## 1. `_worker_stderr_task` 只能记录一个 worker，重启后可能取消错任务

当前 `_start_worker()` 每次都会：

```python
self._worker_stderr_task = asyncio.create_task(self._drain_worker_stderr(process))
```

但 `_stop_worker()` 里无条件 cancel 当前 `self._worker_stderr_task`。如果 worker 重启过程中旧任务还没结束，新任务已经被覆盖，就可能出现“取消新 task、旧 task 泄漏”的情况。

建议把 stderr task 和 process 绑定，最小改法是增加：

```python
self._worker_stderr_process: Optional[asyncio.subprocess.Process] = None
```

启动时：

```python
self._worker_stderr_task = asyncio.create_task(self._drain_worker_stderr(process))
self._worker_stderr_process = process
```

停止时只取消对应进程的 task：

```python
if (
    self._worker_stderr_task is not None
    and self._worker_stderr_process is process
):
    self._worker_stderr_task.cancel()
    self._worker_stderr_task = None
    self._worker_stderr_process = None
```

更干净的做法是封装一个 `OcrWorkerState(process, stderr_task)` dataclass，但这轮不必大重构。

---

## 2. `_stop_worker()` 没有在 lock 内被 idle reaper 调用，可能和请求并发冲突

`_request_worker()` 里持有：

```python
async with self._worker_lock:
```

但 `_idle_reaper()` 里直接：

```python
await self._stop_worker()
```

这可能和正在进行的 OCR 请求冲突。场景是：

```text
request 正在写 stdin / 读 stdout
idle_reaper 判断 idle 超时
直接 stop worker
request 读到 EOF 或 BrokenPipe
```

虽然 `_request_worker()` 会重试，但这是不必要的不稳定。

建议 `_idle_reaper()` stop 前也拿锁：

```python
async with self._worker_lock:
    process = self._worker_process
    if process is None or process.returncode is not None:
        return

    idle_seconds = time.monotonic() - self._last_worker_use
    if idle_seconds >= self._worker_idle_ttl_seconds:
        await self._stop_worker()
        ...
        return
```

这样 worker 生命周期由同一把锁保护。

---

## 3. `_reset_worker()` 在 `_request_worker()` 的 lock 内调用没问题，但 `extract_from_url()` 超时时也会调用，不受 lock 保护

当前：

```python
except asyncio.TimeoutError:
    await self._reset_worker()
```

这里没有拿 `_worker_lock`。如果另一个并发 OCR 正在使用 worker，这里会直接 reset。

建议新增：

```python
async def _reset_worker_locked(self) -> None:
    async with self._worker_lock:
        await self._reset_worker()
```

超时分支用：

```python
await self._reset_worker_locked()
```

或者让 `_reset_worker()` 自己拿锁，但注意 `_request_worker()` 内部已经持锁调用，会死锁。所以最好新增 locked wrapper。

---

## 4. worker 协议没有 request id，并发时必须串行，目前靠 lock 串行是可以的

现在 `_request_worker()` 整个 write/read 都在 `_worker_lock` 里，所以不会出现两个请求同时读 stdout 的错乱。这个设计是对的。

但要注意：既然没有 request id，就必须保持这个锁，不要以后为了并发把锁粒度缩小。建议加注释：

```python
# The worker protocol has no request id, so write/read must stay serialized.
```

---

## 5. `_start_worker()` 的路径推导比较脆弱

当前：

```python
src_root = Path(__file__).resolve().parents[4]
service_root = Path(__file__).resolve().parents[5]
```

这个依赖当前文件层级。只要目录结构稍微变，就会错。

按当前路径：

```text
chat/application/web_fetch/ocr/local_ocr_processor.py
```

`parents[4]` / `parents[5]` 可能是对的，但不够自解释。

建议至少改名并注释：

```python
current = Path(__file__).resolve()
src_root = current.parents[4]      # .../src
project_root = current.parents[5]  # repo root
```

更好的做法是直接使用 settings 配置 `PROJECT_ROOT` / `SRC_ROOT`，但这轮不要扩配置。最小修是用变量名和注释降低风险。

---

# 建议修

## 6. `IMAGE_EXTENSIONS` 同时承担类型集合和 suffix 映射，命名不准确

当前：

```python
IMAGE_EXTENSIONS = {
    "png": ".png",
    "jpg": ".jpg",
    "jpeg": ".jpg",
    "webp": ".webp",
}
```

它实际是 type -> suffix map。建议改名：

```python
IMAGE_SUFFIX_BY_TYPE = {
    ...
}
```

对应：

```python
if input_type in IMAGE_SUFFIX_BY_TYPE:
...
suffix = IMAGE_SUFFIX_BY_TYPE[image_type]
```

---

## 7. `detect_webp_dimensions()` 不支持 VP8L

当前只支持：

```python
VP8X
VP8
```

不支持无损 WebP 的 `VP8L`，这会导致某些 WebP 无法预先检查尺寸。但后续仍会写入文件给 OCR 处理。

这不是阻塞，但如果要支持 webp，就补一下 VP8L；如果不想管，第一版可以直接不支持 webp，减少边界。

建议二选一：

```text
A. 补 VP8L 尺寸解析
B. 第一版移除 webp 支持，只支持 png/jpg/jpeg
```

我倾向 B，简单稳。

---

## 8. `_download_source()` 将所有 chunks 放入 list，最后 join，可以接受但不够省内存

当前有 max 50MB，所以问题不大：

```python
chunks: List[bytes] = []
...
data = b"".join(chunks)
```

可以保留。若要优化，用 `bytearray`：

```python
buffer = bytearray()
...
buffer.extend(chunk)
...
return bytes(buffer), content_type
```

这不是必须。

---

## 9. `_read_worker_response()` 对非 JSON stdout 容忍 20 行，有必要但要确保 worker 日志不打 stdout

当前如果 worker 把日志打到 stdout，会被这里吞掉直到 20 行后失败。

更好的约束是：**worker 日志全部走 stderr，stdout 只输出 JSON line**。

这个需要在 `paddle_ocr_worker.py` review 时重点检查。

---

## 10. `OcrResult.error` 需要约定“纯错误原因”

你前面 `WebFetchTool.format_ocr_failure()` 已经想把 error 当错误原因。这里当前 error 有些带句号，有些像完整句：

```python
"OCR processing timed out."
"OCR backend is not available or not installed."
```

这可以接受。但不要让 worker 返回 `"OCR failed: xxx"`。这也要在 worker 里约束。

---

# 可以保留的设计

## 1. `detect_ocr_input_type()` 放在模块级合理

它是纯函数，不用进类。结合 magic bytes / content-type / URL suffix 的判断完整，保留合理。

## 2. `_request_worker()` 串行锁合理

因为 worker 协议没有 request id，所以持锁覆盖 write + read 是正确设计。

## 3. idle reaper 思路合理

长驻服务里 OCR worker 空闲后释放内存，这是对的。只需要修锁。

---

# 给 Codex 的小修提示词

```text
请小幅修复 LocalOcrProcessor，不要重构整体设计，不要引入新架构。

必须修：

1. idle reaper 停止 worker 时必须持有 _worker_lock。

当前 _idle_reaper 中直接 await self._stop_worker()。
请改成在 async with self._worker_lock: 内重新读取 self._worker_process、重新计算 idle_seconds，然后再调用 _stop_worker()。

原因：
worker JSON line 协议没有 request id，_request_worker 依赖 _worker_lock 串行写入和读取。
idle reaper 不能绕过锁停止 worker。

2. extract_from_url 超时时 reset worker 也必须持锁。

新增方法：

async def _reset_worker_locked(self) -> None:
    async with self._worker_lock:
        await self._reset_worker()

TimeoutError 分支改为：
await self._reset_worker_locked()

不要让 _reset_worker 自己拿锁，因为 _request_worker 已经在持锁状态下调用 _reset_worker，直接加锁会死锁。

3. 修复 _worker_stderr_task 和 process 绑定问题。

新增成员：
self._worker_stderr_process: Optional[asyncio.subprocess.Process] = None

_start_worker 中：
self._worker_stderr_task = asyncio.create_task(self._drain_worker_stderr(process))
self._worker_stderr_process = process

_stop_worker 中只取消对应 process 的 stderr task：
if (
    self._worker_stderr_task is not None
    and self._worker_stderr_process is process
):
    self._worker_stderr_task.cancel()
    self._worker_stderr_task = None
    self._worker_stderr_process = None

4. 在 _request_worker 中加注释：
# The worker protocol has no request id, so each write/read pair must stay serialized.

5. 将 IMAGE_EXTENSIONS 重命名为 IMAGE_SUFFIX_BY_TYPE。
对应所有引用一起改。

建议修：

6. _start_worker 中路径推导增加清晰变量和注释。

current = Path(__file__).resolve()
src_root = current.parents[4]      # project src root
service_root = current.parents[5]  # repository root

保持不变：
- 不改 lazy persistent worker 设计
- 不引入独立 OCR 服务
- 不把 paddleocr import 到主服务
- 不改 ToolContentStore 协议
- 不改 WebFetchTool schema
- 不新增 ocr_extract tool
- 不改 LocalOcrProcessor 对外 API
- 不改 worker JSON line 协议
- 不增加 request id
- 保持 typing 风格
```

# 结论

这版主要问题不在 OCR 业务逻辑，而在 **worker 生命周期并发安全**：

```text
idle reaper 要持锁
timeout reset 要持锁
stderr task 要和 process 绑定
```

修完这三个点后，这个 `LocalOcrProcessor` 的主体设计就比较稳了。
