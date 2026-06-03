from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional


SERVICE_ROOT = Path(__file__).resolve().parent
SRC_ROOT = SERVICE_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from chat.application.tools.web.services.web_fetch.fetcher.local_fetcher import (  # noqa: E402
    LocalScriptFetcher,
    LocalWorker,
    _WORKER_SCRIPT_PATH,
    _resolve_node_path,
)


DEBUG_URL = os.environ.get("DEBUG_LOCAL_FETCH_URL", "https://deepseek.com")
INNER_TIMEOUT_SECONDS = float(os.environ.get("DEBUG_LOCAL_FETCH_INNER_TIMEOUT", "8"))
OUTER_TIMEOUT_SECONDS = float(os.environ.get("DEBUG_LOCAL_FETCH_OUTER_TIMEOUT", "20"))


def _print_header(title: str) -> None:
    print()
    print("=" * 90)
    print(title)
    print("=" * 90)


def _short(value: Any, limit: int = 1200) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"


async def _heartbeat(label: str, stop_event: asyncio.Event) -> None:
    started_at = time.monotonic()
    while not stop_event.is_set():
        elapsed = time.monotonic() - started_at
        print(f"[heartbeat] {label} still running... elapsed={elapsed:.1f}s")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass


async def _run_with_heartbeat(label: str, coro: Any, timeout: float) -> Any:
    stop_event = asyncio.Event()
    heartbeat_task = asyncio.create_task(_heartbeat(label=label, stop_event=stop_event))

    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    finally:
        stop_event.set()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


async def _consume_stderr_for_debug(
        process: asyncio.subprocess.Process,
        *,
        label: str,
) -> None:
    if process.stderr is None:
        return

    while True:
        line = await process.stderr.readline()
        if not line:
            return

        message = line.decode("utf-8", errors="replace").strip()
        if message:
            print(f"[{label}:stderr] {message}")


async def debug_environment() -> None:
    _print_header("1. Environment")

    print("service_root:", SERVICE_ROOT)
    print("src_root:", SRC_ROOT)
    print("debug_url:", DEBUG_URL)
    print("inner_timeout:", INNER_TIMEOUT_SECONDS)
    print("outer_timeout:", OUTER_TIMEOUT_SECONDS)

    try:
        node_path = _resolve_node_path()
        print("node_path:", node_path)
    except Exception as e:
        print("node_path_error:", type(e).__name__, repr(e))
        raise

    print("worker_script:", _WORKER_SCRIPT_PATH)
    print("worker_script_exists:", _WORKER_SCRIPT_PATH.exists())

    if _WORKER_SCRIPT_PATH.exists():
        print("worker_script_size:", _WORKER_SCRIPT_PATH.stat().st_size)


