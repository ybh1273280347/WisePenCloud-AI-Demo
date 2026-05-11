import asyncio
from typing import Any, List, Optional, Tuple

from cachetools import TTLCache

from chat.application.web_fetch.content_processor import ContentProcessor
from chat.application.web_fetch.fetcher import LocalScriptFetcher, StaticFetcher, SteelFetcher
from chat.application.web_fetch.models import FetchedDocument
from chat.application.web_fetch.utils import UrlSecurityError, validate_public_http_url
from common.logger import log_fail, log_ok


_CONTENT_TYPE_RAW = "raw"
_CONTENT_TYPE_MARKDOWN = "markdown"


class FetchCoordinator:
    """网页抓取调度器：StaticFetcher -> SteelFetcher -> LocalScriptFetcher 自动降级。"""

    def __init__(
        self,
        static_fetcher: StaticFetcher,
        steel_fetcher: SteelFetcher,
        local_script_fetcher: LocalScriptFetcher,
        processor: ContentProcessor,
        min_content_length: int,
        last_resort_min_length: int,
        cache_ttl_seconds: int,
        cache_max_items: int,
    ):
        self._min_content_length = min_content_length
        self._last_resort_min_length = last_resort_min_length
        self._cache: TTLCache[str, str] = TTLCache(
            maxsize=cache_max_items,
            ttl=cache_ttl_seconds,
        )

        self._static_fetcher = static_fetcher
        self._steel_fetcher = steel_fetcher
        self._local_script_fetcher = local_script_fetcher
        self._processor = processor

        self._chain: List[Tuple[Any, str]] = [
            (self._static_fetcher, _CONTENT_TYPE_RAW),
            (self._steel_fetcher, _CONTENT_TYPE_MARKDOWN),
            (self._local_script_fetcher, _CONTENT_TYPE_MARKDOWN),
        ]

    async def fetch(self, url: str) -> Optional[Any]:
        """从指定 URL 获取页面内容并转换为 Markdown；全部失败时返回 None。"""
        try:
            url = await asyncio.to_thread(validate_public_http_url, url)
        except UrlSecurityError:
            log_fail("URL 安全校验", "URL 被安全策略拒绝", url=url)
            raise

        cached = self._get_cached(url)
        if cached is not None:
            log_ok("网页抓取缓存命中", url=url, length=len(cached))
            return cached

        failure_reasons: List[str] = []

        for index, (fetcher, content_type) in enumerate(self._chain):
            fetcher_name = fetcher.__class__.__name__
            is_last = index == len(self._chain) - 1
            min_length = self._last_resort_min_length if is_last else self._min_content_length

            try:
                content = await fetcher.fetch(url)
            except UrlSecurityError:
                raise
            except Exception as e:
                failure_reasons.append(f"{fetcher_name}: 异常={e.__class__.__name__}")
                log_fail("网页抓取", e, url=url, fetcher=fetcher_name)
                continue

            if not content:
                failure_reasons.append(f"{fetcher_name}: 内容为空")
                log_fail("网页抓取", "抓取内容为空", url=url, fetcher=fetcher_name)
                continue

            if isinstance(content, FetchedDocument):
                log_ok(
                    "文档直链抓取",
                    url=content.url,
                    fetcher=fetcher_name,
                    filename=content.filename,
                    content_type=content.media_type,
                    size=len(content.content),
                )
                return content

            if content_type == _CONTENT_TYPE_MARKDOWN:
                result = content.strip()
                if len(result) < min_length:
                    failure_reasons.append(f"{fetcher_name}: 内容过短({len(result)}字符，阈值{min_length})")
                    log_event("网页抓取：内容过短，触发降级", url=url, fetcher=fetcher_name)
                    continue
            else:
                result = self._processor.process(content)
                if result is None:
                    failure_reasons.append(f"{fetcher_name}: 内容处理失败")
                    log_event("网页抓取：内容处理失败，触发降级", url=url, fetcher=fetcher_name)
                    continue

            log_ok("网页抓取", url=url, fetcher=fetcher_name, length=len(result))
            self._set_cached(url, result)
            return result

        log_fail(
            "网页抓取",
            "所有抓取器均失败",
            url=url,
            reasons=" | ".join(failure_reasons[-5:]),
        )
        return None

    def _get_cached(self, url: str) -> Optional[str]:
        self._cache.expire()
        return self._cache.get(url)

    def _set_cached(self, url: str, markdown: str) -> None:
        self._cache.expire()
        self._cache[url] = markdown