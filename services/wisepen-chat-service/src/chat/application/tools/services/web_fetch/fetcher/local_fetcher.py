import asyncio
import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from chat.application.tools.services.web_fetch.base import BaseFetcher
from chat.application.tools.services.web_fetch.config import (
    WEB_FETCH_LOCAL_WORKER_COUNT,
    WEB_FETCH_LOCAL_WORKER_RESTART_AFTER,
    WEB_FETCH_LOCAL_WORKER_TIMEOUT,
)
from common.logger import log_event, log_error, log_fail


_MAX_SUBPROCESS_BUFFER = 10 * 1024 * 1024
_MAX_ERROR_SNIPPET = 500
_PROCESS_KILL_TIMEOUT_SECONDS = 5.0

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "local_web_fetcher.js"
_WORKER_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "local_web_fetcher_worker.js"


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


@dataclass(slots=True)
class _LocalWorker:
    index: int
    node_path: str
    script_path: Path
    timeout: float
    restart_after: int
    process: Optional[asyncio.subprocess.Process] = None
    stderr_task: Optional[asyncio.Task[None]] = None
    handled_count: int = 0

    async def start(self) -> None:
        if self.process is not None and self.process.returncode is None:
            return

        self.process = await asyncio.create_subprocess_exec(
            self.node_path,
            str(self.script_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_MAX_SUBPROCESS_BUFFER,
        )
        self.stderr_task = asyncio.create_task(self._consume_stderr())

        log_event(
            "LocalScriptFetcher worker 已启动",
            worker=self.index,
            pid=self.process.pid,
        )

    async def fetch(self, url: str) -> Optional[str]:
        await self.start()

        process = self.process
        if process is None or process.stdin is None or process.stdout is None:
            await self.restart()
            return None

        if process.returncode is not None:
            log_fail("本地脚本执行", "worker already exited", url=url, worker=self.index)
            await self.restart()
            return None

        request_id = uuid.uuid4().hex
        payload = json.dumps(
            {"id": request_id, "url": url},
            ensure_ascii=False,
        ) + "\n"

        try:
            process.stdin.write(payload.encode("utf-8"))
            await process.stdin.drain()

            line = await asyncio.wait_for(
                process.stdout.readline(),
                timeout=self.timeout,
            )

        except asyncio.TimeoutError:
            log_fail(
                "本地脚本执行",
                f"worker timeout: {self.timeout}s",
                url=url,
                worker=self.index,
            )
            await self.restart()
            return None

        except Exception as e:
            log_error("本地脚本执行", e, url=url, worker=self.index)
            await self.restart()
            return None

        if not line:
            log_fail("本地脚本执行", "worker stdout closed", url=url, worker=self.index)
            await self.restart()
            return None

        try:
            response = json.loads(line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as e:
            log_fail("本地脚本执行", f"invalid worker json: {e}", url=url, worker=self.index)
            await self.restart()
            return None

        if response.get("id") != request_id:
            log_fail("本地脚本执行", "worker response id mismatch", url=url, worker=self.index)
            await self.restart()
            return None

        if not response.get("ok"):
            log_fail(
                "本地脚本执行",
                str(response.get("error") or "unknown worker error")[:_MAX_ERROR_SNIPPET],
                url=url,
                worker=self.index,
            )
            return None

        markdown = str(response.get("markdown") or "").strip()
        if not markdown:
            log_fail("本地脚本执行", "empty result", url=url, worker=self.index)
            return None

        self.handled_count += 1

        if self.restart_after > 0 and self.handled_count >= self.restart_after:
            await self.restart()

        return markdown

    async def restart(self) -> None:
        await self.close()
        self.handled_count = 0

        try:
            await self.start()
        except Exception as e:
            log_error("LocalScriptFetcher worker 重启", e, worker=self.index)

    async def close(self) -> None:
        process = self.process
        self.process = None

        stderr_task = self.stderr_task
        self.stderr_task = None
        if stderr_task is not None:
            stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass

        if process is None:
            return

        if process.returncode is not None:
            return

        process.terminate()

        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=_PROCESS_KILL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await _kill_process(process)

    async def _consume_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return

        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    return

                message = line.decode("utf-8", errors="replace").strip()
                if message:
                    log_event(
                        "LocalScriptFetcher worker stderr",
                        message=message[:_MAX_ERROR_SNIPPET],
                        worker=self.index,
                    )

        except asyncio.CancelledError:
            return
        except Exception as e:
            log_error("LocalScriptFetcher worker stderr", e, worker=self.index)


class LocalScriptFetcher(BaseFetcher):
    """本地 JS 脚本抓取兜底器。"""

    name = "local_script"

    def __init__(
        self,
        timeout: float = WEB_FETCH_LOCAL_WORKER_TIMEOUT,
        worker_count: int = WEB_FETCH_LOCAL_WORKER_COUNT,
        restart_after: int = WEB_FETCH_LOCAL_WORKER_RESTART_AFTER,
    ):
        _validate_script_path(_SCRIPT_PATH)
        _validate_script_path(_WORKER_SCRIPT_PATH)

        if worker_count <= 0:
            raise ValueError("worker_count must be positive")

        self._node_path = _resolve_node_path()
        self._timeout = timeout
        self._worker_count = worker_count
        self._restart_after = restart_after
        self._workers = [
            _LocalWorker(
                index=index,
                node_path=self._node_path,
                script_path=_WORKER_SCRIPT_PATH,
                timeout=self._timeout,
                restart_after=self._restart_after,
            )
            for index in range(worker_count)
        ]
        self._idle_workers: asyncio.Queue[_LocalWorker] = asyncio.Queue()
        self._started = False
        self._start_lock = asyncio.Lock()

        log_event(
            "LocalScriptFetcher 初始化",
            node_path=self._node_path,
            timeout=self._timeout,
            worker_count=self._worker_count,
            restart_after=self._restart_after,
        )

    async def fetch(self, url: str) -> Optional[str]:
        try:
            await self._ensure_pool_started()
        except Exception as e:
            log_error("LocalScriptFetcher worker pool 启动", e, url=url)
            return None

        worker = await self._idle_workers.get()

        try:
            return await worker.fetch(url)
        finally:
            await self._idle_workers.put(worker)

    async def close(self) -> None:
        for worker in self._workers:
            await worker.close()

        self._started = False

        self._drain_idle_workers()

        log_event("LocalScriptFetcher worker pool 已关闭")

    async def _ensure_pool_started(self) -> None:
        if self._started:
            return

        async with self._start_lock:
            if self._started:
                return

            started_workers: List[_LocalWorker] = []
            try:
                for worker in self._workers:
                    await worker.start()
                    started_workers.append(worker)
            except Exception:
                for worker in started_workers:
                    await worker.close()
                self._drain_idle_workers()
                self._started = False
                raise

            self._drain_idle_workers()
            for worker in started_workers:
                await self._idle_workers.put(worker)

            self._started = True

            log_event(
                "LocalScriptFetcher worker pool 已启动",
                worker_count=self._worker_count,
            )

    def _drain_idle_workers(self) -> None:
        while not self._idle_workers.empty():
            try:
                self._idle_workers.get_nowait()
            except asyncio.QueueEmpty:
                break
