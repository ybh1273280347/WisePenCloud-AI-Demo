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
    TEST_USER_ID,
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
        "RAW-CPython asyncio task rst 长文档",
        "https://raw.githubusercontent.com/python/cpython/main/Doc/library/asyncio-task.rst",
    ),
    (
        "PDF-Transformer 论文 Attention Is All You Need",
        "https://arxiv.org/pdf/1706.03762",
    ),
    (
        "HTML-Python asyncio task 文档长页面",
        "https://docs.python.org/3/library/asyncio-task.html",
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
    print(f"用户: {TEST_USER_ID}")
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