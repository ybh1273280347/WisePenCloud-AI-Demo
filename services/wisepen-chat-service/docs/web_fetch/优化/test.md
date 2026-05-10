下面给你两个测试文件，风格按你现有 `web_fetch_e2e`：使用 `httpx.AsyncClient`、`create_session`、`stream_chat`、`analyze_events`、`count_tool_calls` 这一套 E2E 基础设施。

---

# 1. 单一功能测试：`test/test_tool_content_read_unit.py`

这个测试不经过 AI，不走 HTTP，直接验证：

```text
1. ToolContentStore 能缓存长文本
2. ToolContentReadTool 能按 content_id + offset + limit 读取窗口
3. 返回包含 ToolContent Metadata
4. truncated=true 时 next_offset 存在
5. 使用 next_offset 可以读下一段
6. session_id 隔离生效
7. 缺失 session_id / content_id 能返回 Tool Error
```

```python
"""
ToolContentReadTool 单一功能测试

直接调用 ToolContentReadTool.execute，不经过 AI / HTTP / SSE。
验证 tool_content_read 的缓存读取协议、next_offset 续读和 session_id 隔离。

使用方式:
    uv run python test/test_tool_content_read_unit.py
"""
from __future__ import annotations

import asyncio
import re
import sys
from typing import Optional

from chat.application.tool_content_store import tool_content_store
from chat.application.tools import ToolContentReadTool


SESSION_ID = "tool-content-read-unit-session"
OTHER_SESSION_ID = "tool-content-read-unit-other-session"


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


async def main() -> int:
    print("ToolContentReadTool 单一功能测试")

    tool = ToolContentReadTool()

    try:
        await test_missing_session_id(tool)
        await test_missing_content_id(tool)
        await test_read_cached_content(tool)
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
```

---

# 2. AI 集成测试：`test/test_tool_content_read_e2e.py`

这个测试让 AI 自己完成：

```text
1. 调用 web_fetch 抓取长文本 URL
2. 看到 web_fetch 返回 ToolContent Metadata 后继续调用 tool_content_read
3. 最终回复总结
```

为了让测试更快、更稳定，这里不要用 PDF，建议用 GitHub raw 长文本。它是纯文本，能稳定触发 `web_fetch` 的长内容缓存与截断。