async def debug_raw_node_worker_protocol() -> None:
    _print_header("2. Raw Node worker protocol test")

    node_path = _resolve_node_path()

    env = {
        **os.environ,
        "WEB_FETCH_JS_WORKER_CONCURRENCY": "1",
        "WEB_FETCH_JS_BROWSER_RESTART_AFTER": "100",
    }

    process: Optional[asyncio.subprocess.Process] = None
    stderr_task: Optional[asyncio.Task[None]] = None

    try:
        process = await asyncio.create_subprocess_exec(
            node_path,
            str(_WORKER_SCRIPT_PATH),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        print("raw_worker_pid:", process.pid)

        stderr_task = asyncio.create_task(
            _consume_stderr_for_debug(
                process,
                label="raw-worker",
            )
        )

        if process.stdin is None or process.stdout is None:
            print("raw_worker_error: stdin/stdout missing")
            return

        request_id = uuid.uuid4().hex
        payload = json.dumps(
            {
                "id": request_id,
                "url": DEBUG_URL,
            },
            ensure_ascii=False,
        ) + "\n"

        print("raw_worker_send:", payload.strip())

        process.stdin.write(payload.encode("utf-8"))
        await asyncio.wait_for(process.stdin.drain(), timeout=5.0)

        line = await _run_with_heartbeat(
            label="raw stdout readline",
            coro=process.stdout.readline(),
            timeout=OUTER_TIMEOUT_SECONDS,
        )

        if not line:
            print("raw_worker_result: stdout closed without response")
            print("raw_worker_returncode:", process.returncode)
            return

        text = line.decode("utf-8", errors="replace").strip()
        print("raw_worker_stdout_line:", _short(text))

        try:
            response = json.loads(text)
        except json.JSONDecodeError as e:
            print("raw_worker_json_error:", repr(e))
            return

        print("raw_worker_response_id:", response.get("id"))
        print("raw_worker_ok:", response.get("ok"))
        print("raw_worker_error:", response.get("error"))
        print("raw_worker_title:", response.get("title"))
        print("raw_worker_final_url:", response.get("finalUrl"))
        print("raw_worker_status_code:", response.get("statusCode"))

        markdown = str(response.get("markdown") or "")
        print("raw_worker_markdown_len:", len(markdown))
        print("raw_worker_markdown_preview:", _short(markdown, limit=500))

    except asyncio.TimeoutError:
        print("raw_worker_timeout: worker did not emit stdout response before outer timeout")
    except Exception as e:
        print("raw_worker_exception:", type(e).__name__, repr(e))
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                print("raw_worker_kill_timeout")

        if stderr_task is not None:
            stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass


async def debug_single_local_worker() -> None:
    _print_header("3. Direct LocalWorker.fetch test")

    node_path = _resolve_node_path()

    worker = LocalWorker(
        index=0,
        node_path=node_path,
        script_path=_WORKER_SCRIPT_PATH,
        timeout=INNER_TIMEOUT_SECONDS,
        restart_after=100,
        in_process_concurrency=1,
    )

    try:
        await _run_with_heartbeat(
            label="LocalWorker.start",
            coro=worker.start(),
            timeout=10.0,
        )

        process = worker.process
        print("worker_started:", process is not None)
        print("worker_pid:", process.pid if process is not None else None)
        print("worker_returncode:", process.returncode if process is not None else None)
        print("worker_pending_before:", len(worker.pending))

        page = await _run_with_heartbeat(
            label="LocalWorker.fetch",
            coro=worker.fetch(DEBUG_URL),
            timeout=OUTER_TIMEOUT_SECONDS,
        )

        print("worker_pending_after:", len(worker.pending))
        print("page_is_none:", page is None)

        if page is not None:
            print("page_title:", page.title)
            print("page_final_url:", page.final_url)
            print("page_domain:", page.domain)
            print("page_status_code:", page.status_code)
            print("page_markdown_len:", len(page.markdown))
            print("page_markdown_preview:", _short(page.markdown, limit=500))
            print("page_links_count:", len(page.links))

    except asyncio.TimeoutError:
        print("direct_worker_timeout: LocalWorker.start/fetch exceeded outer timeout")
        print("worker_pending:", len(worker.pending))
        process = worker.process
        print("worker_process_exists:", process is not None)
        print("worker_returncode:", process.returncode if process is not None else None)
    except Exception as e:
        print("direct_worker_exception:", type(e).__name__, repr(e))
    finally:
        await worker.close()


async def debug_local_script_fetcher_pool() -> None:
    _print_header("4. LocalScriptFetcher pool test")

    fetcher = LocalScriptFetcher(
        timeout=INNER_TIMEOUT_SECONDS,
        worker_count=1,
        restart_after=100,
        worker_concurrency=1,
    )

    try:
        print("pool_started_initial:", fetcher._started)
        print("pool_idle_qsize_initial:", fetcher._idle_workers.qsize())

        await _run_with_heartbeat(
            label="LocalScriptFetcher._ensure_pool_started",
            coro=fetcher._ensure_pool_started(),
            timeout=10.0,
        )

        print("pool_started_after_start:", fetcher._started)
        print("pool_idle_qsize_after_start:", fetcher._idle_workers.qsize())

        for worker in fetcher._workers:
            process = worker.process
            print(
                "pool_worker_after_start:",
                {
                    "index": worker.index,
                    "pid": process.pid if process is not None else None,
                    "returncode": process.returncode if process is not None else None,
                    "pending": len(worker.pending),
                    "handled_count": worker.handled_count,
                    "stdout_task_done": worker.stdout_task.done() if worker.stdout_task else None,
                    "stderr_task_done": worker.stderr_task.done() if worker.stderr_task else None,
                },
            )

        print("\n--- manual checkout worker.fetch test ---")
        print("qsize_before_manual_get:", fetcher._idle_workers.qsize())

        worker = await asyncio.wait_for(
            fetcher._idle_workers.get(),
            timeout=5.0,
        )

        print("qsize_after_manual_get:", fetcher._idle_workers.qsize())

        process = worker.process
        print(
            "manual_worker_before_fetch:",
            {
                "index": worker.index,
                "pid": process.pid if process is not None else None,
                "returncode": process.returncode if process is not None else None,
                "pending": len(worker.pending),
                "handled_count": worker.handled_count,
                "stdout_task_done": worker.stdout_task.done() if worker.stdout_task else None,
                "stderr_task_done": worker.stderr_task.done() if worker.stderr_task else None,
            },
        )

        try:
            page = await _run_with_heartbeat(
                label="manual LocalWorker.fetch",
                coro=worker.fetch(DEBUG_URL),
                timeout=OUTER_TIMEOUT_SECONDS,
            )

            print("manual_page_is_none:", page is None)
            if page is not None:
                print("manual_page_title:", page.title)
                print("manual_page_final_url:", page.final_url)
                print("manual_page_domain:", page.domain)
                print("manual_page_status_code:", page.status_code)
                print("manual_page_markdown_len:", len(page.markdown))
                print("manual_page_markdown_preview:", _short(page.markdown, limit=500))
                print("manual_page_links_count:", len(page.links))

        finally:
            process = worker.process
            print(
                "manual_worker_after_fetch_before_put:",
                {
                    "index": worker.index,
                    "pid": process.pid if process is not None else None,
                    "returncode": process.returncode if process is not None else None,
                    "pending": len(worker.pending),
                    "handled_count": worker.handled_count,
                    "stdout_task_done": worker.stdout_task.done() if worker.stdout_task else None,
                    "stderr_task_done": worker.stderr_task.done() if worker.stderr_task else None,
                },
            )

            await fetcher._idle_workers.put(worker)
            print("qsize_after_manual_put:", fetcher._idle_workers.qsize())

        print("\n--- wrapper fetcher.fetch test ---")
        print("qsize_before_wrapper_fetch:", fetcher._idle_workers.qsize())

        page = await _run_with_heartbeat(
            label="LocalScriptFetcher.fetch wrapper",
            coro=fetcher.fetch(DEBUG_URL),
            timeout=OUTER_TIMEOUT_SECONDS,
        )

        print("qsize_after_wrapper_fetch:", fetcher._idle_workers.qsize())
        print("wrapper_page_is_none:", page is None)

        if page is not None:
            print("wrapper_page_title:", page.title)
            print("wrapper_page_final_url:", page.final_url)
            print("wrapper_page_domain:", page.domain)
            print("wrapper_page_status_code:", page.status_code)
            print("wrapper_page_markdown_len:", len(page.markdown))
            print("wrapper_page_markdown_preview:", _short(page.markdown, limit=500))
            print("wrapper_page_links_count:", len(page.links))

        for worker in fetcher._workers:
            process = worker.process
            print(
                "pool_worker_after_wrapper_fetch:",
                {
                    "index": worker.index,
                    "pid": process.pid if process is not None else None,
                    "returncode": process.returncode if process is not None else None,
                    "pending": len(worker.pending),
                    "handled_count": worker.handled_count,
                    "stdout_task_done": worker.stdout_task.done() if worker.stdout_task else None,
                    "stderr_task_done": worker.stderr_task.done() if worker.stderr_task else None,
                },
            )

    except asyncio.TimeoutError:
        print("pool_timeout: pool start/fetch exceeded outer timeout")
        print("pool_started:", fetcher._started)
        print("pool_idle_qsize:", fetcher._idle_workers.qsize())

        for worker in fetcher._workers:
            process = worker.process
            print(
                "pool_worker_after_timeout:",
                {
                    "index": worker.index,
                    "pid": process.pid if process is not None else None,
                    "returncode": process.returncode if process is not None else None,
                    "pending": len(worker.pending),
                    "handled_count": worker.handled_count,
                    "stdout_task_done": worker.stdout_task.done() if worker.stdout_task else None,
                    "stderr_task_done": worker.stderr_task.done() if worker.stderr_task else None,
                },
            )

    except Exception as e:
        print("pool_exception:", type(e).__name__, repr(e))
    finally:
        await fetcher.close()


async def debug_forced_empty_idle_queue_hang() -> None:
    _print_header("5. Forced empty idle queue hang reproduction")

    fetcher = LocalScriptFetcher(
        timeout=INNER_TIMEOUT_SECONDS,
        worker_count=1,
        restart_after=100,
        worker_concurrency=1,
    )

    try:
        await _run_with_heartbeat(
            label="LocalScriptFetcher._ensure_pool_started before forced drain",
            coro=fetcher._ensure_pool_started(),
            timeout=10.0,
        )

        print("before_forced_drain_started:", fetcher._started)
        print("before_forced_drain_qsize:", fetcher._idle_workers.qsize())

        fetcher._drain_idle_workers()

        print("after_forced_drain_started:", fetcher._started)
        print("after_forced_drain_qsize:", fetcher._idle_workers.qsize())
        print("next fetch should hang at _idle_workers.get() unless caller wraps it.")

        try:
            await _run_with_heartbeat(
                label="LocalScriptFetcher.fetch with empty idle queue",
                coro=fetcher.fetch(DEBUG_URL),
                timeout=3.0,
            )
            print("forced_empty_queue_result: fetch returned unexpectedly")
        except asyncio.TimeoutError:
            print("forced_empty_queue_result: reproduced hang at pool checkout")

    finally:
        await fetcher.close()


async def main() -> None:
    await debug_environment()
    await debug_raw_node_worker_protocol()
    await debug_single_local_worker()
    await debug_local_script_fetcher_pool()
    await debug_forced_empty_idle_queue_hang()


if __name__ == "__main__":
    asyncio.run(main())