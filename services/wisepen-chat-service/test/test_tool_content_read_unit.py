"""
ToolContentReadTool 单一功能测试

直接调用 ToolContentReadTool.execute 和 read_tool_content_window，
不经过 AI / HTTP / SSE。
验证 tool_content_read 的缓存读取协议、next_offset 续读、session_id 隔离和 limit 缺省。

使用方式:
    uv run python test/test_tool_content_read_unit.py
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid
from typing import Optional

from chat.application.tool_content_store import tool_content_store, read_tool_content_window
from chat.application.tools import ToolContentReadTool


SESSION_ID = uuid.uuid4().hex[:16]
OTHER_SESSION_ID = uuid.uuid4().hex[:16]


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def extract_metadata_value(output: str, key: str) -> Optional[str]:
    pattern = rf"^{re.escape(key)}:\s*(.*)$"
    match = re.search(pattern, output, flags=re.MULTILINE)
    if not match:
        return None

    value = match.group(1).strip()
    return value or None


def extract_next_offset(output: str) -> int:
    value = extract_metadata_value(output, "next_offset")
    assert_true(value is not None, "next_offset 不应为空")
    assert_true(value.isdigit(), f"next_offset 应为数字，实际: {value}")
    return int(value)


def build_long_text() -> str:
    paragraphs = []

    for i in range(1, 80):
        paragraphs.append(
            f"## Section {i}\n\n"
            f"这是第 {i} 段测试内容，用于验证 tool_content_read 的分块读取能力。"
            f"本段包含足够多的字符，确保 RecursiveCharacterTextSplitter 会生成多个 chunk。"
            f"我们希望第一段读取后返回 truncated=true，并给出 next_offset，"
            f"随后第二次调用可以从 next_offset 继续读取，而不是重复第一段。"
        )

    return "\n\n".join(paragraphs)


async def test_missing_session_id(tool: ToolContentReadTool) -> None:
    result = await tool.execute({}, content_id="fake")
    assert_true(
        "[Tool Error] Missing session_id" in result,
        "缺失 session_id 时应返回 Tool Error",
    )
    print("✓ missing session_id")


async def test_missing_content_id(tool: ToolContentReadTool) -> None:
    result = await tool.execute({"session_id": SESSION_ID})
    assert_true(
        "[Tool Error] Missing required content_id parameter" in result,
        "缺失 content_id 时应返回 Tool Error",
    )
    print("✓ missing content_id")


async def test_read_cached_content(tool: ToolContentReadTool) -> None:
    text = build_long_text()

    content_id = tool_content_store.put(
        session_id=SESSION_ID,
        tool_name="unit_test",
        source="memory://tool-content-read-unit",
        text=text,
        content_type="text/markdown",
    )

    assert_true(content_id is not None, "长文本应成功写入 ToolContentStore")
    print(f"content_id: {content_id}")

    first = await tool.execute(
        {"session_id": SESSION_ID},
        content_id=content_id,
        offset=0,
        limit=900,
    )

    assert_true("[ToolContent Metadata]" in first, "返回应包含 ToolContent Metadata")
    assert_true("[Content]" in first, "返回应包含 Content 区块")
    assert_true("content_cached: true" in first, "缓存内容读取应标记 content_cached=true")
    assert_true("truncated: true" in first, "第一次读取长文本应 truncated=true")
    assert_true("Section 1" in first, "第一段应包含 Section 1")

    next_offset = extract_next_offset(first)
    assert_true(next_offset > 0, "next_offset 应大于 0")

    second = await tool.execute(
        {"session_id": SESSION_ID},
        content_id=content_id,
        offset=next_offset,
        limit=900,
    )

    assert_true("[ToolContent Metadata]" in second, "第二次返回应包含 ToolContent Metadata")
    assert_true("content_cached: true" in second, "第二次读取仍应来自缓存")
    assert_true(first != second, "第二次读取内容不应完全等于第一次")
    assert_true("offset:" in second, "第二次返回应包含 offset")

    wrong_session = await tool.execute(
        {"session_id": OTHER_SESSION_ID},
        content_id=content_id,
        offset=0,
        limit=900,
    )

    assert_true(
        "Cached tool content not found, expired, or inaccessible" in wrong_session,
        "不同 session_id 不应能读取缓存内容",
    )

    print("✓ read cached content")
    print("✓ read next window")
    print("✓ session isolation")


async def test_read_tool_content_window_default_limit() -> None:
    text = build_long_text()

    content_id = tool_content_store.put(
        session_id=SESSION_ID,
        tool_name="unit_test",
        source="memory://default-limit-test",
        text=text,
        content_type="text/markdown",
    )

    assert_true(content_id is not None, "长文本应成功写入 ToolContentStore")

    result = read_tool_content_window(
        session_id=SESSION_ID,
        content_id=content_id,
        offset=0,
    )

    assert_true("[ToolContent Metadata]" in result, "缺省 limit 返回应包含 ToolContent Metadata")
    assert_true("content_cached: true" in result, "缺省 limit 仍应来自缓存")

    print("✓ read_tool_content_window default limit")


async def test_read_tool_content_window_empty_content_id() -> None:
    result = read_tool_content_window(
        session_id=SESSION_ID,
        content_id="",
    )

    assert_true(
        "[Tool Error] Missing required content_id parameter" in result,
        "空 content_id 应返回 Tool Error",
    )

    result = read_tool_content_window(
        session_id=SESSION_ID,
        content_id="   ",
    )

    assert_true(
        "[Tool Error] Missing required content_id parameter" in result,
        "空白 content_id 应返回 Tool Error",
    )

    print("✓ read_tool_content_window empty content_id")


async def test_read_tool_content_window_session_isolation() -> None:
    text = build_long_text()

    content_id = tool_content_store.put(
        session_id=SESSION_ID,
        tool_name="unit_test",
        source="memory://isolation-test",
        text=text,
        content_type="text/markdown",
    )

    result = read_tool_content_window(
        session_id=OTHER_SESSION_ID,
        content_id=content_id,
        offset=0,
    )

    assert_true(
        "Cached tool content not found, expired, or inaccessible" in result,
        "不同 session_id 通过 read_tool_content_window 不应能读取缓存内容",
    )

    print("✓ read_tool_content_window session isolation")


async def main() -> int:
    print("ToolContentReadTool 单一功能测试")

    tool = ToolContentReadTool()

    try:
        await test_missing_session_id(tool)
        await test_missing_content_id(tool)
        await test_read_cached_content(tool)
        await test_read_tool_content_window_default_limit()
        await test_read_tool_content_window_empty_content_id()
        await test_read_tool_content_window_session_isolation()
    except AssertionError as e:
        print(f"\n✗ FAIL: {e}")
        return 1
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        return 1

    print("\n✓ PASS all unit tests")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
