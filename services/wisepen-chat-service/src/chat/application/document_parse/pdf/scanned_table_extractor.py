from pathlib import Path
from typing import Any, List

from chat.application.document_parse import ParsedTable


_TABLE_SOURCE = "pp_structure"
_RESULT_TYPE_TABLE = "table"


class ScannedTableExtractor:
    """扫描页表格提取器。"""

    def __init__(self, *, lang: str = "ch"):
        self._lang = lang
        self._engine = None

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

    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine

        from paddleocr import PPStructure

        try:
            self._engine = PPStructure(show_log=False, lang=self._lang)
        except TypeError:
            self._engine = PPStructure(lang=self._lang)

        return self._engine

    def _read_image(self, image_path: Path) -> Any:
        import cv2

        image = cv2.imread(str(image_path))
        if image is None:
            raise ValueError(f"Failed to read scanned page image: {image_path}")

        return image

    def _rows_from_item(self, item: Any) -> List[List[str]]:
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
        cells = [
            cell.get_text(" ", strip=True)
            for cell in tr.find_all(["th", "td"])
        ]
        if any(cells):
            rows.append(cells)

    return rows