import asyncio
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from chat.application.tools.services.web_fetch.base import BaseFetcher
from chat.application.tools.services.web_fetch.config import (
    WEB_FETCH_LOCAL_WORKER_CONCURRENCY,
    WEB_FETCH_LOCAL_WORKER_COUNT,
    WEB_FETCH_LOCAL_WORKER_RESTART_AFTER,
    WEB_FETCH_LOCAL_WORKER_TIMEOUT,
)
from chat.application.tools.services.web_fetch.models import FetchedLink, FetchedPage
from chat.application.tools.services.web_fetch.utils.page_metadata import (
    extract_markdown_title,
    extract_page_domain,
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
    in_process_concurrency: int
    process: Optional[asyncio.subprocess.Process] = None
    stdout_task: Optional[asyncio.Task[None]] = None
    stderr_task: Optional[asyncio.Task[None]] = None
    handled_count: int = 0
    lifecycle_lock: asyncio.Lock = field(init=False)
    write_lock: asyncio.Lock = field(init=False)
    capacity: asyncio.Semaphore = field(init=False)
    pending: dict[str, asyncio.Future[dict]] = field(init=False)

    def __post_init__(self) -> None:
        self.lifecycle_lock = asyncio.Lock()
        self.write_lock = asyncio.Lock()
        self.capacity = asyncio.Semaphore(max(1, self.in_process_concurrency))
        self.pending = {}

    async def start(self) -> None:
        async with self.lifecycle_lock:
            await self._start_unlocked()

    async def _start_unlocked(self) -> None:
        if self.process is not None and self.process.returncode is None:
            return

        env = {
            **os.environ,
            "WEB_FETCH_JS_WORKER_CONCURRENCY": str(self.in_process_concurrency),
            "WEB_FETCH_JS_BROWSER_RESTART_AFTER": str(self.restart_after),
        }

        self.process = await asyncio.create_subprocess_exec(
            self.node_path,
            str(self.script_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_MAX_SUBPROCESS_BUFFER,
            env=env,
        )
        self.stdout_task = asyncio.create_task(self._consume_stdout())
        self.stderr_task = asyncio.create_task(self._consume_stderr())

        log_event(
            "LocalScriptFetcher worker 已启动",
            worker=self.index,
            pid=self.process.pid,
        )

    async def fetch(self, url: str) -> Optional[FetchedPage]:
        async with self.capacity:
            return await self._fetch(url)

    async def _fetch(self, url: str) -> Optional[FetchedPage]:
        await self.start()

        process = self.process
        if process is None or process.stdin is None:
            await self.restart()
            return None

        if process.returncode is not None:
            log_fail("本地脚本执行", "worker already exited", url=url, worker=self.index)
            await self._restart_if_current(process)
            return None

        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict] = loop.create_future()
        self.pending[request_id] = future
        payload = json.dumps(
            {"id": request_id, "url": url},
            ensure_ascii=False,
        ) + "\n"

        try:
            async with self.write_lock:
                process.stdin.write(payload.encode("utf-8"))
                await process.stdin.drain()

            response = await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError:
            log_fail(
                "本地脚本执行",
                f"worker timeout: {self.timeout}s",
                url=url,
                worker=self.index,
            )
            await self._restart_if_current(process)
            return None

        except Exception as e:
            log_error("本地脚本执行", e, url=url, worker=self.index)
            await self._restart_if_current(process)
            return None

        if response.get("id") != request_id:
            log_fail("本地脚本执行", "worker response id mismatch", url=url, worker=self.index)
            await self._restart_if_current(process)
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

        links = [
            FetchedLink(
                url=str(link.get("url") or ""),
                anchor_text=str(link.get("anchorText") or link.get("anchor_text") or ""),
                surrounding_text=str(
                    link.get("surroundingText") or link.get("surrounding_text") or ""
                ),
            )
            for link in response.get("links") or []
            if isinstance(link, dict) and str(link.get("url") or "").strip()
        ]

        self.handled_count += 1

        final_url = str(response.get("finalUrl") or "")
        title = str(response.get("title") or "") or extract_markdown_title(markdown)

        return FetchedPage(
            markdown=markdown,
            links=links,
            title=title,
            final_url=final_url,
            domain=extract_page_domain(final_url),
            status_code=response.get("statusCode"),
        )

    async def restart(self) -> None:
        async with self.lifecycle_lock:
            await self._close_unlocked()
            self.handled_count = 0

            try:
                await self._start_unlocked()
            except Exception as e:
                log_error("LocalScriptFetcher worker 重启", e, worker=self.index)

    async def _restart_if_current(self, process: asyncio.subprocess.Process) -> None:
        async with self.lifecycle_lock:
            if self.process is not process:
                return

            await self._close_unlocked()
            self.handled_count = 0

            try:
                await self._start_unlocked()
            except Exception as e:
                log_error("LocalScriptFetcher worker 重启", e, worker=self.index)

    async def close(self) -> None:
        async with self.lifecycle_lock:
            await self._close_unlocked()

    async def _close_unlocked(self) -> None:
        process = self.process
        self.process = None

        pending = self.pending
        self.pending = {}
        for future in pending.values():
            if not future.done():
                future.set_exception(RuntimeError("worker restarted"))

        stdout_task = self.stdout_task
        self.stdout_task = None
        if stdout_task is not None:
            stdout_task.cancel()
            try:
                await stdout_task
            except asyncio.CancelledError:
                pass

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

    async def _consume_stdout(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return

        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    pending = self.pending
                    self.pending = {}
                    for future in pending.values():
                        if not future.done():
                            future.set_exception(RuntimeError("worker stdout closed"))
                    return

                try:
                    response = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError as e:
                    log_fail("本地脚本执行", f"invalid worker json: {e}", worker=self.index)
                    continue

                request_id = str(response.get("id") or "")
                future = self.pending.pop(request_id, None)
                if future is None:
                    log_fail(
                        "本地脚本执行",
                        "worker response id not pending",
                        worker=self.index,
                        request_id=request_id,
                    )
                    continue

                if not future.done():
                    future.set_result(response)

        except asyncio.CancelledError:
            return
        except Exception as e:
            log_error("LocalScriptFetcher worker stdout", e, worker=self.index)

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
        worker_concurrency: int = WEB_FETCH_LOCAL_WORKER_CONCURRENCY,
    ):
        _validate_script_path(_SCRIPT_PATH)
        _validate_script_path(_WORKER_SCRIPT_PATH)

        if worker_count <= 0:
            raise ValueError("worker_count must be positive")

        self._node_path = _resolve_node_path()
        self._timeout = timeout
        self._worker_count = worker_count
        self._restart_after = restart_after
        self._worker_concurrency = worker_concurrency
        self._workers = [
            _LocalWorker(
                index=index,
                node_path=self._node_path,
                script_path=_WORKER_SCRIPT_PATH,
                timeout=self._timeout,
                restart_after=self._restart_after,
                in_process_concurrency=self._worker_concurrency,
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
            worker_concurrency=self._worker_concurrency,
            restart_after=self._restart_after,
        )

    async def fetch(self, url: str) -> Optional[FetchedPage]:
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
                for _ in range(max(1, self._worker_concurrency)):
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
