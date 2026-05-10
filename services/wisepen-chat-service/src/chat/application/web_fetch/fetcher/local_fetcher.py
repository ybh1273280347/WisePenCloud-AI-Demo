import asyncio
import shutil
from pathlib import Path
from typing import Optional

from common.logger import log_error, log_fail, log_ok


_MAX_SUBPROCESS_BUFFER = 10 * 1024 * 1024
_MAX_ERROR_SNIPPET = 500
_PROCESS_KILL_TIMEOUT_SECONDS = 5.0

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "local_web_fetcher.js"


def _validate_script_path(script_path: Path) -> None:
    if script_path.is_file():
        return

    log_error("本地脚本初始化", f"未找到 JS 脚本: {script_path}")
    raise FileNotFoundError(f"未找到 JS 脚本: {script_path}")


def _resolve_node_path() -> str:
    node_path = shutil.which("node") or shutil.which("node.exe")

    if node_path:
        return node_path

    message = "未检测到 Node.js 运行环境，请确认 Node.js 已安装并加入 PATH"
    log_error("本地脚本初始化", message)
    raise FileNotFoundError(message)


async def _kill_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return

    process.kill()

    try:
        await asyncio.wait_for(process.wait(), timeout=_PROCESS_KILL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        log_fail("本地脚本执行", "子进程 kill 后仍未退出")


class LocalScriptFetcher:
    """本地 JS 脚本抓取兜底器。"""

    def __init__(self, timeout: float = 120.0):
        _validate_script_path(_SCRIPT_PATH)

        self._node_path = _resolve_node_path()
        self._timeout = timeout

        log_ok("本地脚本初始化", node_path=self._node_path, timeout=self._timeout)

    async def fetch(self, url: str) -> Optional[str]:
        process: Optional[asyncio.subprocess.Process] = None

        try:
            process = await asyncio.create_subprocess_exec(
                self._node_path,
                str(_SCRIPT_PATH),
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

            if process is not None:
                await _kill_process(process)

            return None

        except Exception as e:
            log_error("本地脚本执行", e, url=url)

            if process is not None:
                await _kill_process(process)

            return None