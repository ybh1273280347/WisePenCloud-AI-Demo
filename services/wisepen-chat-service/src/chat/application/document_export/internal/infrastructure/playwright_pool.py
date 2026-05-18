import asyncio
from pathlib import Path
from typing import Optional, Tuple

from common.logger import log_error, log_event
from playwright.async_api import Browser, Playwright, async_playwright

from ...errors import ExportRenderError


class BrowserPoolUnavailableError(Exception):
    def __init__(
        self,
        message: str,
        *,
        diagnostic_code: str = "BROWSER_POOL_UNAVAILABLE",
    ):
        self.message = message
        self.diagnostic_code = diagnostic_code
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
        try:
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
        except Exception as e:
            diagnostic_code = self._classify_browser_error(e)
            log_error(
                "DocumentExport BrowserPool 启动失败",
                e,
                diagnostic_code=diagnostic_code,
                disable_sandbox=self.disable_sandbox,
            )
            await self._stop_locked()
            raise BrowserPoolUnavailableError(
                self._format_browser_error(e, diagnostic_code=diagnostic_code),
                diagnostic_code=diagnostic_code,
            ) from e

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
                    diagnostic_code = self._classify_browser_error(e)
                    log_error(
                        "DocumentExport PDF 渲染失败",
                        e,
                        diagnostic_code=diagnostic_code,
                        attempt=attempt + 1,
                    )
                    raise ExportRenderError(
                        self._format_browser_error(
                            e,
                            diagnostic_code=diagnostic_code,
                            prefix="Failed to render PDF",
                        )
                    ) from e

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

    @staticmethod
    def _classify_browser_error(e: BaseException) -> str:
        message = str(e).lower()
        if (
            "executable doesn't exist" in message
            or "executable not found" in message
            or "please run the following command" in message
            or "playwright install" in message
        ):
            return "PLAYWRIGHT_BROWSER_MISSING"
        if (
            "running as root without --no-sandbox" in message
            or "no usable sandbox" in message
            or "setuid sandbox" in message
        ):
            return "PLAYWRIGHT_SANDBOX_UNAVAILABLE"
        if "target page, context or browser has been closed" in message:
            return "PLAYWRIGHT_BROWSER_CLOSED"
        if "timeout" in message:
            return "PLAYWRIGHT_TIMEOUT"
        return "PLAYWRIGHT_RENDER_FAILED"

    @staticmethod
    def _format_browser_error(
        e: BaseException,
        *,
        diagnostic_code: str,
        prefix: str = "PDF browser unavailable",
    ) -> str:
        detail = str(e).strip().splitlines()[0] if str(e).strip() else repr(e)
        if diagnostic_code == "PLAYWRIGHT_BROWSER_MISSING":
            return (
                f"{prefix}: Playwright Chromium is not installed. "
                "Run `python -m playwright install chromium` in the runtime image."
            )
        if diagnostic_code == "PLAYWRIGHT_SANDBOX_UNAVAILABLE":
            return (
                f"{prefix}: Chromium sandbox is unavailable. "
                "Set DOCUMENT_EXPORT_PLAYWRIGHT_DISABLE_SANDBOX=true or run with a supported sandbox."
            )
        if diagnostic_code == "PLAYWRIGHT_TIMEOUT":
            return f"{prefix}: browser operation timed out."
        return f"{prefix}: {detail}"
