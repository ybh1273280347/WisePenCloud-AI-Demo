import math
from pathlib import Path

from common.logger import log_ok


class PageRenderer:
    """PDF 页面渲染器。"""

    def __init__(self, *, dpi: int, max_image_pixels: int):
        self._dpi = dpi
        self._max_image_pixels = max_image_pixels

    def render_page(self, path: Path, *, page_index: int, output_dir: Path) -> Path:
        import fitz

        output_dir.mkdir(parents=True, exist_ok=True)
        scale = self._dpi / 72.0

        with fitz.open(str(path)) as doc:
            page = doc.load_page(page_index)

            estimated_width = max(1, math.ceil(page.rect.width * scale))
            estimated_height = max(1, math.ceil(page.rect.height * scale))
            estimated_pixels = estimated_width * estimated_height

            if estimated_pixels > self._max_image_pixels:
                raise ValueError(
                    f"PDF 页面渲染将超出像素限制: "
                    f"{estimated_pixels} > {self._max_image_pixels}"
                )

            pix = page.get_pixmap(dpi=self._dpi, alpha=False)
            actual_pixels = pix.width * pix.height

            if actual_pixels > self._max_image_pixels:
                raise ValueError(
                    f"PDF 页面渲染超出像素限制: "
                    f"{actual_pixels} > {self._max_image_pixels}"
                )

            image_path = output_dir / f"page_{page_index + 1}.png"
            pix.save(str(image_path))
        log_ok("PDF page render", page=page_index + 1, dpi=self._dpi)
        return image_path