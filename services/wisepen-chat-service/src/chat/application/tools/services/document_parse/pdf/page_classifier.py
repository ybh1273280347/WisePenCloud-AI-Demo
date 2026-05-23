from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from chat.application.tools.services.document_parse.pdf.config import (
    PDF_ENABLE_MIXED_PAGE_TYPE,
    PDF_PAGE_MIN_TEXT_CHARS,
    PDF_SCANNED_IMAGE_AREA_RATIO,
)
from chat.application.tools.services.document_parse.text_utils import normalize_text
from common.logger import log_debug, log_event

_PAGE_TYPE_TEXT = "text"
_PAGE_TYPE_MIXED = "mixed"
_PAGE_TYPE_SCANNED = "scanned"
_PAGE_TYPE_EMPTY = "empty"

_IMAGE_BLOCK_TYPE = 1


@dataclass(slots=True)
class ImageProbe:
    has_displayed_images: bool
    area_ratio: Optional[float]
    area_calc_failed: bool = False


@dataclass(slots=True)
class PageProbe:
    page_index: int
    page_type: str
    text: str
    text_length: int
    has_images: bool
    image_area_ratio: Optional[float]


class PageClassifier:
    def __init__(
        self,
        *,
        min_text_chars: int = PDF_PAGE_MIN_TEXT_CHARS,
        image_area_ratio: float = PDF_SCANNED_IMAGE_AREA_RATIO,
        enable_mixed_page_type: bool = PDF_ENABLE_MIXED_PAGE_TYPE,
    ):
        self._min_text_chars = min_text_chars
        self._image_area_ratio = image_area_ratio
        self._enable_mixed_page_type = enable_mixed_page_type

    def classify(self, path: Path, *, page_index: int) -> str:
        import fitz

        with fitz.open(str(path)) as doc:
            page = doc.load_page(page_index)
            return self.probe_page(page, page_index=page_index).page_type

    def classify_page(self, page) -> str:
        return self.probe_page(page, page_index=-1).page_type

    def fallback_probe_page(self, page, *, page_index: int) -> PageProbe:
        text = self._fallback_text(page)
        text_length = len(text)
        image_probe = self._safe_probe_images(page)
        page_type = self._determine_page_type(text, image_probe)

        log_debug(
            "page_probe_fallback",
            page=page_index + 1 if page_index >= 0 else None,
            page_type=page_type,
            text_length=text_length,
            has_images=image_probe.has_displayed_images,
            image_area_ratio=round(image_probe.area_ratio, 3)
            if image_probe.area_ratio is not None
            else None,
        )

        return PageProbe(
            page_index=page_index,
            page_type=page_type,
            text=text,
            text_length=text_length,
            has_images=image_probe.has_displayed_images,
            image_area_ratio=image_probe.area_ratio,
        )

    def probe_page(self, page, *, page_index: int) -> PageProbe:
        blocks = self._get_blocks(page)
        text = self._text_from_blocks(blocks)
        text_length = len(text)
        image_probe = self._image_probe_from_blocks(blocks, page)

        if text_length < self._min_text_chars:
            image_probe = self._probe_images(
                page, fallback_has_images=image_probe.has_displayed_images
            )

        page_type = self._determine_page_type(text, image_probe)

        log_debug(
            "page_probe",
            page=page_index + 1 if page_index >= 0 else None,
            page_type=page_type,
            text_length=text_length,
            has_images=image_probe.has_displayed_images,
            image_area_ratio=round(image_probe.area_ratio, 3)
            if image_probe.area_ratio is not None
            else None,
        )

        return PageProbe(
            page_index=page_index,
            page_type=page_type,
            text=text,
            text_length=text_length,
            has_images=image_probe.has_displayed_images,
            image_area_ratio=image_probe.area_ratio,
        )

    def _determine_page_type(self, text: str, image_probe: ImageProbe) -> str:
        if len(text) >= self._min_text_chars:
            if self._enable_mixed_page_type and image_probe.has_displayed_images:
                return _PAGE_TYPE_MIXED
            return _PAGE_TYPE_TEXT

        if (
            image_probe.area_ratio is not None
            and image_probe.area_ratio >= self._image_area_ratio
        ):
            return _PAGE_TYPE_SCANNED

        if image_probe.area_ratio is None and image_probe.has_displayed_images:
            return _PAGE_TYPE_SCANNED

        if text:
            return _PAGE_TYPE_MIXED

        return _PAGE_TYPE_EMPTY

    def _get_blocks(self, page) -> List[Any]:
        try:
            blocks = page.get_text("blocks", sort=True)
        except Exception:
            blocks = []
        return blocks or []

    def _text_from_blocks(self, blocks: List[Any]) -> str:
        parts: List[str] = []
        for block in blocks:
            if self._block_type(block) == _IMAGE_BLOCK_TYPE:
                continue
            text = self._block_text(block)
            if text:
                parts.append(text)
        return normalize_text("\n".join(parts))

    def _block_type(self, block) -> int:
        if isinstance(block, (list, tuple)) and len(block) > 6:
            try:
                return int(block[6])
            except Exception:
                return 0
        return 0

    def _block_text(self, block) -> str:
        if (
            isinstance(block, (list, tuple))
            and len(block) > 4
            and isinstance(block[4], str)
        ):
            return block[4].strip()
        return ""

    def _image_probe_from_blocks(self, blocks: List[Any], page) -> ImageProbe:
        page_area = float(page.rect.width * page.rect.height)
        image_area = 0.0
        has_images = False
        for block in blocks:
            if self._block_type(block) != _IMAGE_BLOCK_TYPE:
                continue
            has_images = True
            if not isinstance(block, (list, tuple)) or len(block) < 4:
                continue
            try:
                width = float(block[2]) - float(block[0])
                height = float(block[3]) - float(block[1])
            except Exception:
                continue
            if width > 0 and height > 0:
                image_area += width * height

        if not has_images:
            return ImageProbe(False, 0.0, False)

        area_ratio = image_area / page_area if page_area > 0 else 0.0
        return ImageProbe(True, area_ratio, False)

    def _probe_images(self, page, *, fallback_has_images: bool = False) -> ImageProbe:
        try:
            infos = page.get_image_info(hashes=False, xrefs=False)
        except Exception:
            infos = None

        if infos is not None:
            if not infos:
                return ImageProbe(False, 0.0, False)

            image_area = 0.0
            page_area = float(page.rect.width * page.rect.height)
            for info in infos:
                bbox = info.get("bbox")
                if not bbox or len(bbox) < 4:
                    continue
                width = bbox[2] - bbox[0]
                height = bbox[3] - bbox[1]
                if width > 0 and height > 0:
                    image_area += float(width * height)

            area_ratio = image_area / page_area if page_area > 0 else 0.0
            return ImageProbe(True, area_ratio, False)

        has_images = fallback_has_images or bool(page.get_images(full=True))
        if not has_images:
            return ImageProbe(False, 0.0, False)

        try:
            image_area = 0.0
            for image in page.get_images(full=True):
                xref = image[0]
                for rect in page.get_image_rects(xref):
                    image_area += float(rect.width * rect.height)
            page_area = float(page.rect.width * page.rect.height)
            area_ratio = image_area / page_area if page_area > 0 else 0.0
            return ImageProbe(True, area_ratio, False)
        except Exception:
            pass

        log_event("image_area_calc_failed")
        return ImageProbe(True, None, True)

    def _safe_probe_images(self, page) -> ImageProbe:
        try:
            return self._probe_images(page)
        except Exception as e:
            log_event("image_probe_failed", error=repr(e))
            return ImageProbe(False, None, True)

    def _fallback_text(self, page) -> str:
        try:
            return normalize_text(page.get_text("text", sort=True))
        except TypeError:
            try:
                return normalize_text(page.get_text("text"))
            except Exception:
                return ""
        except Exception:
            return ""

    def _has_large_image_area(self, page) -> bool:
        image_probe = self._probe_images(page)
        if image_probe.area_ratio is None:
            return image_probe.has_displayed_images
        return image_probe.area_ratio >= self._image_area_ratio
