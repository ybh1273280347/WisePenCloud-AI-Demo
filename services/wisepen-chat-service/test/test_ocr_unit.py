"""
document_parse OCR adapter unit tests.

Usage:
    uv run python test/test_ocr_unit.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from chat.application.document_parse.ocr.processor import OcrProcessor


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def _read_all(stream):
    data = bytearray()
    try:
        while True:
            chunk = await asyncio.wait_for(stream.read(4096), timeout=2.0)
            if not chunk:
                break
            data.extend(chunk)
    except (asyncio.TimeoutError, Exception):
        pass
    return data


async def test_disabled_ocr() -> None:
    processor = OcrProcessor(timeout=1.0, enabled=False)
    result = await processor.recognize_image(Path("missing.png"))
    assert_true(result.ok is False, f"disabled OCR should return ok=False, got ok={result.ok}")
    assert_true("disabled" in (result.error or "").lower(), f"error should mention disabled, got: {result.error}")
    await processor.close()


async def test_worker_protocol_shutdown() -> None:
    src_root = str(Path(__file__).resolve().parent.parent / "src")
    common_root = str(Path(__file__).resolve().parent.parent.parent / "wisepen-common" / "src")
    env = os.environ.copy()
    env["PYTHONPATH"] = src_root + os.pathsep + common_root + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "chat.application.document_parse.ocr.worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stderr_future = asyncio.ensure_future(_read_all(process.stderr))

    try:
        assert process.stdin is not None
        process.stdin.write(json.dumps({"shutdown": True}, ensure_ascii=False).encode("utf-8") + b"\n")
        await process.stdin.drain()
        exit_code = await asyncio.wait_for(process.wait(), timeout=30.0)
        assert_true(exit_code == 0, f"worker should exit with code 0, got {exit_code}")
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()
        stderr_future.cancel()
        try:
            await stderr_future
        except (asyncio.CancelledError, Exception):
            pass


async def test_missing_image_fails_without_worker() -> None:
    processor = OcrProcessor(timeout=1.0, enabled=True)

    async def fail(*args, **kwargs):
        raise AssertionError("worker should not start when input file is missing")

    processor._request_worker = fail
    result = await processor.recognize_image(Path("missing.png"))
    assert_true(result.ok is False, "missing input should fail")
    assert_true("not found" in (result.error or "").lower(), f"error should mention not found, got: {result.error}")
    await processor.close()


async def main() -> int:
    tests = [
        test_disabled_ocr,
        test_worker_protocol_shutdown,
        test_missing_image_fails_without_worker,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            await test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {test.__name__}: {e}")
            failed += 1

    print(f"\n{passed}/{passed + failed} tests passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
