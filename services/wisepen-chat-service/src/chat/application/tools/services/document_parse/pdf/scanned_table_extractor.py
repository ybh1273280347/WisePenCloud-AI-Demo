from pathlib import Path
from typing import List, Tuple

from chat.application.tools.common.errors.document_parse import DocumentParseError
from chat.application.tools.services.document_parse.models import ParsedTable

_TABLE_SOURCE = "pp_structure"
_RESULT_TYPE_TABLE = "table"


class ScannedTableExtractor:
    """扫描页表格提取器。"""

    def __init__(self, *, lang: str = "ch"):
        self._lang = lang
        self._engine = None

    def looks_like_table(
        self, image_path: Path, *, ocr_text: str = ""
    ) -> Tuple[bool, str]:
        if self._text_has_table_signal(ocr_text):
            return True, "ocr_text_table_signal"
        if self._image_has_table_lines(image_path):
            return True, "image_line_table_signal"
        return False, "gate_rejected"

    def extract_tables(self, image_path: Path, *, page_index: int) -> List[ParsedTable]:
        image = self._read_image(image_path)
        engine = self._get_engine()
        result = engine(image)

        page_number = page_index + 1
        tables: List[ParsedTable] = []

        for item in result or []:
            rows = self._rows_from_item(item)
            if not rows:
                continue

            table_number = len(tables) + 1
            tables.append(
                ParsedTable(
                    table_id=f"pdf_page_{page_number}_scanned_table_{table_number}",
                    source=_TABLE_SOURCE,
                    rows=rows,
                    page_index=page_index,
                    metadata={},
                )
            )

        return tables

    def _text_has_table_signal(self, text: str) -> bool:
        import re

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        table_like = 0
        for line in lines:
            if "|" in line or "\t" in line:
                parts = [
                    part for part in line.replace("|", "\t").split("\t") if part.strip()
                ]
                if len(parts) >= 2:
                    table_like += 1
                    continue
            if len([part for part in re.split(r"\s{2,}", line) if part.strip()]) >= 2:
                table_like += 1
        return table_like >= 3

    def _image_has_table_lines(self, image_path: Path) -> bool:
        try:
            import cv2
        except Exception:
            return False

        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            return False

        height, width = image.shape[:2]
        if height <= 0 or width <= 0:
            return False

        max_dim = 1600
        scale = min(1.0, max_dim / max(height, width))
        if scale < 1.0:
            image = cv2.resize(
                image,
                (int(width * scale), int(height * scale)),
                interpolation=cv2.INTER_AREA,
            )
            height, width = image.shape[:2]

        _, binary = cv2.threshold(
            image, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )
        horizontal_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (max(20, width // 25), 1)
        )
        vertical_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (1, max(20, height // 25))
        )
        horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
        vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)

        horizontal_count = len(
            cv2.findContours(horizontal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
        )
        vertical_count = len(
            cv2.findContours(vertical, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
        )
        line_pixels = int(cv2.countNonZero(horizontal) + cv2.countNonZero(vertical))
        page_pixels = int(height * width)

        return (
            horizontal_count >= 3
            and vertical_count >= 3
            and line_pixels / max(1, page_pixels) >= 0.002
        )

    def _get_engine(self):
        if self._engine is not None:
            return self._engine

        from paddleocr import PPStructure

        try:
            self._engine = PPStructure(show_log=False, lang=self._lang)
        except TypeError:
            self._engine = PPStructure(lang=self._lang)

        return self._engine

    def _read_image(self, image_path: Path):
        import cv2

        image = cv2.imread(str(image_path))
        if image is None:
            raise DocumentParseError(f"Failed to read scanned page image: {image_path}")

        return image

    def _rows_from_item(self, item) -> List[List[str]]:
        if not isinstance(item, dict):
            return []

        if str(item.get("type", "")).lower() != _RESULT_TYPE_TABLE:
            return []

        result = item.get("res")
        if not isinstance(result, dict):
            return []

        html = result.get("html")
        if not isinstance(html, str):
            return []

        return _parse_html_table_rows(html)


def _parse_html_table_rows(html: str) -> List[List[str]]:
    if not html.strip():
        return []

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    rows: List[List[str]] = []

    for tr in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
        if any(cells):
            rows.append(cells)

    return rows
