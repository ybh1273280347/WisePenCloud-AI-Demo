import asyncio
from pathlib import Path

from chat.application.document_export.errors import ExportRenderError
from chat.application.document_export.internal.infrastructure.playwright_pool import (
    BrowserPoolUnavailableError,
    PlaywrightBrowserPool,
)
from chat.application.document_export.internal.renderers.html_renderer import HtmlRenderer
from chat.application.document_export.internal.renderers.pdf_renderer import PdfRenderer
from chat.application.document_export.models import ExportOptions, ExportRequest


def test_classifies_missing_playwright_browser() -> None:
    error = RuntimeError(
        "Executable doesn't exist at /ms-playwright/chromium/chrome-linux/chrome\n"
        "Please run the following command to download new browsers:\n"
        "python -m playwright install"
    )

    assert (
        PlaywrightBrowserPool._classify_browser_error(error)
        == "PLAYWRIGHT_BROWSER_MISSING"
    )
    assert "python -m playwright install chromium" in (
        PlaywrightBrowserPool._format_browser_error(
            error,
            diagnostic_code="PLAYWRIGHT_BROWSER_MISSING",
        )
    )


def test_classifies_chromium_sandbox_error() -> None:
    error = RuntimeError("Chromium sandboxing failed: No usable sandbox!")

    assert (
        PlaywrightBrowserPool._classify_browser_error(error)
        == "PLAYWRIGHT_SANDBOX_UNAVAILABLE"
    )
    assert "DOCUMENT_EXPORT_PLAYWRIGHT_DISABLE_SANDBOX=true" in (
        PlaywrightBrowserPool._format_browser_error(
            error,
            diagnostic_code="PLAYWRIGHT_SANDBOX_UNAVAILABLE",
        )
    )


def test_pdf_renderer_preserves_browser_pool_diagnostic(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        renderer = PdfRenderer(
            html_renderer=HtmlRenderer(),
            browser_pool=MissingBrowserPool(),
        )
        request = ExportRequest(
            user_id="user",
            session_id="session",
            markdown="# Title",
            target_format="pdf",
            output_path=tmp_path / "out.pdf",
            file_name="out.pdf",
            options=ExportOptions(),
        )

        try:
            await renderer.render(request)
        except ExportRenderError as e:
            assert "Playwright Chromium is not installed" in str(e)
        else:
            raise AssertionError("Expected ExportRenderError")

    class MissingBrowserPool:
        async def render_pdf_from_html(self, **kwargs) -> None:
            raise BrowserPoolUnavailableError(
                "PDF browser unavailable: Playwright Chromium is not installed.",
                diagnostic_code="PLAYWRIGHT_BROWSER_MISSING",
            )

    asyncio.run(run())
