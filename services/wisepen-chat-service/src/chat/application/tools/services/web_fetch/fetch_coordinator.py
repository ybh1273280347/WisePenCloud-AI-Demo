import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional
from urllib.parse import urlparse

from cachetools import TTLCache
from chat.application.tools.common.security.network import (
    DOCUMENT_EXTENSIONS,
    UrlSecurityError,
    validate_public_http_url,
)
from chat.application.tools.services.web_fetch.base import BaseFetcher
from chat.application.tools.services.web_fetch.content_processor import ContentProcessor
from chat.application.tools.services.web_fetch.errors import UnsupportedMediaError
from chat.application.tools.services.web_fetch.models import FetchedDocument
from common.logger import log_event, log_fail, log_ok

_BATCH_CONCURRENCY = 5
_MAX_FAILURE_REASONS_IN_ERROR = 5


class FetchContentKind(str, Enum):
    RAW = "raw"
    MARKDOWN = "markdown"


class FetchFailureReason(str, Enum):
    EXCEPTION = "exception"
    EMPTY_CONTENT = "empty_content"
    SHORT_CONTENT = "short_content"
    PROCESSING_FAILED = "processing_failed"


@dataclass(frozen=True, slots=True)
class FetchResultItem:
    url: str
    success: bool
    content: Optional[str] = None
    document: Optional[FetchedDocument] = None
    error: Optional[str] = None
    fetcher: Optional[str] = None


@dataclass(frozen=True, slots=True)
class FetchChainStep:
    fetcher: BaseFetcher
    content_kind: FetchContentKind
    min_content_length: int
    skip_document_url: bool = False

    @property
    def fetcher_name(self) -> str:
        return self.fetcher.__class__.__name__

    def should_skip(self, *, is_document_url: bool) -> bool:
        return is_document_url and self.skip_document_url


@dataclass(frozen=True, slots=True)
class FetchAttemptFailure:
    fetcher_name: str
    reason: FetchFailureReason
    message: str
    error_type: Optional[str] = None
    length: Optional[int] = None
    threshold: Optional[int] = None

    def format_message(self) -> str:
        return f"{self.fetcher_name}: {self.message}"


