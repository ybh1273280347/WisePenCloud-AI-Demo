import math
from pathlib import Path

from chat.application.document_parse.errors import DocumentParseError
from chat.application.document_parse.pdf.config import (
    PDF_MAX_IMAGE_PIXELS,
    PDF_RENDER_ALPHA,
    PDF_RENDER_DPI,
)
from common.logger import log_debug


class PageRenderer:
    def __init__(
        self,
        *,
        dpi: int = PDF_RENDER_DPI,
        max_image_pixels: int = PDF_MAX_IMAGE_PIXELS,
        alpha: bool = PDF_RENDER_ALPHA,
    ):
        self._dpi = dpi
        self._max_image_pixels = max_image_pixels
        self._alpha = alpha

    def render_page(self, path: Path, *, page_index: int, output_dir: Path) -> Path:
        import fitz

        output_dir.mkdir(parents=True, exist_ok=True)

        with fitz.open(str(path)) as doc:
            page = doc.load_page(page_index)
            return self.render_loaded_page(
                page, page_index=page_index, output_dir=output_dir
            )

    def render_loaded_page(self, page, *, page_index: int, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        scale = self._dpi / 72.0

        estimated_width = max(1, math.ceil(page.rect.width * scale))
        estimated_height = max(1, math.ceil(page.rect.height * scale))
        estimated_pixels = estimated_width * estimated_height

        if estimated_pixels > self._max_image_pixels:
            raise DocumentParseError(
                f"PDF page render exceeds pixel limit: "
                f"{estimated_pixels} > {self._max_image_pixels}"
            )

        pix = page.get_pixmap(dpi=self._dpi, alpha=self._alpha)
        actual_pixels = pix.width * pix.height

        if actual_pixels > self._max_image_pixels:
            raise DocumentParseError(
                f"PDF page render exceeds pixel limit: "
                f"{actual_pixels} > {self._max_image_pixels}"
            )

        image_path = output_dir / f"page_{page_index + 1}.png"
        pix.save(str(image_path))
        log_debug("PDF page render 完成", page=page_index + 1, dpi=self._dpi)
        return image_path