```python
"""
ToolContentReadTool AI 集成测试

通过 HTTP 调用 /chat/completions 接口，让 AI 自主决策：
1. 调用 web_fetch 抓取长文本 URL；
2. 如果 web_fetch 返回 ToolContent Metadata 且 truncated=true，
   继续调用 tool_content_read 读取 next_offset 对应的下一段；
3. 最终输出中文总结。

该测试重点验证：
- web_fetch 被调用；
- tool_content_read 被调用；
- SSE 协议完整；
- Agent Step 存在；
- 工具链能完成长内容续读。

使用方式:
    uv run python test/test_tool_content_read_e2e.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from typing import Any, Dict, List

import httpx

from test_common import (
    BASE_URL,
    analyze_events,
    check_health,
    count_tool_calls,
    create_session,
    delete_session,
    make_headers,
    shorten,
    stream_chat,
)


TIMEOUT_SECONDS = int(__import__("os").getenv("E2E_TIMEOUT", "180"))


TEST_CASES = [
    (
        "长文本-CPython asyncio task 文档",
        "https://raw.githubusercontent.com/python/cpython/main/Doc/library/asyncio-task.rst",
    ),
]


def build_query(name: str, url: str) -> str:
    return (
        "请严格按以下步骤完成任务：\n"
        "1. 必须先调用 web_fetch 工具抓取这个 URL 的内容。\n"
        "2. 如果 web_fetch 的返回中出现 ToolContent Metadata，并且 content_cached=true、"
        "truncated=true、next_offset 非空，你必须继续调用 tool_content_read 工具读取 next_offset 对应的下一段内容。\n"
        "3. 最后用中文回答：你是否读取到了第一段和第二段内容，以及这两段大致分别在讲什么。\n"
        "4. 不要使用 web_search，不要只凭常识回答。\n\n"
        f"测试名称：{name}\n"
        f"URL：{url}"
    )


async def run_single_test(
    client: httpx.AsyncClient,
    name: str,
    url: str,
    idx: int,
) -> Dict[str, Any]:
    query = build_query(name, url)

    print(f"\n{'=' * 80}")
    print(f"测试 #{idx}: {name}")
    print(f"URL:  {url}")
    print(f"{'=' * 80}")

    session_id = await create_session(client, title=f"tool_content_read E2E - {name}")
    print(f"会话已创建: {session_id}")

    try:
        t0 = time.monotonic()
        events = await stream_chat(client, session_id, query, timeout_seconds=TIMEOUT_SECONDS)
        elapsed = time.monotonic() - t0

        analysis = analyze_events(events)
        analysis["name"] = name
        analysis["url"] = url
        analysis["session_id"] = session_id
        analysis["elapsed_seconds"] = round(elapsed, 2)

        web_fetch_count = count_tool_calls(analysis, "web_fetch")
        tool_content_read_count = count_tool_calls(analysis, "tool_content_read")

        analysis["web_fetch_called"] = web_fetch_count > 0
        analysis["web_fetch_call_count"] = web_fetch_count
        analysis["tool_content_read_called"] = tool_content_read_count > 0
        analysis["tool_content_read_call_count"] = tool_content_read_count

        print("\n--- 事件统计 ---")
        print(f"总事件数: {analysis['total_events']}")
        print(f"耗时: {analysis['elapsed_seconds']}s")
        print(f"Agent Steps: {analysis['steps']}")
        print(f"协议完整性: start={analysis['has_start']} finish={analysis['has_finish']}")
        print(f"web_fetch 调用数: {web_fetch_count}")
        print(f"tool_content_read 调用数: {tool_content_read_count}")
        print(f"错误数: {len(analysis['errors'])}")

        if analysis["full_text"]:
            print("\n--- AI 回复预览 ---")
            print(shorten(analysis["full_text"], 800))

        if analysis["errors"]:
            print("\n--- 错误 ---")
            for err in analysis["errors"]:
                print(f"  ✗ {err}")

        return analysis

    except Exception as e:
        print(f"\n✗ 测试 #{idx} 异常: {e}")
        return {
            "name": name,
            "url": url,
            "session_id": session_id,
            "error": str(e),
        }
    finally:
        await delete_session(client, session_id)


async def main() -> int:
    print("ToolContentReadTool AI 集成测试")
    print(f"目标: {BASE_URL}")
    print(f"用户: {__import__('os').getenv('TEST_USER_ID', 'e2e-test-user')}")
    print(f"用例数: {len(TEST_CASES)}")

    async with httpx.AsyncClient(base_url=BASE_URL, headers=make_headers()) as client:
        if not await check_health(client):
            print(f"\n✗ 服务不可达: {BASE_URL}")
            print("请先启动 chat-service: uv run python -m chat.main")
            return 1

        print("✓ 服务可达")

        results: List[Dict[str, Any]] = []

        for idx, (name, url) in enumerate(TEST_CASES, 1):
            result = await run_single_test(client, name, url, idx)
            results.append(result)

    print(f"\n\n{'=' * 80}")
    print("汇总报告")
    print(f"{'=' * 80}")

    passed = 0
    failed = 0

    for result in results:
        name = result.get("name", "?")

        if "error" in result:
            print(f"  ✗ FAIL {name}: 异常 - {result['error']}")
            failed += 1
            continue

        web_fetch_called = result.get("web_fetch_called", False)
        tool_content_read_called = result.get("tool_content_read_called", False)
        has_start = result.get("has_start", False)
        has_finish = result.get("has_finish", False)
        steps = result.get("steps", 0)

        ok = (
            web_fetch_called
            and tool_content_read_called
            and has_start
            and has_finish
            and steps > 0
        )

        if ok:
            passed += 1
            status = "✓ PASS"
        else:
            failed += 1
            status = "✗ FAIL"

        reasons = []
        if not web_fetch_called:
            reasons.append("web_fetch 未被调用")
        if not tool_content_read_called:
            reasons.append("tool_content_read 未被调用")
        if not has_start:
            reasons.append("缺少 start")
        if not has_finish:
            reasons.append("缺少 finish")
        if steps == 0:
            reasons.append("无 Agent Step")

        detail = f" ({', '.join(reasons)})" if reasons else ""

        print(
            f"  {status} {name}: "
            f"steps={steps}, "
            f"web_fetch={result.get('web_fetch_call_count', 0)}, "
            f"tool_content_read={result.get('tool_content_read_call_count', 0)}, "
            f"elapsed={result.get('elapsed_seconds', '?')}s"
            f"{detail}"
        )

    print(f"\n通过: {passed}/{passed + failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

---

# 一个关键提醒

这个 E2E 的成功依赖两个条件：

```text
1. web_fetch 返回的内容长度超过 TOOL_RESULT_MAX_CHARS，触发 truncated=true；
2. 模型遵守提示，看到 next_offset 后调用 tool_content_read。
```

所以我选了 GitHub raw 的 CPython 长文档，而不是短 HTML 页面。它更容易稳定触发 `tool_content_read`。
