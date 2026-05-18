import base64
from typing import Dict, Optional

from chat.application.tools.services.web_fetch.content_processor import ContentProcessor
from common.logger import log_fail
from playwright.async_api import Page

from ..action_runtime import (
    get_existing_page_or_error,
    session_state,
)
from ..intervention import UserInterventionDetector
from ..protocol import (
    ActionResult,
    build_error_response,
    build_success_response,
    get_page_state,
    make_action_failed_error,
    make_schema_error,
)
from ..session import BrowserSessionManager
from ..snapshot import SnapshotManager

_SCREENSHOT_JPEG_QUALITY = 40

_RENDERED_CONTENT_SCRIPT = r"""() => {
    const candidateSelectors = [
        '#mw-content-text',
        '.mw-parser-output',
        'article',
        'main',
        '[role="main"]',
        '#content',
        '#main',
        '.content',
        '.main',
        '.article',
        '.markdown-body',
        '.repository-content',
        '.wiki-content',
        '.post',
        '.entry-content',
        '.document',
        '.docs-content'
    ];

    const removeSelectors = [
        'script',
        'style',
        'noscript',
        'template',
        'svg',
        'canvas',
        'iframe',
        '[hidden]',
        '[aria-hidden="true"]',
        '.mw-editsection',
        '.reference',
        '.reflist',
        '.navbox',
        '.metadata',
        '.ambox',
        '.vertical-navbox',
        '.sistersitebox',
        '.printfooter',
        '.catlinks',
        '#toc',
        '.toc'
    ];

    function cleanText(text) {
        return (text || '')
            .replace(/\r\n/g, '\n')
            .replace(/\r/g, '\n')
            .split('\n')
            .map(line => line.trim())
            .filter(line => line.length > 0)
            .join('\n')
            .replace(/\n{3,}/g, '\n\n')
            .trim();
    }

    function cloneAndClean(element) {
        const clone = element.cloneNode(true);

        for (const selector of removeSelectors) {
            clone.querySelectorAll(selector).forEach(node => node.remove());
        }

        return clone;
    }

    function extractText(element) {
        if (!element) {
            return '';
        }

        const cleaned = cloneAndClean(element);
        return cleanText(cleaned.innerText || cleaned.textContent || '');
    }

    function scoreText(text, selectorIndex) {
        if (!text) {
            return -1;
        }

        let score = Math.min(text.length, 20000);
        score += Math.max(0, 1000 - selectorIndex * 80);

        const lineCount = text.split('\n').filter(Boolean).length;
        score += Math.min(lineCount * 8, 800);

        return score;
    }

    let bestText = '';
    let bestScore = -1;

    for (let i = 0; i < candidateSelectors.length; i += 1) {
        const selector = candidateSelectors[i];
        const candidates = Array.from(document.querySelectorAll(selector));

        for (const element of candidates) {
            const text = extractText(element);
            const score = scoreText(text, i);

            if (score > bestScore) {
                bestText = text;
                bestScore = score;
            }
        }
    }

    if (bestText) {
        return bestText;
    }

    if (document.body) {
        const bodyClone = cloneAndClean(document.body);
        return cleanText(bodyClone.innerText || bodyClone.textContent || '');
    }

    return '';
}"""


def _normalize_rendered_text(text: str) -> str:
    lines = [
        line.rstrip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    result = "\n".join(lines).strip()

    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")

    return result


async def _extract_rendered_content(page: Page) -> str:
    content = await page.evaluate(_RENDERED_CONTENT_SCRIPT)
    if not isinstance(content, str):
        return ""

    return _normalize_rendered_text(content)


async def handle_snapshot(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    processor: ContentProcessor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    try:
        mode = action.get("mode")
        goal = action.get("goal")

        if mode is None:
            mode = "focused" if goal else "full"

        if mode not in ("full", "focused"):
            return build_error_response(
                session_state=session_state(session_manager),
                page_state=await get_page_state(page),
                error=make_schema_error("snapshot mode must be 'full' or 'focused'."),
            )

        limit = action.get("limit")
        snapshot_payload = await snapshot_manager.take(
            page,
            mode=mode,
            goal=goal,
            limit=limit,
        )
    except Exception as error:
        log_fail("浏览器快照", str(error))
        page_state = await get_page_state(page)
        return build_error_response(
            session_state=session_state(session_manager),
            page_state=page_state,
            error=make_action_failed_error(
                action_type="snapshot",
                message="Snapshot failed.",
                diagnostic_code="SNAPSHOT_FAILED",
            ),
        )

    page_state = await get_page_state(page)
    return build_success_response(
        session_state=session_state(session_manager, reused=True),
        page_state=page_state,
        action_result=ActionResult(
            type="snapshot",
            status="completed",
            detail={
                "snapshot_id": snapshot_payload.snapshot_id,
                "mode": snapshot_payload.mode,
                "goal": snapshot_payload.goal,
            },
        ),
        snapshot=snapshot_payload,
    )


async def handle_screenshot(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    processor: ContentProcessor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    try:
        screenshot_bytes = await page.screenshot(
            type="jpeg",
            quality=_SCREENSHOT_JPEG_QUALITY,
            scale="css",
            full_page=False,
        )
        screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
    except Exception as error:
        log_fail("浏览器截图", str(error))
        page_state = await get_page_state(page)
        return build_error_response(
            session_state=session_state(session_manager),
            page_state=page_state,
            error=make_action_failed_error(
                action_type="screenshot",
                message="Screenshot failed.",
                diagnostic_code="SCREENSHOT_FAILED",
            ),
        )

    page_state = await get_page_state(page)
    return build_success_response(
        session_state=session_state(session_manager, reused=True),
        page_state=page_state,
        action_result=ActionResult(
            type="screenshot",
            status="completed",
            detail={},
        ),
        screenshot=screenshot_b64,
    )


async def handle_get_content(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    processor: ContentProcessor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    try:
        content = await _extract_rendered_content(page)
    except Exception as error:
        log_fail("渲染页面内容提取", str(error))
        content = ""

    if not content:
        try:
            html = await page.content()
            cleaned = processor.process(html)
            content = cleaned if cleaned else ""
        except Exception as error:
            log_fail("页面内容备用提取", str(error))
            page_state = await get_page_state(page)
            return build_error_response(
                session_state=session_state(session_manager),
                page_state=page_state,
                error=make_action_failed_error(
                    action_type="get_content",
                    message="Get content failed.",
                    diagnostic_code="GET_CONTENT_FAILED",
                ),
            )

    page_state = await get_page_state(page)

    return build_success_response(
        session_state=session_state(session_manager, reused=True),
        page_state=page_state,
        action_result=ActionResult(
            type="get_content",
            status="completed",
            detail={
                "content": content,
                "content_length": len(content),
            },
        ),
    )
