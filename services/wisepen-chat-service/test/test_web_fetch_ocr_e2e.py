"""
Legacy filename kept for local scripts; web_fetch OCR behavior has been removed.

Usage:
    uv run python test/test_web_fetch_ocr_e2e.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, "src")


def _load_web_fetch_tool():
    module_path = Path(__file__).resolve().parents[1] / "src" / "chat" / "application" / "tools" / "web_fetch_tool.py"
    spec = importlib.util.spec_from_file_location("web_fetch_tool_under_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load web_fetch_tool module from {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.WebFetchTool


WebFetchTool = _load_web_fetch_tool()


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main() -> int:
    schema = WebFetchTool(fetcher=None).parameters_schema
    props = schema["properties"]
    assert_true("force_ocr" not in props, "web_fetch must not expose force_ocr")
    assert_true("force_browser" not in props, "web_fetch must not expose force_browser")
    assert_true(set(props.keys()) == {"url"}, f"web_fetch should only expose url, got {props.keys()}")

    print("PASS web_fetch OCR decoupling verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