class FetchCoordinator:
    """web_fetch 调度器：StaticFetcher -> SteelFetcher -> LocalScriptFetcher。"""

    def __init__(
        self,
        fetchers: List[BaseFetcher],
        processor: ContentProcessor,
        min_content_length: int,
        last_resort_min_length: int,
        cache_ttl_seconds: int,
        cache_max_items: int,
    ):
        self._cache: TTLCache[str, str] = TTLCache(
            maxsize=cache_max_items,
            ttl=cache_ttl_seconds,
        )
        self._processor = processor
        self._chain = _build_chain(
            fetchers=fetchers,
            min_content_length=min_content_length,
            last_resort_min_length=last_resort_min_length,
        )

    async def fetch(self, url: str) -> Optional[str | FetchedDocument]:
        """获取单个 URL，返回 Markdown 文本或文档交接对象。"""
        item = await self.fetch_one(url, raise_security_error=True)
        if item.document is not None:
            return item.document
        return item.content if item.success else None

    async def fetch_one(
        self,
        url: str,
        *,
        raise_security_error: bool = False,
    ) -> FetchResultItem:
        """获取单个 URL，并保留最终 fetcher / 失败原因供日志使用。"""
        try:
            url = await asyncio.to_thread(validate_public_http_url, url)
        except UrlSecurityError as e:
            if raise_security_error:
                raise
            return FetchResultItem(
                url=url,
                success=False,
                error=f"URL rejected by security policy: {e}",
            )

        cached = self._get_cached(url)
        if cached is not None:
            return FetchResultItem(
                url=url,
                success=True,
                content=cached,
                fetcher="cache",
            )

        is_document_url = _is_document_url(url)
        failures: List[FetchAttemptFailure] = []

        for step in self._chain:
            if step.should_skip(is_document_url=is_document_url):
                continue

            try:
                log_event("web_fetch fetcher 开始", url=url, fetcher=step.fetcher_name)
                content = await step.fetcher.fetch(url)
            except UnsupportedMediaError as e:
                return FetchResultItem(
                    url=url,
                    success=False,
                    error=str(e),
                    fetcher=step.fetcher_name,
                )
            except UrlSecurityError as e:
                if raise_security_error:
                    raise
                return FetchResultItem(
                    url=url,
                    success=False,
                    error=f"URL rejected by security policy: {e}",
                )
            except Exception as e:
                failure = FetchAttemptFailure(
                    fetcher_name=step.fetcher_name,
                    reason=FetchFailureReason.EXCEPTION,
                    message=f"异常={e.__class__.__name__}",
                    error_type=e.__class__.__name__,
                )
                failures.append(failure)
                log_fail(
                    "web_fetch fetcher",
                    f"降级: {failure.reason.value}",
                    url=url,
                    fetcher=step.fetcher_name,
                    error=failure.error_type,
                )
                continue

            if content is None or content == "":
                failure = FetchAttemptFailure(
                    fetcher_name=step.fetcher_name,
                    reason=FetchFailureReason.EMPTY_CONTENT,
                    message="内容为空",
                )
                failures.append(failure)
                log_fail(
                    "web_fetch fetcher",
                    f"降级: {failure.reason.value}",
                    url=url,
                    fetcher=step.fetcher_name,
                )
                continue

            if isinstance(content, FetchedDocument):
                return FetchResultItem(
                    url=url,
                    success=True,
                    document=content,
                    fetcher=step.fetcher_name,
                )

            if step.content_kind == FetchContentKind.MARKDOWN:
                result = content.strip()

                if len(result) < step.min_content_length:
                    failure = FetchAttemptFailure(
                        fetcher_name=step.fetcher_name,
                        reason=FetchFailureReason.SHORT_CONTENT,
                        message=f"内容过短({len(result)}字符，阈值{step.min_content_length})",
                        length=len(result),
                        threshold=step.min_content_length,
                    )
                    failures.append(failure)
                    log_fail(
                        "web_fetch fetcher",
                        f"降级: {failure.reason.value}",
                        url=url,
                        fetcher=step.fetcher_name,
                        length=failure.length,
                        threshold=failure.threshold,
                    )
                    continue
            else:
                result = await asyncio.to_thread(self._processor.process, content)

                if result is None:
                    failure = FetchAttemptFailure(
                        fetcher_name=step.fetcher_name,
                        reason=FetchFailureReason.PROCESSING_FAILED,
                        message="内容处理失败",
                    )
                    failures.append(failure)
                    log_fail(
                        "web_fetch fetcher",
                        f"降级: {failure.reason.value}",
                        url=url,
                        fetcher=step.fetcher_name,
                    )
                    continue

            self._set_cached(url, result)
            return FetchResultItem(
                url=url,
                success=True,
                content=result,
                fetcher=step.fetcher_name,
            )

        return FetchResultItem(
            url=url,
            success=False,
            error=_format_exhausted_error(failures),
            fetcher=None,
        )

    def _get_cached(self, url: str) -> Optional[str]:
        self._cache.expire()
        return self._cache.get(url)

    def _set_cached(self, url: str, markdown: str) -> None:
        self._cache.expire()
        self._cache[url] = markdown

    async def fetch_many(self, urls: List[str]) -> List[FetchResultItem]:
        """并发获取多个 URL，并保持输入顺序。"""
        log_event("web_fetch 批量开始", count=len(urls), urls=urls)
        t0 = asyncio.get_event_loop().time()

        semaphore = asyncio.Semaphore(_BATCH_CONCURRENCY)
        total = len(urls)
        done_count = 0
        ok_count = 0
        fail_count = 0
        slots: List[Optional[FetchResultItem]] = [None] * total

        async def _fetch_one(index: int, url: str) -> None:
            nonlocal done_count, ok_count, fail_count
            async with semaphore:
                try:
                    result = await self.fetch_one(url)
                except Exception as e:
                    result = FetchResultItem(
                        url=url,
                        success=False,
                        error=f"未预期异常: {e.__class__.__name__}",
                    )

            slots[index] = result
            done_count += 1
            if result.success:
                ok_count += 1
            else:
                fail_count += 1

            log_event(
                "web_fetch 单个完成",
                进度=f"{done_count}/{total}",
                已完成=ok_count,
                未完成=fail_count,
                url=result.url,
                success=result.success,
                fetcher=result.fetcher,
                error=result.error if not result.success else None,
            )

        tasks = [_fetch_one(i, url) for i, url in enumerate(urls)]
        await asyncio.gather(*tasks)

        results: List[FetchResultItem] = list(slots)  # type: ignore[arg-type]

        elapsed_ms = int((asyncio.get_event_loop().time() - t0) * 1000)

        fetchers: Dict[str, int] = {}
        for result in results:
            if not result.success:
                continue

            fetcher = result.fetcher or "unknown"
            fetchers[fetcher] = fetchers.get(fetcher, 0) + 1

        failed_urls = [result.url for result in results if not result.success][:5]
        failures = [
            {
                "URL": result.url,
                "原因": _format_log_error(result.error),
            }
            for result in results
            if not result.success
        ][:5]

        fields = {
            "总数": total,
            "已完成": ok_count,
            "未完成": fail_count,
            "fetcher分布": fetchers,
            "未完成_URLs": failed_urls,
            "未完成_URLs_省略": max(0, fail_count - len(failed_urls)),
            "未完成原因": failures,
            "未完成原因_省略": max(0, fail_count - len(failures)),
            "耗时_ms": elapsed_ms,
        }

        if fail_count == 0:
            log_ok("web_fetch", **fields)
        elif ok_count == 0:
            log_fail("web_fetch", "所有 URL 未完成", **fields)
        else:
            log_fail("web_fetch 部分", "部分 URL 未完成", **fields)

        return results


