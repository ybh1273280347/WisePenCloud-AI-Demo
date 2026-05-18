from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
_LINE_ALIGNMENT_TOLERANCE = 6.0
_MIN_TABLE_LIKE_LINES = 3
_MIN_TABLE_LIKE_COLUMNS = 2
_MIN_VECTOR_SHAPES = 8


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
    maybe_vector_table: bool
    maybe_text_table: bool
    maybe_scanned_table: bool
    table_candidate_reason: str = "none"
    scanned_table_candidate_reason: str = "none"


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
        maybe_text_table, text_reason = self._probe_text_table(blocks, text)
        maybe_vector_table, vector_reason = self._probe_vector_table(page)
        maybe_scanned_table = False
        scanned_reason = (
            "await_ocr_or_image_gate"
            if page_type == _PAGE_TYPE_SCANNED
            else "not_scanned_page"
        )

        if maybe_vector_table and maybe_text_table:
            table_reason = f"{vector_reason}+{text_reason}"
        elif maybe_vector_table:
            table_reason = vector_reason
        elif maybe_text_table:
            table_reason = text_reason
        else:
            table_reason = "no_table_signal"

        log_debug(
            "page_probe",
            page=page_index + 1 if page_index >= 0 else None,
            page_type=page_type,
            text_length=text_length,
            has_images=image_probe.has_displayed_images,
            image_area_ratio=round(image_probe.area_ratio, 3)
            if image_probe.area_ratio is not None
            else None,
            maybe_vector_table=maybe_vector_table,
            maybe_text_table=maybe_text_table,
            maybe_scanned_table=maybe_scanned_table,
        )

        return PageProbe(
            page_index=page_index,
            page_type=page_type,
            text=text,
            text_length=text_length,
            has_images=image_probe.has_displayed_images,
            image_area_ratio=image_probe.area_ratio,
            maybe_vector_table=maybe_vector_table,
            maybe_text_table=maybe_text_table,
            maybe_scanned_table=maybe_scanned_table,
            table_candidate_reason=table_reason,
            scanned_table_candidate_reason=scanned_reason,
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

    def _probe_text_table(self, blocks: List[Any], text: str) -> Tuple[bool, str]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        separator_like_lines = 0
        for line in lines:
            if "\t" in line or "|" in line:
                columns = [
                    part for part in line.replace("|", "\t").split("\t") if part.strip()
                ]
                if len(columns) >= _MIN_TABLE_LIKE_COLUMNS:
                    separator_like_lines += 1
                    continue
            if self._split_by_wide_spaces(line) >= _MIN_TABLE_LIKE_COLUMNS:
                separator_like_lines += 1

        if separator_like_lines >= _MIN_TABLE_LIKE_LINES:
            return True, "text_column_separators"

        x_positions: Dict[int, int] = {}
        for block in blocks:
            if self._block_type(block) == _IMAGE_BLOCK_TYPE:
                continue
            if not isinstance(block, (list, tuple)) or len(block) < 5:
                continue
            block_text = self._block_text(block)
            if not block_text:
                continue
            try:
                x0 = float(block[0])
            except Exception:
                continue
            bucket = int(round(x0 / _LINE_ALIGNMENT_TOLERANCE))
            x_positions[bucket] = x_positions.get(bucket, 0) + 1

        repeated_columns = sum(
            1 for count in x_positions.values() if count >= _MIN_TABLE_LIKE_LINES
        )
        if repeated_columns >= _MIN_TABLE_LIKE_COLUMNS:
            return True, "repeated_text_columns"

        return False, "no_text_table_signal"

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

    def _split_by_wide_spaces(self, line: str) -> int:
        import re

        return len([part for part in re.split(r"\s{2,}", line) if part.strip()])

    def _probe_vector_table(self, page) -> Tuple[bool, str]:
        try:
            drawings = page.get_drawings()
        except Exception:
            return False, "vector_probe_failed"

        if not drawings:
            return False, "no_vector_shapes"

        line_like = 0
        rect_like = 0
        for drawing in drawings:
            rect = drawing.get("rect")
            if rect is not None:
                width = float(getattr(rect, "width", 0.0))
                height = float(getattr(rect, "height", 0.0))
                if width > 8 and height > 8:
                    rect_like += 1
                if width > 20 and height <= 2:
                    line_like += 1
                elif height > 20 and width <= 2:
                    line_like += 1

            for item in drawing.get("items", []) or []:
                if not item:
                    continue
                op = item[0]
                if op == "l" and len(item) >= 3:
                    p1 = item[1]
                    p2 = item[2]
                    dx = abs(
                        float(getattr(p1, "x", 0.0)) - float(getattr(p2, "x", 0.0))
                    )
                    dy = abs(
                        float(getattr(p1, "y", 0.0)) - float(getattr(p2, "y", 0.0))
                    )
                    if (dx > 20 and dy <= 2) or (dy > 20 and dx <= 2):
                        line_like += 1
                elif op == "re":
                    rect_like += 1

        if line_like + rect_like >= _MIN_VECTOR_SHAPES:
            return True, "vector_grid_lines"

        return False, "insufficient_vector_shapes"

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

    def _has_large_image_area(self, page) -> bool:
        image_probe = self._probe_images(page)
        if image_probe.area_ratio is None:
            return image_probe.has_displayed_images
        return image_probe.area_ratio >= self._image_area_ratio
