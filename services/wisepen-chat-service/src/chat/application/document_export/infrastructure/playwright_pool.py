import asyncio
from pathlib import Path
from typing import Optional, Tuple

from common.logger import log_event
from playwright.async_api import Browser, Playwright, async_playwright

from ..errors import ExportRenderError


class BrowserPoolUnavailableError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class PlaywrightBrowserPool:
    def __init__(self, *, max_contexts: int = 8, disable_sandbox: bool = False):
        self.max_contexts = max_contexts
        self.disable_sandbox = disable_sandbox
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._browser_generation: int = 0
        self._semaphore = asyncio.Semaphore(self.max_contexts)
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return
            await self._stop_locked()
            await self._start_locked()

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _start_locked(self) -> None:
        self._playwright = await async_playwright().start()
        args = [
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ]
        if self.disable_sandbox:
            args.append("--no-sandbox")

        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=args,
        )
        self._browser_generation += 1
        log_event(
            "DocumentExport BrowserPool 启动",
            generation=self._browser_generation,
            max_contexts=self.max_contexts,
        )

    async def _stop_locked(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        log_event("DocumentExport BrowserPool 停止")

    async def _get_browser_snapshot(self) -> Tuple[Browser, int]:
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                await self._stop_locked()
                await self._start_locked()

            if self._browser is None:
                raise BrowserPoolUnavailableError(
                    "Playwright browser is not available."
                )

            return self._browser, self._browser_generation

    async def _restart_browser_if_current(self, failed_generation: int) -> None:
        async with self._lock:
            if (
                self._browser is not None
                and self._browser.is_connected()
                and self._browser_generation != failed_generation
            ):
                return

            await self._stop_locked()
            await self._start_locked()

    async def render_pdf_from_html(
        self,
        *,
        html: str,
        output_path: Path,
        timeout_seconds: float = 60.0,
    ) -> None:
        last_error: Optional[BaseException] = None

        for attempt in range(2):
            browser, generation = await self._get_browser_snapshot()

            try:
                async with self._semaphore:
                    await asyncio.wait_for(
                        self._render_with_context(
                            browser=browser,
                            html=html,
                            output_path=output_path,
                            timeout_seconds=timeout_seconds,
                        ),
                        timeout=timeout_seconds,
                    )
                return

            except asyncio.TimeoutError as e:
                raise ExportRenderError("PDF rendering timed out.") from e

            except (BrowserPoolUnavailableError, ExportRenderError):
                raise

            except Exception as e:
                last_error = e

                if attempt >= 1:
                    raise

                await self._restart_browser_if_current(generation)

        if last_error is not None:
            raise last_error

    async def _render_with_context(
        self,
        *,
        browser: Browser,
        html: str,
        output_path: Path,
        timeout_seconds: float,
    ) -> None:
        context = await browser.new_context(java_script_enabled=False)
        page = None

        async def block_external_requests(route):
            request_url = route.request.url
            if request_url.startswith("data:") or request_url == "about:blank":
                await route.continue_()
                return
            await route.abort()

        try:
            await context.route("**/*", block_external_requests)
            page = await context.new_page()
            await page.set_content(
                html, wait_until="load", timeout=timeout_seconds * 1000
            )
            await page.pdf(
                path=str(output_path),
                print_background=True,
                prefer_css_page_size=True,
            )
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
            try:
                await context.close()
            except Exception:
                pass
