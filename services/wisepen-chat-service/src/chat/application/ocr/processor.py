import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from chat.application.document_parse.errors import (
    DocumentParserDependencyError,
    OcrProcessingError,
)
from chat.application.ocr.config import (
    OCR_BACKEND,
    OCR_LANGUAGE,
    OCR_TIMEOUT_SECONDS,
    OCR_USE_DOC_ORIENTATION_CLASSIFY,
    OCR_USE_DOC_UNWARPING,
    OCR_USE_TEXTLINE_ORIENTATION,
    OCR_WORKER_IDLE_TTL_SECONDS,
    OCR_WORKER_MODE,
)
from chat.core.config.app_settings import settings
from common.logger import log_event, log_fail


@dataclass(slots=True)
class OcrResult:
    ok: bool
    text: str = ""
    error: Optional[str] = None
    backend: str = "paddleocr"


class _OcrProcessingError(OcrProcessingError):
    def __init__(self, message: str):
        super().__init__(message)


class _OcrWorkerError(_OcrProcessingError):
    pass


_PADDLE_WORKER_ENV = {
    "FLAGS_use_mkldnn": "0",
    "FLAGS_enable_onednn": "0",
    "FLAGS_cpu_math_library_num_threads": "1",
    "DNNL_MAX_CPU_ISA": "NONE",
}

_BACKEND_PADDLEOCR = "paddleocr"
_WORKER_MODE_LAZY_PERSISTENT = "lazy_persistent"
_WORKER_REQUEST_MAX_ATTEMPTS = 2
_WORKER_RESPONSE_MAX_NON_JSON_LINES = 20
_WORKER_SHUTDOWN_TIMEOUT_SECONDS = 5.0
_SHUTDOWN_REQUEST = {"shutdown": True}


