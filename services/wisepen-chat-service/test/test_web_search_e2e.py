"""
WebSearchTool 真实场景端到端测试

通过 HTTP 调用 /chat/completions 接口，让 AI 自主决策调用 web_search 工具，
验证完整 SSE 流式协议、工具调用链路和最终回答可用性。

测试原则：
1. 不死板限定 web_search 的具体参数。
2. 不要求模型必须使用某个 mode。
3. 不测试 Tool Schema 细节；Schema 单测另测。
4. 只测试真实用户场景下：
   - SSE 是否完整
   - web_search 是否被调用
   - deep 模式是否通过 web_search 内部 fetcher 读取页面内容
   - Agent Step 是否存在
   - 最终回答是否可用
   - 错误是否可接受
5. image / 多 query 倾向只作为软检查，不作为硬失败条件。

使用方式:
    uv run python test/test_web_search_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

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

TIMEOUT_SECONDS = int(os.getenv("E2E_TIMEOUT", "180"))


@dataclass(frozen=True)
class WebSearchE2ECase:
    name: str
    user_request: str
    expected_intent: str
    min_answer_chars: int = 80
    expected_keywords: List[str] = field(default_factory=list)
    prefer_deep: bool = False
    require_page_fetch: bool = False
    prefer_images: bool = False
    allow_sparse_result: bool = False


TEST_CASES: List[WebSearchE2ECase] = [
    WebSearchE2ECase(
        name="普通事实查询",
        user_request="法国的首都是哪里？请用 web_search 查证后用中文简要回答。",
        expected_intent="normal factual search",
        min_answer_chars=30,
        expected_keywords=["巴黎", "法国"],
    ),
    WebSearchE2ECase(
        name="技术对比查询",
        user_request=(
            "请用 web_search 查询 Python asyncio.gather 和 asyncio.wait 的区别，"
            "结合搜索结果用中文总结适用场景。"
        ),
        expected_intent="technical comparison",
        min_answer_chars=180,
        expected_keywords=["gather", "wait", "asyncio"],
        prefer_deep=True,
    ),
    WebSearchE2ECase(
        name="深度页面提取查询",
        user_request=(
            "请用 web_search 检索 Python asyncio TaskGroup 的官方文档和相关资料，"
            "阅读搜索结果页面内容后，用中文总结 TaskGroup 的用途、异常处理特点和适用场景。"
        ),
        expected_intent="deep search with page content extraction",
        min_answer_chars=180,
        expected_keywords=["TaskGroup", "asyncio", "异常", "任务"],
        prefer_deep=True,
        require_page_fetch=True,
    ),
    WebSearchE2ECase(
        name="中文技术研究",
        user_request=(
            "请用 web_search 查询 Docker Compose 网络配置和服务名解析的最佳实践，"
            "请基于搜索结果用中文总结。"
        ),
        expected_intent="Chinese technical research",
        min_answer_chars=180,
        expected_keywords=["Docker", "Compose", "网络"],
        prefer_deep=True,
    ),
    WebSearchE2ECase(
        name="图片相关查询",
        user_request=(
            "请用 web_search 搜索埃菲尔铁塔相关资料和图片线索，"
            "然后用中文说明你找到了什么。"
        ),
        expected_intent="image-oriented search",
        min_answer_chars=80,
        expected_keywords=["埃菲尔", "铁塔"],
        prefer_images=True,
    ),
    WebSearchE2ECase(
        name="低召回随机查询",
        user_request=(
            "请用 web_search 搜索这个非常罕见的字符串："
            "xyznonexistentquery12345 qwertyuiop。"
            "如果没有可靠结果，请直接说明搜索结果不足。"
        ),
        expected_intent="sparse or no-result search",
        min_answer_chars=20,
        allow_sparse_result=True,
    ),
]


def build_prompt(case: WebSearchE2ECase) -> str:
    hints: List[str] = [
        "请真实调用 web_search 工具完成，不要只依赖已有知识。",
        "你可以自行决定如何拆分 queries，以及使用 normal 还是 deep 模式。",
        "不要为了测试硬凑参数；按你认为最适合用户问题的方式调用工具。",
    ]

    if case.prefer_deep:
        hints.append("这个问题需要较可靠的资料支撑，必要时读取搜索结果页面内容。")

    if case.prefer_images:
        hints.append("如果你认为图片结果有帮助，可以请求图片结果。")

    return (
        f"{case.user_request}\n\n"
        "执行要求：\n"
        + "\n".join(f"- {hint}" for hint in hints)
    )


def flatten_event_strings(value: Any) -> Iterable[str]:
    if value is None:
        return

    if isinstance(value, str):
        yield value
        return

    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from flatten_event_strings(item)
        return

    if isinstance(value, list):
        for item in value:
            yield from flatten_event_strings(item)
        return

    if isinstance(value, tuple):
        for item in value:
            yield from flatten_event_strings(item)
        return

    try:
        yield str(value)
    except Exception:
        return


def serialize_events_for_search(events: List[Dict[str, Any]]) -> str:
    chunks: List[str] = []

    for event in events:
        try:
            chunks.append(json.dumps(event, ensure_ascii=False, default=str))
        except Exception:
            chunks.extend(flatten_event_strings(event))

    return "\n".join(chunks)


def get_tool_call_sequence(analysis: Dict[str, Any]) -> List[str]:
    return [
        tc["tool_name"]
        for tc in analysis.get("tool_calls", [])
        if tc.get("phase") == "input_start" and tc.get("tool_name")
    ]


def extract_tool_inputs(analysis: Dict[str, Any], tool_name: str) -> List[Dict[str, Any]]:
    return [
        tc.get("input") or {}
        for tc in analysis.get("tool_calls", [])
        if tc.get("phase") == "input_available" and tc.get("tool_name") == tool_name
    ]


def extract_tool_results(analysis: Dict[str, Any], tool_name: str) -> List[str]:
    call_id_to_name: Dict[str, str] = {}
    for tc in analysis.get("tool_calls", []):
        if tc.get("phase") == "input_start" and tc.get("tool_name"):
            call_id_to_name[tc["call_id"]] = tc["tool_name"]

    results: List[str] = []
    for tc in analysis.get("tool_calls", []):
        if tc.get("phase") != "output_available":
            continue
        call_id = tc.get("call_id", "")
        if call_id_to_name.get(call_id) == tool_name:
            output = tc.get("output", "")
            if output:
                results.append(output)

    return results


def detect_page_fetch_behavior(analysis: Dict[str, Any]) -> Dict[str, Any]:
    inputs = extract_tool_inputs(analysis, "web_search")
    outputs = extract_tool_results(analysis, "web_search")
    combined_output = "\n".join(outputs)
    output_lower = combined_output.lower()
    requested_deep = any((item.get("mode") or "normal") == "deep" for item in inputs)
    page_content_blocks = len(re.findall(r"--- Page content for result #\d+ ---", combined_output))
    no_content_notes = [
        "Deep search was requested but no page content was read.",
        "No readable result URLs were available for page content reading.",
        "returned no content and was skipped.",
        "failed and was skipped.",
        "timed out.",
        "returned non-text content.",
    ]

    return {
        "web_search_inputs": inputs,
        "web_search_output_preview": shorten(combined_output, 1000) if combined_output else "",
        "requested_deep": requested_deep,
        "has_page_contents_section": "Page contents:" in combined_output,
        "page_content_blocks": page_content_blocks,
        "page_fetch_success": page_content_blocks > 0,
        "page_fetch_no_content_note": any(note.lower() in output_lower for note in no_content_notes),
        "tool_content_metadata_count": combined_output.count("[ToolContent Metadata]"),
        "web_fetch_tool_call_count": count_tool_calls(analysis, "web_fetch"),
    }


def detect_observed_tool_behavior(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    serialized = serialize_events_for_search(events)

    return {
        "mentions_web_search": "web_search" in serialized,
        "observed_deep": bool(re.search(r'"mode"\s*:\s*"deep"', serialized)),
        "observed_normal": bool(re.search(r'"mode"\s*:\s*"normal"', serialized)),
        "observed_with_images_true": bool(
            re.search(r'"with_images"\s*:\s*true', serialized, re.IGNORECASE)
        ),
        "observed_queries_array": bool(re.search(r'"queries"\s*:', serialized)),
    }


def collect_quality_warnings(
    case: WebSearchE2ECase,
    analysis: Dict[str, Any],
    observed: Dict[str, Any],
) -> List[str]:
    warnings: List[str] = []

    full_text = analysis.get("full_text") or ""

    if case.prefer_deep and not observed.get("observed_deep"):
        warnings.append("该场景倾向 deep，但未观察到 mode=deep。")

    page_fetch = analysis.get("page_fetch") or {}
    if case.prefer_deep and not page_fetch.get("page_fetch_success"):
        warnings.append("该场景倾向读取页面内容，但未观察到 Page contents 输出。")

    if case.prefer_images and not observed.get("observed_with_images_true"):
        warnings.append("该场景倾向图片搜索，但未观察到 with_images=true。")

    for keyword in case.expected_keywords:
        if keyword.lower() not in full_text.lower():
            warnings.append(f"最终回答未明显包含关键词：{keyword}")

    if not observed.get("observed_queries_array"):
        warnings.append("未在事件中观察到 queries 参数；可能是事件格式未暴露工具参数。")

    return warnings


def is_result_acceptable(
    case: WebSearchE2ECase,
    analysis: Dict[str, Any],
) -> tuple[bool, List[str]]:
    reasons: List[str] = []

    if not analysis.get("web_search_called", False):
        reasons.append("web_search 未被调用")

    if not analysis.get("has_start", False):
        reasons.append("缺少 SSE start 事件")

    if not analysis.get("has_finish", False):
        reasons.append("缺少 SSE finish 事件")

    if analysis.get("steps", 0) <= 0:
        reasons.append("没有 Agent Step")

    page_fetch = analysis.get("page_fetch") or {}
    if case.require_page_fetch:
        if not page_fetch.get("requested_deep"):
            reasons.append("该用例要求页面内容提取，但未观察到 web_search mode=deep")
        if not page_fetch.get("page_fetch_success"):
            if page_fetch.get("page_fetch_no_content_note"):
                reasons.append("web_search deep 已尝试页面提取，但没有读到页面内容")
            else:
                reasons.append("该用例要求页面内容提取，但未观察到 Page contents 输出")

    errors = analysis.get("errors") or []
    if errors:
        reasons.append(f"存在错误事件：{len(errors)} 个")

    full_text = (analysis.get("full_text") or "").strip()
    if not case.allow_sparse_result and len(full_text) < case.min_answer_chars:
        reasons.append(
            f"最终回答过短：{len(full_text)} chars < {case.min_answer_chars}"
        )

    if case.allow_sparse_result and not full_text:
        reasons.append("低召回场景也应给出简短说明，但最终回答为空")

    return not reasons, reasons


async def run_single_test(
    client: httpx.AsyncClient,
    case: WebSearchE2ECase,
    idx: int,
) -> Dict[str, Any]:
    prompt = build_prompt(case)

    print(f"\n{'=' * 80}")
    print(f"测试 #{idx}: {case.name}")
    print(f"意图: {case.expected_intent}")
    print(f"偏好 deep: {'是' if case.prefer_deep else '否'}")
    print(f"偏好图片: {'是' if case.prefer_images else '否'}")
    print(f"{'=' * 80}")

    session_id = await create_session(client, title=f"web_search E2E - {case.name}")
    print(f"会话已创建: {session_id}")

    try:
        t0 = time.monotonic()
        events = await stream_chat(
            client,
            session_id,
            prompt,
            timeout_seconds=TIMEOUT_SECONDS,
        )
        elapsed = time.monotonic() - t0

        analysis = analyze_events(events)
        observed = detect_observed_tool_behavior(events)

        web_search_count = count_tool_calls(analysis, "web_search")
        page_fetch = detect_page_fetch_behavior(analysis)

        analysis["name"] = case.name
        analysis["expected_intent"] = case.expected_intent
        analysis["session_id"] = session_id
        analysis["elapsed_seconds"] = round(elapsed, 2)
        analysis["web_search_called"] = web_search_count > 0
        analysis["web_search_call_count"] = web_search_count
        analysis["tool_call_sequence"] = get_tool_call_sequence(analysis)
        analysis["page_fetch"] = page_fetch
        analysis["observed"] = observed
        analysis["quality_warnings"] = collect_quality_warnings(
            case,
            analysis,
            observed,
        )

        ok, failure_reasons = is_result_acceptable(case, analysis)
        analysis["ok"] = ok
        analysis["failure_reasons"] = failure_reasons

        print("\n--- 事件统计 ---")
        print(f"总事件数: {analysis.get('total_events')}")
        print(f"耗时: {analysis['elapsed_seconds']}s")
        print(f"Agent Steps: {analysis.get('steps')}")
        print(
            "协议完整性: "
            f"start={analysis.get('has_start')} finish={analysis.get('has_finish')}"
        )
        print(f"web_search 调用数: {web_search_count}")
        print(f"web_fetch 工具调用数: {page_fetch['web_fetch_tool_call_count']}（web_search deep 页面提取走内部 fetcher，通常不会显示为 web_fetch 工具调用）")
        print(f"错误数: {len(analysis.get('errors') or [])}")
        print(f"工具调用顺序: {' -> '.join(analysis['tool_call_sequence']) if analysis['tool_call_sequence'] else '(无)'}")

        print("\n--- 观察到的工具行为 ---")
        print(f"web_search 事件痕迹: {observed['mentions_web_search']}")
        print(f"queries 参数痕迹: {observed['observed_queries_array']}")
        print(f"mode=normal: {observed['observed_normal']}")
        print(f"mode=deep: {observed['observed_deep']}")
        print(f"with_images=true: {observed['observed_with_images_true']}")

        print("\n--- 页面提取信号 ---")
        print(f"web_search input mode=deep: {page_fetch['requested_deep']}")
        print(f"web_search 输出 Page contents: {page_fetch['has_page_contents_section']}")
        print(f"页面内容块数: {page_fetch['page_content_blocks']}")
        print(f"页面提取成功: {page_fetch['page_fetch_success']}")
        print(f"页面提取失败/空内容 note: {page_fetch['page_fetch_no_content_note']}")
        print(f"ToolContent Metadata 数: {page_fetch['tool_content_metadata_count']}")
        if page_fetch["web_search_inputs"]:
            print("web_search tool input:")
            print(shorten(page_fetch["web_search_inputs"], 1000))
        if page_fetch["web_search_output_preview"]:
            print("web_search tool output preview:")
            print(page_fetch["web_search_output_preview"])

        full_text = analysis.get("full_text") or ""
        if full_text:
            print("\n--- AI 回复预览 ---")
            print(shorten(full_text, 700))

        if analysis["quality_warnings"]:
            print("\n--- 质量提醒，不计入失败 ---")
            for warning in analysis["quality_warnings"]:
                print(f"  ⚠ {warning}")

        if failure_reasons:
            print("\n--- 失败原因 ---")
            for reason in failure_reasons:
                print(f"  ✗ {reason}")

        return analysis

    except Exception as e:
        print(f"\n✗ 测试 #{idx} 异常: {e}")
        return {
            "name": case.name,
            "expected_intent": case.expected_intent,
            "error": str(e),
            "ok": False,
            "failure_reasons": [str(e)],
            "page_fetch": {},
        }
    finally:
        await delete_session(client, session_id)


async def main() -> int:
    print("WebSearchTool 真实场景端到端测试")
    print(f"目标: {BASE_URL}")
    print(f"用户: {TEST_USER_ID}")
    print(f"用例数: {len(TEST_CASES)}")
    print(f"超时: {TIMEOUT_SECONDS}s")

    async with httpx.AsyncClient(base_url=BASE_URL, headers=make_headers()) as client:
        if not await check_health(client):
            print(f"\n✗ 服务不可达: {BASE_URL}")
            print("请先启动 chat-service: uv run python -m chat.main")
            return 1

        print("✓ 服务可达")

        results: List[Dict[str, Any]] = []
        for idx, case in enumerate(TEST_CASES, 1):
            result = await run_single_test(client, case, idx)
            results.append(result)

    print(f"\n\n{'=' * 80}")
    print("汇总报告")
    print(f"{'=' * 80}")

    passed = 0
    failed = 0
    warning_count = 0

    for result in results:
        name = result.get("name", "?")
        ok = bool(result.get("ok", False))

        if ok:
            passed += 1
            status = "✓ PASS"
        else:
            failed += 1
            status = "✗ FAIL"

        warnings = result.get("quality_warnings") or []
        warning_count += len(warnings)

        reasons = result.get("failure_reasons") or []
        detail = f" ({', '.join(reasons)})" if reasons else ""

        print(
            f"  {status} {name}: "
            f"steps={result.get('steps', '?')}, "
            f"web_search={result.get('web_search_call_count', 0)}, "
            f"web_fetch_tool={result.get('page_fetch', {}).get('web_fetch_tool_call_count', 0)}, "
            f"deep={result.get('page_fetch', {}).get('requested_deep', False)}, "
            f"page_blocks={result.get('page_fetch', {}).get('page_content_blocks', 0)}, "
            f"elapsed={result.get('elapsed_seconds', '?')}s, "
            f"warnings={len(warnings)}"
            f"{detail}"
        )

    print(f"\n通过: {passed}/{passed + failed}")
    print(f"质量提醒: {warning_count}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
