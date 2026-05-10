基本不会受到接口层面的影响。你的 Python 封装和最终优化版 JS 是匹配的，因为它们都遵循同一个子进程协议：

| 项目       | JS 最终优化版行为             | Python 当前代码是否兼容 |
| -------- | ---------------------- | --------------: |
| `stdout` | 只输出 Markdown           |              兼容 |
| `stderr` | 输出日志、错误、截图片段           |              兼容 |
| 成功退出码    | `0`                    |              兼容 |
| 失败退出码    | 非 `0`，通常是 `1`          |              兼容 |
| 超时控制     | JS 内部单步超时，Python 外部总超时 |              兼容 |

所以结论是：

> **你的 Python 调用代码不需要因为 JS 最终优化版而大改。**

但有几个边界点值得注意。

---

## 1. `stderr` 会多一些正常诊断日志，但不影响当前逻辑

最终 JS 版里可能有：

```js
process.stderr.write(
  `Extracted text length: ${pageData.selectedTextLength}, fallbackToBody: ${pageData.usedBodyFallback}\n`
);
```

你的 Python 代码只有在失败时才读取并记录 `stderr`：

```python
if process.returncode != 0:
    err_msg = stderr.decode("utf-8", errors="replace").strip()[:_MAX_ERROR_SNIPPET]
```

所以成功时即使 JS 往 `stderr` 写了日志，也不会影响返回值。

这没问题。

---

## 2. `stdout` 仍然只会是 Markdown，因此不会破坏 `markdown = stdout.decode(...).strip()`

最终 JS 没有改成 JSON，也没有往 `stdout` 加标题、状态码、日志，所以这一段仍然正确：

```python
markdown = stdout.decode("utf-8").strip()
```

这一点很重要。当前 Python 代码不需要变。

---

## 3. JS 性能优化可能改变“抓到的内容”，但不会破坏 Python 调用

最终 JS 版采用了：

```js
image
media
font
stylesheet
```

资源拦截，以及正文容器优先提取。

这会带来两个结果：

### 正面影响

多数页面会：

```text
更快
输出更短
Markdown 更干净
导航栏、广告、页脚更少
```

### 潜在影响

少数页面可能因为依赖 CSS、图片加载事件、懒加载策略，导致正文没有完全渲染。

这种情况下，Python 侧看到的表现可能是：

```python
markdown == ""
```

或者 JS 返回非 0，最终：

```python
return None
```

也就是说，Python 代码不会坏，但个别 URL 的抓取结果可能和旧版不同。

既然你说“显著提升性能的都接受，没有历史包袱”，这点可以接受。

---

## 4. `timeout=120.0` 基本够用，但可以略微提高

最终 JS 的主要耗时上限大致是：

```text
browser launch: 不固定
page.goto: 最多 60s
networkidle: 最多 8s
动态滚动: 大约 8 * 0.7~1.0s
post scroll idle: 1.2s
turndown 转换: 取决于页面大小
browser close: 通常很快
```

所以正常情况下 120 秒是够的。

但如果你批量跑复杂站点，建议改成：

```python
def __init__(self, timeout: float = 150.0):
```

不是必须，但更稳。

---

## 5. `_MAX_SUBPROCESS_BUFFER` 不是严格的总输出上限

你现在写的是：

```python
_MAX_SUBPROCESS_BUFFER = 10 * 1024 * 1024
```

然后：

```python
limit=_MAX_SUBPROCESS_BUFFER
```

这里要注意：`limit` 是 `asyncio.StreamReader` 的缓冲限制参数，不是“stdout 最多只能读取 10MB”的硬限制。

也就是说，如果网页转出的 Markdown 非常大，`process.communicate()` 仍然可能把完整 stdout 读进内存。

最终 JS 因为做了正文提取和 DOM 清理，通常会降低这个风险，但它不是严格防护。

当前可以先不改。若以后要硬限制 Markdown 大小，需要在 JS 侧控制输出长度，或者 Python 侧不要用 `communicate()` 一次性读完。

---

## 6. 超时后的清理逻辑可以稍微更稳

你现在是：

```python
except asyncio.TimeoutError:
    log_fail("本地脚本执行", f"超时 {self._timeout}s", url=url)
    if process and process.returncode is None:
        process.kill()
        await process.wait()
    return None
```

这通常可用。