class OcrProcessor:
    """本地 OCR worker 处理器。"""

    def __init__(
        self,
        timeout: Optional[float] = None,
        enabled: Optional[bool] = None,
        backend: str = OCR_BACKEND,
        language: str = OCR_LANGUAGE,
        worker_mode: str = OCR_WORKER_MODE,
        worker_idle_ttl_seconds: int = OCR_WORKER_IDLE_TTL_SECONDS,
        use_doc_orientation_classify: bool = OCR_USE_DOC_ORIENTATION_CLASSIFY,
        use_doc_unwarping: bool = OCR_USE_DOC_UNWARPING,
        use_textline_orientation: bool = OCR_USE_TEXTLINE_ORIENTATION,
    ):
        if worker_mode != _WORKER_MODE_LAZY_PERSISTENT:
            raise DocumentParserDependencyError(
                "ocr_worker", f"Unsupported OCR worker mode: {worker_mode}"
            )

        self._timeout = timeout if timeout is not None else OCR_TIMEOUT_SECONDS
        self._enabled = settings.ENABLE_OCR if enabled is None else enabled
        self._backend = backend
        self._language = language
        self._worker_mode = worker_mode
        self._worker_idle_ttl_seconds = worker_idle_ttl_seconds
        self._use_doc_orientation_classify = use_doc_orientation_classify
        self._use_doc_unwarping = use_doc_unwarping
        self._use_textline_orientation = use_textline_orientation

        self._worker_process: Optional[asyncio.subprocess.Process] = None
        self._worker_lock = asyncio.Lock()
        self._worker_stderr_task: Optional[asyncio.Task] = None
        self._worker_stderr_process: Optional[asyncio.subprocess.Process] = None
        self._idle_reaper_task: Optional[asyncio.Task] = None
        self._last_worker_use = 0.0

    async def recognize_image(self, image_path: Path) -> OcrResult:
        if not self._enabled:
            return OcrResult(ok=False, error="OCR is disabled.", backend=self._backend)

        if self._backend != _BACKEND_PADDLEOCR:
            return OcrResult(
                ok=False,
                error="OCR backend is not available or not installed.",
                backend=self._backend,
            )

        log_event(
            "OCR 识别开始",
            backend=self._backend,
            path=str(image_path),
            language=self._language,
        )
        try:
            return await asyncio.wait_for(
                self._recognize_image(image_path), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            await self._reset_worker_locked()
            log_fail(
                "OCR 识别", "请求超时", path=str(image_path), timeout=self._timeout
            )
            return OcrResult(
                ok=False, error="OCR processing timed out.", backend=self._backend
            )
        except OcrProcessingError as e:
            log_fail("OCR 识别", e, path=str(image_path))
            return OcrResult(ok=False, error=str(e), backend=self._backend)
        except Exception as e:
            log_fail("OCR 识别", e, path=str(image_path))
            return OcrResult(
                ok=False,
                error=f"{e.__class__.__name__}: {e}",
                backend=self._backend,
            )

    async def _recognize_image(self, image_path: Path) -> OcrResult:
        if not image_path.is_file():
            raise _OcrProcessingError(f"OCR input file not found: {image_path}")

        text = await self._recognize_image_text(image_path)
        if not text:
            return OcrResult(
                ok=False, error="OCR produced no text.", backend=self._backend
            )

        log_event(
            "OCR 识别完成",
            backend=self._backend,
            length=len(text),
            path=str(image_path),
        )
        return OcrResult(ok=True, text=text, backend=self._backend)

    async def _recognize_image_text(self, image_path: Path) -> str:
        response = await self._request_worker(
            {
                "input": str(image_path),
                "lang": self._language,
                "use_doc_orientation_classify": self._use_doc_orientation_classify,
                "use_doc_unwarping": self._use_doc_unwarping,
                "use_textline_orientation": self._use_textline_orientation,
            }
        )

        if response.get("ok"):
            return str(response.get("text") or "").strip()

        error = response.get("error")
        if error == "OCR_BACKEND_UNAVAILABLE":
            log_fail(
                "OCR 识别", "后端不可用", reason=response.get("message", "后端不可用")
            )
            raise _OcrProcessingError("OCR backend is not available or not installed.")

        message = response.get("message") or error or "Unknown OCR worker error"
        raise _OcrProcessingError(str(message))

    async def _request_worker(self, request: Dict[str, Any]) -> Dict[str, Any]:
        for attempt in range(_WORKER_REQUEST_MAX_ATTEMPTS):
            async with self._worker_lock:
                process = await self._ensure_worker()

                try:
                    if process.stdin is None or process.stdout is None:
                        raise _OcrWorkerError("OCR worker 标准输入或标准输出不可用")

                    process.stdin.write(
                        json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"
                    )
                    await process.stdin.drain()
                    response = await self._read_worker_response(process)
                    self._last_worker_use = time.monotonic()
                    self._ensure_idle_reaper()
                    return response
                except Exception as e:
                    await self._reset_worker()
                    if attempt == 0:
                        log_fail("OCR worker 请求", repr(e))
                        continue
                    raise

        raise _OcrWorkerError("OCR worker request failed")

    async def _ensure_worker(self) -> asyncio.subprocess.Process:
        if self._worker_process is not None and self._worker_process.returncode is None:
            return self._worker_process

        if self._worker_process is not None:
            await self._reset_worker()

        process = await self._start_worker()
        self._worker_process = process
        self._last_worker_use = time.monotonic()
        self._ensure_idle_reaper()
        return process

    async def _start_worker(self) -> asyncio.subprocess.Process:
        current = Path(__file__).resolve()
        src_root = current.parents[3]
        service_root = current.parents[4]
        env = os.environ.copy()
        env.update(_PADDLE_WORKER_ENV)
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            str(src_root)
            if not existing_pythonpath
            else str(src_root) + os.pathsep + existing_pythonpath
        )
        env["PYTHONIOENCODING"] = "utf-8"

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "chat.application.ocr.worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(service_root),
            env=env,
        )

        self._worker_stderr_task = asyncio.create_task(
            self._drain_worker_stderr(process)
        )
        self._worker_stderr_process = process
        log_event(
            "OCR worker 启动",
            backend=self._backend,
            mode=self._worker_mode,
            pid=process.pid,
        )
        return process

    async def _read_worker_response(
        self, process: asyncio.subprocess.Process
    ) -> Dict[str, Any]:
        if process.stdout is None:
            raise _OcrWorkerError("OCR worker 标准输出不可用")

        for _ in range(_WORKER_RESPONSE_MAX_NON_JSON_LINES):
            line = await asyncio.wait_for(
                process.stdout.readline(), timeout=self._timeout
            )
            if not line:
                raise _OcrWorkerError("OCR worker 在返回响应前退出")

            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded:
                continue

            try:
                parsed = json.loads(decoded)
            except json.JSONDecodeError:
                log_fail("OCR worker 协议", f"stdout={decoded}")
                continue

            if not isinstance(parsed, dict):
                raise _OcrWorkerError("OCR worker 返回的 JSON 不是对象")

            return parsed

        raise _OcrWorkerError("OCR worker 返回了过多非 JSON 行")

    async def _drain_worker_stderr(self, process: asyncio.subprocess.Process) -> None:
        if process.stderr is None:
            return

        error_keywords = {"error", "traceback", "exception", "failed"}

        while True:
            line = await process.stderr.readline()
            if not line:
                return

            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded:
                continue

            lower = decoded.lower()
            if any(keyword in lower for keyword in error_keywords):
                log_event("OCR worker stderr", stderr=decoded, level="error")
            else:
                log_event("OCR worker stderr", stderr=decoded)

    def _ensure_idle_reaper(self) -> None:
        if self._worker_idle_ttl_seconds <= 0:
            return

        if self._idle_reaper_task is not None and not self._idle_reaper_task.done():
            return

        self._idle_reaper_task = asyncio.create_task(self._idle_reaper())

    async def _idle_reaper(self) -> None:
        while True:
            sleep_seconds = min(max(self._worker_idle_ttl_seconds / 4, 1.0), 60.0)
            await asyncio.sleep(sleep_seconds)

            async with self._worker_lock:
                process = self._worker_process
                if process is None or process.returncode is not None:
                    return

                idle_seconds = time.monotonic() - self._last_worker_use
                if idle_seconds >= self._worker_idle_ttl_seconds:
                    await self._stop_worker()
                    log_event(
                        "OCR worker 空闲退出",
                        backend=self._backend,
                        idle_seconds=round(idle_seconds, 2),
                    )
                    return

    async def _reset_worker(self) -> None:
        await self._stop_worker(force=True)

    async def _reset_worker_locked(self) -> None:
        async with self._worker_lock:
            await self._reset_worker()

    async def close(self) -> None:
        idle_task = self._idle_reaper_task
        self._idle_reaper_task = None

        if idle_task is not None and not idle_task.done():
            idle_task.cancel()
            try:
                await idle_task
            except asyncio.CancelledError:
                pass

        async with self._worker_lock:
            await self._stop_worker()

    async def _stop_worker(self, *, force: bool = False) -> None:
        process = self._worker_process
        self._worker_process = None

        if process is None:
            return

        try:
            if process.returncode is None and process.stdin is not None and not force:
                process.stdin.write(
                    json.dumps(_SHUTDOWN_REQUEST).encode("utf-8") + b"\n"
                )
                await process.stdin.drain()
                await asyncio.wait_for(
                    process.wait(), timeout=_WORKER_SHUTDOWN_TIMEOUT_SECONDS
                )
            elif process.returncode is None:
                process.terminate()
                await asyncio.wait_for(
                    process.wait(), timeout=_WORKER_SHUTDOWN_TIMEOUT_SECONDS
                )
        except Exception:
            if process.returncode is None:
                process.kill()
                await process.wait()

        task = self._worker_stderr_task
        if task is not None and self._worker_stderr_process is process:
            self._worker_stderr_task = None
            self._worker_stderr_process = None
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
