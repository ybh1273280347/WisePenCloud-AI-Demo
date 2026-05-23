import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import steel
from chat.application.tools.services.web_fetch.base import BaseFetcher
from chat.application.tools.services.web_fetch.config import (
    STEEL_CONCURRENCY,
    STEEL_DELAY_MS,
    STEEL_MAX_RETRIES,
    WEB_FETCH_BROWSER_TIMEOUT,
)
from chat.application.tools.services.web_fetch.content_processor import ContentProcessor
from chat.application.tools.services.web_fetch.models import FetchedPage
from chat.application.tools.services.web_fetch.utils.page_metadata import (
    extract_markdown_title,
    extract_page_domain,
)
from chat.core.config.app_settings import settings
from common.logger import log_event, log_fail
from steel import AsyncSteel

for _LOGGER_NAME in ("courlan", "htmldate", "trafilatura"):
    logging.getLogger(_LOGGER_NAME).setLevel(logging.ERROR)


@dataclass(frozen=True, slots=True)
class SteelFetcherConfig:
    base_url: str = settings.STEEL_BASE_URL
    timeout: float = WEB_FETCH_BROWSER_TIMEOUT
    max_retries: int = STEEL_MAX_RETRIES
    use_proxy: bool = settings.STEEL_USE_PROXY
    delay_ms: float = STEEL_DELAY_MS
    region: Optional[str] = settings.STEEL_REGION
    strip_output: bool = True
    formats: Tuple[str, ...] = ("markdown", "cleaned_html", "html")


def _log_steel_fetch_fail(message, *, url: str, **kwargs) -> None:
    log_fail("SteelFetcher", f"reason={message}", url=url, **kwargs)


class SteelFetcher(BaseFetcher):
    """通过 Steel API 的 scrape 接口获取页面 Markdown 内容"""

    name = "steel"

    def __init__(
        self,
        config: SteelFetcherConfig,
        *,
        concurrency: int = STEEL_CONCURRENCY,
        processor: Optional[ContentProcessor] = None,
    ):
        self._config = config
        self._processor = processor or ContentProcessor()
        self._semaphore = asyncio.Semaphore(concurrency)
        self._client = AsyncSteel(
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )
        log_event(
            "SteelFetcher 初始化",
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=config.max_retries,
            use_proxy=config.use_proxy,
            delay_ms=config.delay_ms,
            formats=list(config.formats),
            region=config.region,
            concurrency=concurrency,
        )

    async def close(self) -> None:
        if self._client.is_closed():
            return

        await self._client.close()
        log_event("SteelFetcher 关闭")

    async def fetch(self, url: str) -> Optional[FetchedPage]:
        started_at = time.monotonic()

        async with self._semaphore:
            return await self._fetch_inner(url, started_at)

    async def _fetch_inner(self, url: str, started_at: float) -> Optional[FetchedPage]:
        try:
            kwargs = {
                "url": url,
                "format": list(self._config.formats),
                "use_proxy": self._config.use_proxy,
                "timeout": self._config.timeout,
            }

            if self._config.delay_ms > 0:
                kwargs["delay"] = self._config.delay_ms

            if self._config.region is not None:
                kwargs["region"] = self._config.region

            log_event(
                "SteelFetcher 开始",
                url=url,
                formats=list(self._config.formats),
                use_proxy=self._config.use_proxy,
                delay_ms=self._config.delay_ms,
                region=self._config.region,
                timeout=self._config.timeout,
            )

            response = await self._client.scrape(**kwargs)
            elapsed = time.monotonic() - started_at

            metadata = response.metadata
            status_code = metadata.status_code if metadata else None

            if status_code is not None and status_code >= 400:
                _log_steel_fetch_fail(
                    f"目标页面 HTTP {status_code}",
                    url=url,
                    elapsed_seconds=f"{elapsed:.2f}",
                )
                return None

            content = response.content
            if content is None:
                _log_steel_fetch_fail(
                    "响应中无 content",
                    url=url,
                    status_code=status_code,
                    elapsed_seconds=f"{elapsed:.2f}",
                )
                return None

            result = await self._extract_content(
                content,
                url=url,
                status_code=status_code,
            )
            if result is None:
                return None

            final_url = _metadata_string(metadata, "final_url", "url") or url
            title = _metadata_string(metadata, "title") or extract_markdown_title(
                result
            )

            log_event(
                "SteelFetcher 完成",
                url=url,
                status_code=status_code,
                length=len(result),
                has_markdown=bool(content.markdown),
                has_cleaned_html=bool(content.cleaned_html),
                has_html=bool(content.html),
                elapsed_seconds=f"{elapsed:.2f}",
            )
            return FetchedPage(
                markdown=result,
                links=[],
                title=title,
                final_url=final_url,
                domain=extract_page_domain(final_url),
                status_code=status_code,
            )

        except steel.RateLimitError as e:
            elapsed = time.monotonic() - started_at
            _log_steel_fetch_fail(
                f"触发限流: {e}", url=url, elapsed_seconds=f"{elapsed:.2f}"
            )
            return None

        except steel.APITimeoutError as e:
            elapsed = time.monotonic() - started_at
            _log_steel_fetch_fail(
                f"请求超时: {e}; elapsed={elapsed:.2f}s",
                url=url,
                elapsed_seconds=f"{elapsed:.2f}",
            )
            return None

        except steel.APIConnectionError as e:
            elapsed = time.monotonic() - started_at
            _log_steel_fetch_fail(
                f"连接失败: {e}", url=url, elapsed_seconds=f"{elapsed:.2f}"
            )
            return None

        except steel.APIStatusError as e:
            elapsed = time.monotonic() - started_at
            body = getattr(e.response, "text", "")
            _log_steel_fetch_fail(
                f"Steel API HTTP {e.status_code}: {body[:500]}",
                url=url,
                elapsed_seconds=f"{elapsed:.2f}",
            )
            return None

        except steel.APIError as e:
            elapsed = time.monotonic() - started_at
            _log_steel_fetch_fail(
                f"Steel API 错误: {e}", url=url, elapsed_seconds=f"{elapsed:.2f}"
            )
            return None

        except Exception as e:
            elapsed = time.monotonic() - started_at
            _log_steel_fetch_fail(
                f"{e.__class__.__name__}: {e}",
                url=url,
                elapsed_seconds=f"{elapsed:.2f}",
            )
            return None

    async def _extract_content(self, content, *, url: str, status_code) -> Optional[str]:
        markdown = content.markdown
        if markdown is not None:
            if self._config.strip_output:
                markdown = markdown.strip()
            if markdown:
                return markdown

        for field_name in ("cleaned_html", "html"):
            html = getattr(content, field_name, None)
            if not isinstance(html, str):
                continue
            if self._config.strip_output:
                html = html.strip()
            if not html:
                continue

            processed = await asyncio.to_thread(self._processor.process, html)
            if processed:
                log_event(
                    "SteelFetcher HTML 转 Markdown 成功",
                    url=url,
                    status_code=status_code,
                    html_field=field_name,
                    html_length=len(html),
                    markdown_length=len(processed),
                )
                return processed

        _log_steel_fetch_fail(
            "markdown/html 均为空或处理失败，降级到本地浏览器",
            url=url,
            status_code=status_code,
            has_markdown=bool(content.markdown),
            has_cleaned_html=bool(content.cleaned_html),
            has_html=bool(content.html),
        )
        return None


def _metadata_string(metadata, *names: str) -> str:
    if metadata is None:
        return ""

    for name in names:
        value = getattr(metadata, name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""