不过对于带 `stdout=PIPE`、`stderr=PIPE` 的子进程，更稳的做法是 kill 后再尝试 `communicate()` 一次，把管道收干净：

```python
except asyncio.TimeoutError:
    log_fail("本地脚本执行", f"超时 {self._timeout}s", url=url)

    if process and process.returncode is None:
        process.kill()
        try:
            await asyncio.wait_for(process.communicate(), timeout=5)
        except Exception:
            await process.wait()

    return None
```

这不是因为 JS 优化版要求，而是子进程管道场景下更稳。

---

## 7. 最小推荐修改版

你的代码整体可以保留，只建议改两个地方：

### 7.1 默认超时可提高到 150 秒

```python
class LocalScriptFetcher:
    def __init__(self, timeout: float = 150.0):
```

### 7.2 超时和异常时的进程清理函数抽出来

```python
async def _kill_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return

    process.kill()

    try:
        await asyncio.wait_for(process.communicate(), timeout=5)
    except Exception:
        await process.wait()
```

然后你的类可以改成：

```python
import asyncio
import shutil
from pathlib import Path
from typing import Optional

from common.logger import log_ok, log_fail, log_error


_MAX_DIR_TRAVERSAL = 10
_MAX_SUBPROCESS_BUFFER = 10 * 1024 * 1024
_MAX_ERROR_SNIPPET = 500


def _find_root_dir() -> Path:
    """向上查找包含 scripts/local_web_fetcher.js 的最近父目录"""
    current = Path(__file__).resolve().parent

    for _ in range(_MAX_DIR_TRAVERSAL):
        if (current / "scripts" / "local_web_fetcher.js").exists():
            return current
        current = current.parent

    raise FileNotFoundError("找不到项目根目录（包含 scripts/local_web_fetcher.js）")


ROOT_DIR = _find_root_dir()
SCRIPT_PATH = ROOT_DIR / "scripts" / "local_web_fetcher.js"


async def _kill_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return

    process.kill()

    try:
        await asyncio.wait_for(process.communicate(), timeout=5)
    except Exception:
        await process.wait()


class LocalScriptFetcher:
    def __init__(self, timeout: float = 150.0):
        if not SCRIPT_PATH.is_file():
            log_error("本地脚本初始化", f"未找到 JS 脚本: {SCRIPT_PATH}")
            raise FileNotFoundError(f"未找到 JS 脚本: {SCRIPT_PATH}")

        node_path = shutil.which("node") or shutil.which("node.exe")

        if not node_path or not Path(node_path).is_file():
            log_error("本地脚本初始化", "未检测到 Node.js 运行环境，请确认 Node.js 已安装并加入 PATH")
            raise FileNotFoundError("未检测到 Node.js 运行环境，请确认 Node.js 已安装并加入 PATH")

        self._node_path = node_path
        self._timeout = timeout

        log_ok("本地脚本初始化", node_path=self._node_path, timeout=self._timeout)

    async def fetch(self, url: str) -> Optional[str]:
        process = None

        try:
            process = await asyncio.create_subprocess_exec(
                self._node_path,
                str(SCRIPT_PATH),
                url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=_MAX_SUBPROCESS_BUFFER,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self._timeout,
            )

            if process.returncode != 0:
                err_msg = (
                    stderr.decode("utf-8", errors="replace").strip()[:_MAX_ERROR_SNIPPET]
                    if stderr
                    else ""
                )
                log_fail("本地脚本执行", f"退出码 {process.returncode}: {err_msg}", url=url)
                return None

            markdown = stdout.decode("utf-8", errors="replace").strip()

            if not markdown:
                log_fail("本地脚本执行", "抓取内容为空", url=url)
                return None

            return markdown

        except asyncio.TimeoutError:
            log_fail("本地脚本执行", f"超时 {self._timeout}s", url=url)

            if process:
                await _kill_process(process)

            return None

        except Exception as e:
            log_error("本地脚本执行", e, url=url)

            if process:
                await _kill_process(process)

            return None
```

---

## 最终判断

你的 Python 封装不会被最终优化版 JS 破坏。

真正需要注意的只有三点：

```text
1. JS 输出内容可能更干净、更短，但个别页面结果可能变化
2. timeout=120 基本够，150 更稳
3. 超时后 kill 子进程时，最好用 communicate() 收尾管道
```

其余部分保持现状即可。
