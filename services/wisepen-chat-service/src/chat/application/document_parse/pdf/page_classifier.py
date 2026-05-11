from pathlib import Path


_PAGE_TYPE_TEXT = "text"
_PAGE_TYPE_MIXED = "mixed"
_PAGE_TYPE_SCANNED = "scanned"
_PAGE_TYPE_EMPTY = "empty"


class PageClassifier:
    """PDF 页面类型分类器。"""

    def __init__(
        self,
        *,
        min_text_chars: int = 30,
        image_area_ratio: float = 0.6,
    ):
        self._min_text_chars = min_text_chars
        self._image_area_ratio = image_area_ratio

    def classify(self, path: Path, *, page_index: int) -> str:
        import fitz

        with fitz.open(str(path)) as doc:
            page = doc.load_page(page_index)
            text = page.get_text("text").strip()
            has_images = bool(page.get_images(full=True))
            has_large_image_area = self._has_large_image_area(page)

        if len(text) >= self._min_text_chars:
            if has_images:
                return _PAGE_TYPE_MIXED
            return _PAGE_TYPE_TEXT

        if has_large_image_area:
            return _PAGE_TYPE_SCANNED

        if text:
            return _PAGE_TYPE_MIXED

        return _PAGE_TYPE_EMPTY

    def _has_large_image_area(self, page) -> bool:
        page_area = float(page.rect.width * page.rect.height)
        if page_area <= 0:
            return False

        image_area = 0.0

        for image in page.get_images(full=True):
            xref = image[0]

            for rect in page.get_image_rects(xref):
                image_area += float(rect.width * rect.height)

        return image_area / page_area >= self._image_area_ratio
