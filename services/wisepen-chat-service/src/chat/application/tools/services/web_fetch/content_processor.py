import logging
from typing import Optional

import trafilatura
from chat.application.tools.common.security.web_access import (
    PageBlockDetection,
    detect_page_block,
    should_reject_page_block,
)
from chat.application.tools.services.web_fetch.utils.text import normalize_text
from common.logger import log_event, log_fail

_HTML_DETECTION_SCAN_CHARS = 1024

for _LOGGER_NAME in ("courlan", "htmldate", "trafilatura"):
    logging.getLogger(_LOGGER_NAME).setLevel(logging.ERROR)


def _looks_like_html(text: str) -> bool:
    lower_head = text[:_HTML_DETECTION_SCAN_CHARS].lower()
    return (
        "<html" in lower_head or "<!doctype html" in lower_head or "<body" in lower_head
    )


def _extract_markdown_from_html(
    html: str,
    *,
    min_content_length: int,
) -> Optional[str]:
    for extractor_name, extractor in (
        ("trafilatura", _extract_with_trafilatura),
        ("baseline", _extract_with_baseline),
    ):
        extracted = extractor(html)
        if not extracted:
            continue

        normalized = normalize_text(extracted)
        log_event("HTML 提取成功", extractor=extractor_name, length=len(normalized))

        if len(normalized) < min_content_length:
            continue

        if _should_reject_extracted_text(
            normalized,
            stage=f"HTML 清洗后检测:{extractor_name}",
            html=html,
        ):
            continue

        return normalized

    return None


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
    except Exception as e:
        log_fail("trafilatura 提取", repr(e))
        return None


def _extract_with_baseline(html: str) -> Optional[str]:
    try:
        _, text, _ = trafilatura.baseline(html)
    except Exception as e:
        log_fail("trafilatura baseline 提取", repr(e))
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


def _should_reject_raw_html(html: str) -> bool:
    detection = detect_page_block(html, html=html)

    if not should_reject_page_block(detection, stage="raw_html"):
        return False

    _log_page_block("HTML 原始页检测", detection)
    return True


def _should_reject_extracted_text(
    text: str,
    *,
    stage: str,
    html: str = "",
) -> bool:
    detection = detect_page_block(text, html=html)

    if not should_reject_page_block(detection, stage="extracted_text"):
        return False

    _log_page_block(stage, detection)
    return True


class ContentProcessor:
    """将 web_fetch 获取到的文本内容转换为 Markdown。"""

    def __init__(self, min_content_length: int = 400):
        self._min_content_length = min_content_length

    def process(self, content: str) -> Optional[str]:
        return self._process_text(content)

    def _process_text(self, content: str) -> Optional[str]:
        stripped = content.strip()
        if not stripped:
            return None

        if _looks_like_html(stripped):
            if _should_reject_raw_html(stripped):
                return None

            return self._process_html(stripped)

        return self._process_plain_text(stripped)

    def _process_html(self, html: str) -> Optional[str]:
        result = _extract_markdown_from_html(
            html,
            min_content_length=self._min_content_length,
        )
        if result is None:
            log_fail("HTML 清洗", "no valid body")
            return None

        return result

    def _process_plain_text(self, text: str) -> Optional[str]:
        normalized = normalize_text(text)

        if len(normalized) < self._min_content_length:
            log_fail(
                "文本清理",
                f"纯文本过短: {len(normalized)} < {self._min_content_length}",
                length=len(normalized),
                threshold=self._min_content_length,
            )
            return None

        detection = detect_page_block(normalized)
        if should_reject_page_block(detection, stage="plain_text"):
            _log_page_block("纯文本检测", detection)
            return None

        return normalized
