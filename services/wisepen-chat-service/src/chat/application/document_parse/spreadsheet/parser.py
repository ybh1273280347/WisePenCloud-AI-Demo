import re
from pathlib import Path
from typing import Any, Dict, List

from chat.application.document_parse import DocumentParseResult, ParsedPage, ParsedTable
from chat.application.document_parse.text_utils import normalize_text


_FILE_TYPE_SPREADSHEET = "spreadsheet"
_PAGE_TYPE_SPREADSHEET = "spreadsheet"
_PARSER_NAME = "SpreadsheetParser"
_BACKEND_PANDAS = "pandas"
_TSV_FENCE = "tsv"


def _format_cell(value: object) -> str:
    if value is None:
        return ""

    text = str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", " / ", text)
    return text.strip()


class SpreadsheetParser:
    """电子表格文档解析器。"""

    def parse(self, path: Path) -> DocumentParseResult:
        import pandas as pd

        sheets = pd.read_excel(path, sheet_name=None, dtype=object, keep_default_na=False)

        text_parts: List[str] = []
        tables: List[ParsedTable] = []
        sheets_info: List[Dict[str, Any]] = []

        for sheet_name, df in sheets.items():
            rows = self._rows_from_dataframe(df)
            sheet_name_text = str(sheet_name)
            sheet_number = len(tables) + 1

            table = ParsedTable(
                table_id=f"sheet_{sheet_number}",
                source=_BACKEND_PANDAS,
                rows=rows,
                page_index=None,
                metadata={
                    "sheet_name": sheet_name_text,
                    "rows": len(df),
                    "columns": len(df.columns),
                },
            )
            tables.append(table)

            sheets_info.append(
                {
                    "name": sheet_name_text,
                    "rows": len(df),
                    "columns": len(df.columns),
                }
            )

            text_parts.append(f"## Sheet: {sheet_name_text}")
            text_parts.append("")
            text_parts.append(f"```{_TSV_FENCE}")
            text_parts.extend(["\t".join(row) for row in rows])
            text_parts.append("```")
            text_parts.append("")

        text = normalize_text("\n".join(text_parts))

        page = ParsedPage(
            page_index=0,
            text=text,
            page_type=_PAGE_TYPE_SPREADSHEET,
            tables=tables,
            metadata={"parser": _BACKEND_PANDAS},
        )

        return DocumentParseResult(
            text=text,
            source=str(path),
            file_type=_FILE_TYPE_SPREADSHEET,
            pages=[page],
            tables=tables,
            metadata={
                "parser": _PARSER_NAME,
                "spreadsheet_backend": _BACKEND_PANDAS,
                "sheet_count": len(sheets),
                "sheets": sheets_info,
                "page_count": 1,
                "table_count": len(tables),
            },
            warnings=[],
        )

    def _rows_from_dataframe(self, df: Any) -> List[List[str]]:
        filled = df.fillna("")
        rows: List[List[str]] = [[_format_cell(col) for col in filled.columns.tolist()]]
        rows.extend([[_format_cell(value) for value in row] for row in filled.values.tolist()])
        return rows
