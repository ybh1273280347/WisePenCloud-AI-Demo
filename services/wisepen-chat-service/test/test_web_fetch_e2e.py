"""
WebFetchTool / document_parse 端到端测试

通过 HTTP 调用 /chat/completions 接口，让 AI 自主决策调用 web_fetch 工具，
验证完整 SSE 流式协议、工具调用链路和最终回答可用性。

核心测试目标：
1. HTML 网页：模型自动调用 web_fetch 抓取并总结
2. PDF / DOCX / PPTX / XLSX 直链：模型自动完成 web_fetch -> file_ref handoff -> document_parse
3. 扫描版 PDF：模型自动完成 web_fetch -> file_ref handoff -> document_parse OCR -> 最终总结
4. 最终回答基于解析后的文件内容进行总结

设计原则：
- prompt 不写死"必须调用 web_fetch"，让模型自主决策
- 不要求精确措辞或数值，只检查关键词命中
- 真实公网 URL，环境变量可覆盖
- 单个 case 失败不中断后续 case
- 失败分层归因：区分 web_fetch 未调用 / handoff 失败 / document_parse 未继续

使用方式:
    uv run python test/test_web_fetch_e2e.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any, Dict, List, Optional

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

DEFAULT_TIMEOUT = int(os.getenv("E2E_TIMEOUT", "180"))

RESULT_PREVIEW_CHARS = 800


def env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


TEST_CASES: List[Dict[str, Any]] = [
    {
        "name": "HTML-Python",
        "url": os.getenv("E2E_HTML_URL", "https://www.python.org/"),
        "prompt": "请打开这个网页，并用中文概括它的主要内容：\n{url}",
        "expect_web_fetch": True,
        "expect_document_parse": False,
        "min_steps": 1,
        "expected_keywords": ["python", "programming", "language", "software", "编程", "语言"],
        "timeout_seconds": None,
    },
    {
        "name": "PDF-Document-Handoff",
        "url": os.getenv("E2E_PDF_URL", "https://arxiv.org/pdf/1706.03762.pdf"),
        "prompt": "请打开这个链接并阅读其中的 PDF 文档内容，然后用中文概括文档主要内容。链接可能会直接下载文件，请读取文件内容后再总结：\n{url}",
        "expect_web_fetch": True,
        "expect_document_parse": True,
        "min_steps": 2,
        "expected_keywords": ["attention", "transformer", "translation", "encoder", "decoder", "注意力", "文档"],
        "timeout_seconds": None,
    },
    {
        "name": "PDF-Scanned-OCR-Handoff",
        "url": os.getenv(
            "E2E_OCR_PDF_URL",
            "https://nlsblog.org/wp-content/uploads/2020/06/image-based-pdf-sample.pdf",
        ),
        "prompt": (
            "请打开这个链接并阅读其中的扫描版 PDF 文档内容，"
            "如果它是图片型 PDF，请尽量识别其中的文字，然后用中文概括内容：\n{url}"
        ),
        "expect_web_fetch": True,
        "expect_document_parse": True,
        "expect_handoff": True,
        "min_steps": 2,
        "expected_keywords": [
            "ocr",
            "scanned",
            "scan",
            "image",
            "image-based",
            "pdf",
            "扫描",
            "图片",
            "文档",
        ],
        "allow_ocr_unavailable": True,
        "timeout_seconds": None,
    },
    {
        "name": "DOCX-Document-Handoff",
        "url": os.getenv("E2E_DOCX_URL", "https://calibre-ebook.com/downloads/demos/demo.docx"),
        "prompt": "请打开这个链接并读取其中的 Word 文档内容，然后用中文概括文档主要内容。链接可能会直接下载 DOCX 文件，请读取文件内容后再总结：\n{url}",
        "expect_web_fetch": True,
        "expect_document_parse": True,
        "min_steps": 2,
        "expected_keywords": ["docx", "word", "calibre", "ebook", "文档", "演示"],
        "timeout_seconds": None,
    },
    {
        "name": "PPTX-Document-Handoff",
        "url": os.getenv(
            "E2E_PPTX_URL",
            "https://raw.githubusercontent.com/aws-samples/aws-nlp-workshop/master/Presentation-AWS-NLP-workshop.pptx",
        ),
        "prompt": "请打开这个链接并读取其中的演示文稿内容，然后用中文概括幻灯片主要内容。链接可能会直接下载 PPTX 文件，请读取文件内容后再总结：\n{url}",
        "expect_web_fetch": True,
        "expect_document_parse": True,
        "min_steps": 2,
        "expected_keywords": ["pptx", "presentation", "slide", "aws", "nlp", "workshop", "comprehend", "幻灯片", "演示"],
        "timeout_seconds": None,
    },
    {
        "name": "XLSX-Document-Handoff",
        "url": os.getenv("E2E_XLSX_URL", "https://raw.githubusercontent.com/LEARNEREA/Excel_Files/master/Products.xlsx"),
        "prompt": "请打开这个链接并读取其中的表格文件内容，然后用中文概括数据集的主要字段和数据含义。链接可能会直接下载 Excel 文件，请读取文件内容后再总结：\n{url}",
        "expect_web_fetch": True,
        "expect_document_parse": True,
        "min_steps": 2,
        "expected_keywords": ["excel", "xlsx", "product", "products", "price", "category", "表格", "字段", "产品"],
        "timeout_seconds": None,
    },
]

if env_flag("E2E_ENABLE_REDIRECT_CASE"):
    TEST_CASES.append(
        {
            "name": "Redirect-Document-Handoff",
            "url": os.getenv(
                "E2E_REDIRECT_DOC_URL",
                "https://go.microsoft.com/fwlink/?LinkID=521962",
            ),
            "prompt": (
                "请打开这个可能会跳转到文件下载地址的链接，并读取最终文件内容，"
                "然后用中文概括文件里的主要信息：\n{url}"
            ),
            "expect_web_fetch": True,
            "expect_document_parse": True,
            "expect_handoff": True,
            "min_steps": 2,
            "expected_keywords": ["excel", "xlsx", "sales", "profit", "字段", "表格"],
            "require_keyword_hit": True,
            "optional": True,
            "timeout_seconds": None,
        }
    )

if env_flag("E2E_ENABLE_UNSUPPORTED_CASES"):
    TEST_CASES.append(
        {
            "name": "CSV-Unsupported-NoDocumentParseSuccess",
            "url": os.getenv(
                "E2E_UNSUPPORTED_DOC_URL",
                "https://raw.githubusercontent.com/plotly/datasets/master/2014_usa_states.csv",
            ),
            "prompt": (
                "请打开这个链接并读取其中的文件内容，然后用中文概括它包含的数据。"
                "如果它不是支持的二进制文档格式，请直接说明可读取到的内容或限制：\n{url}"
            ),
            "expect_web_fetch": True,
            "expect_document_parse": False,
            "expect_handoff": False,
            "expect_final_answer": False,
            "expect_unsupported": True,
            "allow_document_parse_call": True,
            "min_steps": 1,
            "expected_keywords": ["csv", "state", "rank", "postal", "数据"],
            "require_keyword_hit": False,
            "optional": True,
            "timeout_seconds": None,
        }
    )


def get_tool_call_sequence(analysis: Dict[str, Any]) -> List[str]:
    return [
        tc["tool_name"]
        for tc in analysis.get("tool_calls", [])
        if tc.get("phase") == "input_start" and tc.get("tool_name")
    ]


def extract_tool_results(analysis: Dict[str, Any], tool_name: str) -> List[str]:
    """从 analysis 中提取指定工具的所有 output 文本。"""
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


def detect_web_fetch_result_signals(output_text: str) -> Dict[str, bool]:
    """检测 web_fetch 返回结果中的关键信号。"""
    text_lower = output_text.lower()
    return {
        "has_file_ref": "file_ref" in text_lower or "fileref" in text_lower,
        "has_downloaded_document": "downloaded a document" in text_lower,
        "has_failed_to_fetch": "failed to fetch" in text_lower,
        "has_tool_error": "[tool error]" in text_lower or "tool error" in text_lower,
        "looks_like_markdown": "#" in output_text and ("http" in text_lower or "www" in text_lower),
    }


def detect_document_parse_result_signals(output_text: str) -> Dict[str, bool]:
    """检测 document_parse / final answer 中和解析、OCR 相关的信号。"""
    text_lower = output_text.lower()
    ocr_unavailable_markers = [
        "ocr is disabled",
        "ocr disabled",
        "ocr backend is not available",
        "ocr_backend_unavailable",
        "backend_unavailable",
        "not available or not installed",
        "paddle unavailable",
        "worker unavailable",
        "ocr worker",
        "ocr processing timed out",
    ]
    empty_content_markers = [
        "no text extracted",
        "empty content",
        "empty text",
        "ocr produced no text",
        "produced no text",
        "no content extracted",
        "无法提取",
        "未提取到",
        "没有提取",
        "空文本",
    ]
    return {
        "has_tool_error": "[tool error]" in text_lower or "tool error" in text_lower,
        "has_parse_failure": "parse failed" in text_lower or "parsing failed" in text_lower or "解析失败" in output_text,
        "has_ocr_unavailable": any(marker in text_lower for marker in ocr_unavailable_markers),
        "has_empty_text": any(marker in text_lower for marker in empty_content_markers),
    }


def check_keyword_hit(full_text: str, keywords: List[str]) -> Dict[str, Any]:
    text_lower = full_text.lower()
    hits = [kw for kw in keywords if kw.lower() in text_lower]
    misses = [kw for kw in keywords if kw.lower() not in text_lower]
    return {
        "hits": hits,
        "misses": misses,
        "any_hit": len(hits) > 0,
    }


def validate_case(
    case: Dict[str, Any],
    analysis: Dict[str, Any],
) -> tuple[bool, List[str], str]:
    """验证单个测试用例，返回 (是否通过, 失败原因列表, 失败类别)。

    失败类别：
    - "": 通过，无类别
    - "protocol": SSE 协议不完整
    - "web_fetch_not_called": web_fetch 未被调用
    - "web_fetch_document_handoff_failed": web_fetch 调用了但未产生 file_ref
    - "document_parse_not_called_after_handoff": web_fetch 产生了 file_ref 但 document_parse 未继续
    - "document_parse_failed": document_parse 被调用但解析失败
    - "document_parse_ocr_content_failed": OCR 文档解析被调用但未得到可用内容
    - "ocr_unavailable": OCR 环境不可用，允许跳过
    - "unexpected_document_parse_success": unsupported 用例意外完成 document_parse
    - "document_parse_content_not_reflected": document_parse 调用了但关键词未命中
    - "empty_answer": 最终回复为空
    - "keyword_miss": 非 document 场景关键词未命中
    - "steps_insufficient": Agent Steps 不足
    """
    reasons: List[str] = []
    fail_category = ""

    if not analysis.get("has_start", False):
        reasons.append("缺少 SSE start 事件")
        fail_category = "protocol"

    if not analysis.get("has_finish", False):
        reasons.append("缺少 SSE finish 事件")
        fail_category = fail_category or "protocol"

    if analysis.get("steps", 0) < case["min_steps"]:
        reasons.append(
            f"Agent Steps 不足：{analysis.get('steps', 0)} < {case['min_steps']}"
        )
        fail_category = fail_category or "steps_insufficient"

    web_fetch_count = count_tool_calls(analysis, "web_fetch")
    document_parse_count = count_tool_calls(analysis, "document_parse")
    full_text = (analysis.get("full_text") or "").strip()
    wf_results = extract_tool_results(analysis, "web_fetch")
    dp_results = extract_tool_results(analysis, "document_parse")
    wf_combined = "\n".join(wf_results)
    dp_combined = "\n".join(dp_results)
    wf_signals = detect_web_fetch_result_signals(wf_combined)
    dp_signal_text = "\n".join([dp_combined, full_text])
    dp_signals = detect_document_parse_result_signals(dp_signal_text)
    expect_handoff = case.get("expect_handoff", case["expect_document_parse"])
    expect_final_answer = case.get("expect_final_answer", True)
    expect_document_parse_success = case.get(
        "expect_document_parse_success",
        case["expect_document_parse"],
    )
    expected_keywords = case.get("expected_keywords", [])
    require_keyword_hit = case.get(
        "require_keyword_hit",
        bool(expected_keywords) and expect_document_parse_success,
    )

    if case["expect_web_fetch"] and web_fetch_count < 1:
        reasons.append(f"期望调用 web_fetch，但实际调用 {web_fetch_count} 次")
        fail_category = fail_category or "web_fetch_not_called"

    if not case["expect_document_parse"] and document_parse_count > 0 and not case.get("allow_document_parse_call", False):
        reasons.append(f"不期望调用 document_parse，但实际调用 {document_parse_count} 次")
        fail_category = fail_category or "unexpected_document_parse_call"

    if expect_handoff:
        if web_fetch_count >= 1:
            if not wf_signals["has_file_ref"]:
                reasons.append(
                    "web_fetch 调用了但未返回文档 handoff（无 file_ref），"
                    "document_parse 未被触发或无法基于 file_ref 解析"
                )
                fail_category = fail_category or "web_fetch_document_handoff_failed"

    if case["expect_document_parse"]:
        if web_fetch_count >= 1 and wf_signals["has_file_ref"]:
            if document_parse_count < 1:
                reasons.append(
                    "web_fetch 返回了 file_ref，"
                    "但 document_parse 未被调用"
                )
                fail_category = fail_category or "document_parse_not_called_after_handoff"

    if (
        case.get("allow_ocr_unavailable")
        and web_fetch_count >= 1
        and wf_signals["has_file_ref"]
        and document_parse_count >= 1
        and dp_signals["has_ocr_unavailable"]
        and fail_category not in {"protocol", "web_fetch_not_called", "web_fetch_document_handoff_failed", "document_parse_not_called_after_handoff"}
    ):
        return (
            True,
            ["OCR 环境明确不可用，跳过扫描 PDF 内容断言"],
            "ocr_unavailable",
        )

    if document_parse_count >= 1 and expect_document_parse_success:
        if case.get("allow_ocr_unavailable") and (dp_signals["has_empty_text"] or not full_text):
            reasons.append("document_parse 已调用，但扫描 PDF/OCR 未返回可用文本内容")
            fail_category = fail_category or "document_parse_ocr_content_failed"
        elif dp_signals["has_tool_error"] or dp_signals["has_parse_failure"]:
            reasons.append("document_parse 已调用，但返回了解析错误")
            fail_category = fail_category or "document_parse_failed"

    if case.get("expect_unsupported") and document_parse_count >= 1:
        if not dp_signals["has_tool_error"] and not dp_signals["has_parse_failure"]:
            reasons.append("unsupported 文件类型用例不应误报 document_parse 成功")
            fail_category = fail_category or "unexpected_document_parse_success"

    if expect_final_answer and not full_text:
        reasons.append("最终回复为空")
        fail_category = fail_category or "empty_answer"

    if require_keyword_hit and expected_keywords and full_text:
        kw_result = check_keyword_hit(full_text, expected_keywords)
        if not kw_result["any_hit"]:
            if case["expect_document_parse"] and document_parse_count >= 1:
                reasons.append(
                    f"document_parse 已调用但关键词未命中：{kw_result['misses']}"
                )
                fail_category = fail_category or "document_parse_content_not_reflected"
            else:
                reasons.append(f"关键词未命中：{kw_result['misses']}")
                fail_category = fail_category or "keyword_miss"

    return not reasons, reasons, fail_category


def check_sequence_soft(case: Dict[str, Any], analysis: Dict[str, Any]) -> Optional[str]:
    if not case["expect_document_parse"]:
        return None

    sequence = get_tool_call_sequence(analysis)
    if not sequence:
        return None

    web_fetch_indices = [i for i, name in enumerate(sequence) if name == "web_fetch"]
    doc_parse_indices = [i for i, name in enumerate(sequence) if name == "document_parse"]

    if not web_fetch_indices or not doc_parse_indices:
        return None

    first_wf = web_fetch_indices[0]
    first_dp = doc_parse_indices[0]

    if first_dp < first_wf:
        return (
            f"工具调用顺序异常：第一个 document_parse (位置 {first_dp}) "
            f"出现在第一个 web_fetch (位置 {first_wf}) 之前"
        )

    return None


async def run_single_test(
    client: httpx.AsyncClient,
    case: Dict[str, Any],
    idx: int,
) -> Dict[str, Any]:
    url = case["url"]
    prompt = case["prompt"].format(url=url)
    timeout = case.get("timeout_seconds") or DEFAULT_TIMEOUT

    print(f"\n{'=' * 80}")
    print(f"测试 #{idx}: {case['name']}")
    print(f"URL: {url}")
    print(f"期望 web_fetch: {'是' if case['expect_web_fetch'] else '否'}")
    print(f"期望 document_parse: {'是' if case['expect_document_parse'] else '否'}")
    print(f"超时: {timeout}s")
    print(f"{'=' * 80}")

    session_id = await create_session(client, title=f"web_fetch E2E - {case['name']}")
    print(f"会话已创建: {session_id}")

    try:
        t0 = time.monotonic()
        events = await stream_chat(
            client,
            session_id,
            prompt,
            timeout_seconds=timeout,
        )
        elapsed = time.monotonic() - t0

        analysis = analyze_events(events)

        web_fetch_count = count_tool_calls(analysis, "web_fetch")
        document_parse_count = count_tool_calls(analysis, "document_parse")
        full_text = (analysis.get("full_text") or "").strip()

        wf_results = extract_tool_results(analysis, "web_fetch")
        dp_results = extract_tool_results(analysis, "document_parse")

        wf_combined = "\n".join(wf_results)
        dp_combined = "\n".join(dp_results)

        wf_signals = detect_web_fetch_result_signals(wf_combined) if wf_combined else {}
        dp_signal_text = "\n".join([dp_combined, full_text])
        dp_signals = detect_document_parse_result_signals(dp_signal_text) if dp_signal_text.strip() else {}
        web_fetch_handoff_success = bool(wf_signals.get("has_file_ref", False))
        document_parse_called = document_parse_count > 0

        analysis["name"] = case["name"]
        analysis["url"] = url
        analysis["elapsed_seconds"] = round(elapsed, 2)
        analysis["web_fetch_call_count"] = web_fetch_count
        analysis["document_parse_call_count"] = document_parse_count
        analysis["web_fetch_result_preview"] = shorten(wf_combined, RESULT_PREVIEW_CHARS) if wf_combined else ""
        analysis["document_parse_result_preview"] = shorten(dp_combined, RESULT_PREVIEW_CHARS) if dp_combined else ""
        analysis["web_fetch_signals"] = wf_signals
        analysis["document_parse_signals"] = dp_signals
        analysis["web_fetch_handoff_success"] = web_fetch_handoff_success
        analysis["document_parse_called"] = document_parse_called
        analysis["ocr_unavailable"] = bool(dp_signals.get("has_ocr_unavailable", False))
        analysis["document_parse_empty_content_signal"] = bool(dp_signals.get("has_empty_text", False))
        analysis["document_parse_tool_error"] = bool(dp_signals.get("has_tool_error", False))

        ok, failure_reasons, fail_category = validate_case(case, analysis)
        analysis["ok"] = ok
        analysis["failure_reasons"] = failure_reasons
        analysis["fail_category"] = fail_category
        analysis["skipped"] = ok and fail_category == "ocr_unavailable"

        sequence_warning = check_sequence_soft(case, analysis)
        analysis["sequence_warning"] = sequence_warning

        expected_keywords = case.get("expected_keywords", [])
        kw_result = check_keyword_hit(full_text, expected_keywords) if full_text else {"hits": [], "misses": expected_keywords, "any_hit": False}
        analysis["keyword_hits"] = kw_result["hits"]
        analysis["keyword_misses"] = kw_result["misses"]

        print("\n--- 事件统计 ---")
        print(f"总事件数: {analysis.get('total_events')}")
        print(f"耗时: {analysis['elapsed_seconds']}s")
        print(f"Agent Steps: {analysis.get('steps')}")
        print(
            "协议完整性: "
            f"start={analysis.get('has_start')} finish={analysis.get('has_finish')}"
        )
        print(f"web_fetch 调用数: {web_fetch_count}")
        print(f"document_parse 调用数: {document_parse_count}")
        print(f"web_fetch handoff 成功: {'是' if web_fetch_handoff_success else '否'}")
        print(f"document_parse 已调用: {'是' if document_parse_called else '否'}")

        sequence = get_tool_call_sequence(analysis)
        print(f"工具调用顺序: {' -> '.join(sequence) if sequence else '(无)'}")

        print("\n--- web_fetch 结果信号 ---")
        if wf_signals:
            for signal_name, signal_val in wf_signals.items():
                marker = "✓" if signal_val else "✗"
                print(f"  {marker} {signal_name}: {signal_val}")
        else:
            print("  (无 web_fetch 输出)")

        if wf_combined:
            print("\n--- web_fetch 结果预览 ---")
            print(shorten(wf_combined, RESULT_PREVIEW_CHARS))

        if dp_combined:
            print("\n--- document_parse 结果预览 ---")
            print(shorten(dp_combined, RESULT_PREVIEW_CHARS))

        if case.get("allow_ocr_unavailable"):
            print("\n--- OCR 结果信号 ---")
            print(f"  OCR unavailable: {analysis['ocr_unavailable']}")
            print(f"  document_parse 空文本相关提示: {analysis['document_parse_empty_content_signal']}")
            print(f"  document_parse Tool Error: {analysis['document_parse_tool_error']}")

        if full_text:
            print("\n--- AI 回复预览 ---")
            print(shorten(full_text, 700))
        else:
            print("\n--- AI 回复为空 ---")

        if kw_result["hits"] or kw_result["misses"]:
            print("\n--- 关键词命中 ---")
            print(f"命中: {kw_result['hits']}")
            print(f"未命中: {kw_result['misses']}")

        if sequence_warning:
            print(f"\n⚠ 顺序警告（不计入失败）: {sequence_warning}")

        if analysis["skipped"]:
            print(f"\n--- SKIP: {fail_category} ---")
            for reason in failure_reasons:
                print(f"  - {reason}")
        elif fail_category:
            print(f"\n--- 失败归因类别: {fail_category} ---")
            for reason in failure_reasons:
                print(f"  ✗ {reason}")
        elif failure_reasons:
            print("\n--- 失败原因 ---")
            for reason in failure_reasons:
                print(f"  ✗ {reason}")

        return analysis

    except Exception as e:
        print(f"\n✗ 测试 #{idx} 异常: {e}")
        return {
            "name": case["name"],
            "url": url,
            "error": str(e),
            "ok": False,
            "failure_reasons": [f"异常: {e}"],
            "fail_category": "exception",
            "elapsed_seconds": None,
            "web_fetch_call_count": 0,
            "document_parse_call_count": 0,
            "web_fetch_result_preview": "",
            "document_parse_result_preview": "",
            "web_fetch_signals": {},
            "document_parse_signals": {},
            "web_fetch_handoff_success": False,
            "document_parse_called": False,
            "ocr_unavailable": False,
            "document_parse_empty_content_signal": False,
            "document_parse_tool_error": False,
            "skipped": False,
        }
    finally:
        await delete_session(client, session_id)


async def main() -> int:
    print("WebFetchTool / document_parse 端到端测试")
    print(f"目标: {BASE_URL}")
    print(f"用户: {TEST_USER_ID}")
    print(f"用例数: {len(TEST_CASES)}")
    print(f"默认超时: {DEFAULT_TIMEOUT}s")

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
    skipped = 0

    for result in results:
        name = result.get("name", "?")
        ok = bool(result.get("ok", False))
        is_skipped = bool(result.get("skipped", False))

        if is_skipped:
            skipped += 1
            status = "↷ SKIP"
        elif ok:
            passed += 1
            status = "✓ PASS"
        else:
            failed += 1
            status = "✗ FAIL"

        reasons = result.get("failure_reasons") or []
        fail_cat = result.get("fail_category", "")
        detail = f" [{fail_cat}]" if fail_cat else ""
        if reasons:
            detail += f" ({'; '.join(reasons)})"

        seq_warn = result.get("sequence_warning")
        seq_info = f" [顺序警告: {seq_warn}]" if seq_warn else ""

        kw_hits = result.get("keyword_hits", [])
        kw_info = ""
        if kw_hits:
            kw_info = f" 关键词命中={kw_hits}"

        wf_signals = result.get("web_fetch_signals", {})
        signal_info = ""
        if wf_signals:
            active_signals = [k for k, v in wf_signals.items() if v]
            if active_signals:
                signal_info = f" 信号={active_signals}"

        handoff_info = f" handoff={result.get('web_fetch_handoff_success', False)}"
        dp_called_info = f" dp_called={result.get('document_parse_called', False)}"
        ocr_info = ""
        if result.get("name") == "PDF-Scanned-OCR-Handoff":
            ocr_info = (
                f" ocr_unavailable={result.get('ocr_unavailable', False)}"
                f" ocr_empty={result.get('document_parse_empty_content_signal', False)}"
            )

        print(
            f"  {status} {name}: "
            f"steps={result.get('steps', '?')}, "
            f"web_fetch={result.get('web_fetch_call_count', 0)}, "
            f"document_parse={result.get('document_parse_call_count', 0)}, "
            f"elapsed={result.get('elapsed_seconds', '?')}s"
            f"{handoff_info}"
            f"{dp_called_info}"
            f"{kw_info}"
            f"{signal_info}"
            f"{ocr_info}"
            f"{seq_info}"
            f"{detail}"
        )

    total = passed + failed + skipped
    print(f"\n通过: {passed}/{total}，跳过: {skipped}，失败: {failed}")

    if failed > 0:
        print("\n失败用例详情：")
        for result in results:
            if not result.get("ok", False):
                name = result.get("name", "?")
                fail_cat = result.get("fail_category", "")
                reasons = result.get("failure_reasons") or []
                error = result.get("error")
                wf_preview = result.get("web_fetch_result_preview", "")
                dp_preview = result.get("document_parse_result_preview", "")
                wf_signals = result.get("web_fetch_signals", {})
                dp_signals = result.get("document_parse_signals", {})

                print(f"  - {name} [类别: {fail_cat}]")
                for r in reasons:
                    print(f"      {r}")
                if error:
                    print(f"      异常: {error}")
                if wf_signals:
                    active = [k for k, v in wf_signals.items() if v]
                    inactive = [k for k, v in wf_signals.items() if not v]
                    if active:
                        print(f"      web_fetch 信号命中: {active}")
                    if inactive:
                        print(f"      web_fetch 信号未命中: {inactive}")
                if dp_signals:
                    active_dp = [k for k, v in dp_signals.items() if v]
                    if active_dp:
                        print(f"      document_parse 信号命中: {active_dp}")
                if wf_preview:
                    print(f"      web_fetch 结果预览: {wf_preview[:300]}")
                if dp_preview:
                    print(f"      document_parse 结果预览: {dp_preview[:300]}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
