from pathlib import Path
from typing import Any, List, Dict

from chat.application.document_parse.models import ParsedTable
from common.logger import log_fail


class TableExtractor:
    def extract_tables(self, path: Path, *, page_index: int) -> List[ParsedTable]:
        try:
            import camelot
        except ImportError:
            return []

        parsed_tables = self._extract_tables_with_flavor(
            camelot,
            path,
            page_index=page_index,
            flavor="lattice",
        )
        if parsed_tables:
            return parsed_tables

        return self._extract_tables_with_flavor(
            camelot,
            path,
            page_index=page_index,
            flavor="stream",
        )

    def _extract_tables_with_flavor(
        self,
        camelot,
        path: Path,
        *,
        page_index: int,
        flavor: str,
    ) -> List[ParsedTable]:
        try:
            tables = camelot.read_pdf(str(path), pages=str(page_index + 1), flavor=flavor)
        except Exception as e:
            log_fail("Camelot 表格提取", e, page=page_index + 1, path=str(path), flavor=flavor)
            return []

        parsed_tables: List[ParsedTable] = []
        for table_index, table in enumerate(tables):
            rows = self._rows_from_table(table)
            if not rows:
                continue

            parsed_tables.append(
                ParsedTable(
                    table_id=f"pdf_page_{page_index}_camelot_{table_index}",
                    source="camelot",
                    rows=rows,
                    page_index=page_index,
                    metadata=self._metadata_from_table(table, flavor=flavor),
                )
            )

        return parsed_tables

    def _rows_from_table(self, table) -> List[List[str]]:
        df = getattr(table, "df", None)
        if df is None:
            return []

        rows: List[List[str]] = []
        for row in df.astype(str).values.tolist():
            cells = [cell.strip() for cell in row]
            if any(cells):
                rows.append(cells)

        return rows

    def _metadata_from_table(self, table, *, flavor: str) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {"flavor": flavor}
        for attr in ("accuracy", "whitespace", "order", "page"):
            if hasattr(table, attr):
                metadata[attr] = getattr(table, attr)
        return metadata
