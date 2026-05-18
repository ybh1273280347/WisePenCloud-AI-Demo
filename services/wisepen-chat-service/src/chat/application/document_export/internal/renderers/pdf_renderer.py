from dataclasses import dataclass

from ...errors import ExportRenderError, ExportTimeoutError
from ..infrastructure.playwright_pool import (
    BrowserPoolUnavailableError,
    PlaywrightBrowserPool,
)
from ...models import ExportRequest
from .base import DocumentRenderer
from .html_renderer import HtmlRenderer


@dataclass(frozen=True, slots=True)
class PdfRenderer(DocumentRenderer):
    html_renderer: HtmlRenderer
    browser_pool: PlaywrightBrowserPool
    target_format: str = "pdf"

    async def render(self, request: ExportRequest) -> None:
        html = self.html_renderer.render_to_string(
            request.markdown,
            title=request.options.title,
        )

        try:
            await self.browser_pool.render_pdf_from_html(
                html=html,
                output_path=request.output_path,
                timeout_seconds=request.options.timeout_seconds,
            )
        except ExportTimeoutError:
            raise
        except ExportRenderError:
            raise
        except BrowserPoolUnavailableError as e:
            raise ExportRenderError(f"Failed to render PDF: {e.message}") from e
        except Exception as e:
            raise ExportRenderError("Failed to render PDF.") from e