def _build_chain(
    *,
    fetchers: List[BaseFetcher],
    min_content_length: int,
    last_resort_min_length: int,
) -> List[FetchChainStep]:
    chain: List[FetchChainStep] = []
    last_index = len(fetchers) - 1

    for index, fetcher in enumerate(fetchers):
        content_kind = (
            FetchContentKind.RAW
            if fetcher.name == "static"
            else FetchContentKind.MARKDOWN
        )

        chain.append(
            FetchChainStep(
                fetcher=fetcher,
                content_kind=content_kind,
                min_content_length=(
                    last_resort_min_length
                    if index == last_index
                    else min_content_length
                ),
                skip_document_url=content_kind == FetchContentKind.MARKDOWN,
            )
        )

    return chain


def _is_document_url(url: str) -> bool:
    url_path = urlparse(url).path.lower()
    return any(url_path.endswith(extension) for extension in DOCUMENT_EXTENSIONS)


def _format_exhausted_error(failures: List[FetchAttemptFailure]) -> str:
    if not failures:
        return "所有 fetcher 均失败"

    reasons = " | ".join(
        failure.format_message()
        for failure in failures[-_MAX_FAILURE_REASONS_IN_ERROR:]
    )
    return f"所有 fetcher 均失败: {reasons}"


def _format_log_error(error: Optional[str]) -> str:
    if not error:
        return ""

    if error.startswith("URL rejected by security policy: "):
        return "URL 被安全策略拒绝: " + error.removeprefix(
            "URL rejected by security policy: "
        )

    if error.startswith("All fetch methods exhausted"):
        return "所有 fetcher 均未完成" + error.removeprefix(
            "All fetch methods exhausted"
        )

    if error.startswith("Unexpected error: "):
        return "未预期异常: " + error.removeprefix("Unexpected error: ")

    return error
