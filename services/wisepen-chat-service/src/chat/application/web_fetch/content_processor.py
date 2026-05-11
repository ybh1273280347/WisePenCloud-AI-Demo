from typing import Optional

import trafilatura

from chat.application.web_fetch.utils import (
    PageBlockDetection,
    detect_page_block,
    normalize_text,
    should_degrade_detection,
)
from common.logger import log_event, log_fail, log_ok

_HTML_DETECTION_SCAN_CHARS = 1024


def _looks_like_html(text: str) -> bool:
    lower_head = text[:_HTML_DETECTION_SCAN_CHARS].lower()
    return "<html" in lower_head or "<!doctype html" in lower_head or "<body" in lower_head


def _extract_markdown_from_html(
    html: str,
    *,
    min_content_length: int,
) -> Optional[str]:
    best_result: Optional[str] = None

    for extractor in (
        _extract_with_trafilatura,
        _extract_with_baseline,
        _extract_with_html2txt,
    ):
        extracted = extractor(html)
        if not extracted:
            continue

        normalized = normalize_text(extracted)

        if best_result is None or len(normalized) > len(best_result):
            best_result = normalized

        if len(normalized) >= min_content_length:
            return normalized

    return best_result


def _extract_with_trafilatura(html: str) -> Optional[str]:
    try:
        return trafilatura.extract(
            html,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            include_links=True,
            favor_precision=False,
            favor_recall=True,
        )
    except Exception:
        log_fail("trafilatura 提取失败")
        return None


def _extract_with_baseline(html: str) -> Optional[str]:
    try:
        _, text, _ = trafilatura.baseline(html)
    except Exception:
        log_fail("trafilatura baseline 提取失败")
        return None

    return text or None


def _extract_with_html2txt(html: str) -> Optional[str]:
    try:
        text = trafilatura.html2txt(html)
    except Exception:
        log_fail("trafilatura html2txt 提取失败")
        return None

    return text or None


def _log_page_block(stage: str, detection: PageBlockDetection) -> None:
    log_event(
        stage,
        reason="疑似反爬/登录/JS 空壳页面",
        kind=detection.kind,
        confidence=detection.confidence,
        score=detection.score,
        signals=",".join(detection.signals[:5]),
    )


def _should_reject_page(
    text: str,
    *,
    stage: str,
    html: str = "",
) -> bool:
    detection = detect_page_block(text, html=html)

    if not should_degrade_detection(detection):
        return False

    _log_page_block(stage, detection)
    return True


class ContentProcessor:
    """将 Web Fetch 抓取到的文本内容转换为 Markdown。"""

    def __init__(self, min_content_length: int = 400):
        self._min_content_length = min_content_length

    def process(self, content: str | bytes) -> Optional[str]:
        if isinstance(content, bytes):
            log_fail("内容处理", "收到 bytes 内容，Web Fetch 不再解析文档")
            return None

        return self._process_text(content)

    def _process_text(self, content: str) -> Optional[str]:
        stripped = content.strip()
        if not stripped:
            return None

        if _looks_like_html(stripped):
            if _should_reject_page(stripped, stage="内容检测", html=stripped):
                return None

            return self._process_html(stripped)

        if _should_reject_page(stripped, stage="内容检测"):
            return None

        return self._process_plain_text(stripped)

    def _process_html(self, html: str) -> Optional[str]:
        result = _extract_markdown_from_html(
            html,
            min_content_length=self._min_content_length,
        )
        if result is None:
            return None

        if _should_reject_page(result, stage="HTML 清洗", html=html):
            return None

        if len(result) < self._min_content_length:
            log_event(
                "HTML 清洗：清洗后文本过短，触发降级",
                length=len(result),
                threshold=self._min_content_length,
            )
            return None

        log_ok("HTML 清洗", length=len(result))
        return result

    def _process_plain_text(self, text: str) -> Optional[str]:
        normalized = normalize_text(text)

        if len(normalized) < self._min_content_length:
            log_event(
                "纯文本检测：文本过短，触发降级",
                length=len(normalized),
                threshold=self._min_content_length,
            )
            return None

        if _should_reject_page(normalized, stage="纯文本检测"):
            return None

        return normalized
