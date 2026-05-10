import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from chat.application.document_parse import DocumentParseResult, ParsedPage, ParsedTable
from chat.application.document_parse.text_utils import normalize_text
from common.logger import log_fail, log_ok


_PARSER_NAME = "PdfParser"
_FILE_TYPE_PDF = "pdf"

_PAGE_TYPE_TEXT = "text"
_PAGE_TYPE_MIXED = "mixed"
_PAGE_TYPE_SCANNED = "scanned"
_PAGE_TYPE_EMPTY = "empty"

_TEXT_BACKEND_PYMUPDF = "pymupdf"
_TEXT_BACKEND_PADDLEOCR = "paddleocr"
_TABLE_BACKEND_CAMELOT = "camelot"
_TABLE_BACKEND_PP_STRUCTURE = "pp_structure"
_BACKEND_NONE = "none"


def _format_tables(tables: List[ParsedTable]) -> str:
    parts: List[str] = []
    for table_index, table in enumerate(tables, 1):
        parts.append(f"[Table {table_index}]")
        for row in table.rows:
            parts.append(" | ".join(row))
        parts.append("")
    return "\n".join(parts).strip()


class PdfParser:
    def __init__(
        self,
        *,
        classifier: Any,
        text_extractor: Any,
        page_renderer: Any,
        table_extractor: Any,
        ocr_adapter: Any,
        scanned_table_extractor: Any,
    ):
        self.classifier = classifier
        self.text_extractor = text_extractor
        self.page_renderer = page_renderer
        self.table_extractor = table_extractor
        self.ocr_adapter = ocr_adapter
        self.scanned_table_extractor = scanned_table_extractor

    async def parse(self, path: Path) -> DocumentParseResult:
        return await self._parse(path)

    def _page_count(self, path: Path) -> int:
        import fitz

        with fitz.open(str(path)) as doc:
            return len(doc)

    async def _parse(self, path: Path) -> DocumentParseResult:
        page_count = self._page_count(path)
        pages: List[ParsedPage] = []
        tables: List[ParsedTable] = []
        warnings: List[str] = []
        page_type_counts: Dict[str, int] = {
            _PAGE_TYPE_TEXT: 0,
            _PAGE_TYPE_MIXED: 0,
            _PAGE_TYPE_SCANNED: 0,
            _PAGE_TYPE_EMPTY: 0,
        }

        with tempfile.TemporaryDirectory(prefix="wisepen-pdf-pages-") as tmp_dir:
            render_dir = Path(tmp_dir)

            for page_index in range(page_count):
                try:
                    page_type = self.classifier.classify(path, page_index=page_index)
                    page_type_counts[page_type] = page_type_counts.get(page_type, 0) + 1

                    if page_type in {_PAGE_TYPE_TEXT, _PAGE_TYPE_MIXED}:
                        page_text, page_tables = self._parse_text_page(
                            path,
                            page_index=page_index,
                            warnings=warnings,
                        )
                    elif page_type == _PAGE_TYPE_SCANNED:
                        page_text, page_tables = await self._parse_scanned_page(
                            path,
                            page_index=page_index,
                            render_dir=render_dir,
                            warnings=warnings,
                        )
                    elif page_type == _PAGE_TYPE_EMPTY:
                        page_text, page_tables = "", []
                    else:
                        raise ValueError(f"Unknown PDF page type: {page_type}")

                    page_text = self._format_page(page_index, page_text, page_tables)
                    tables.extend(page_tables)

                    page_metadata = self._build_page_metadata(page_type, page_tables)

                    pages.append(
                        ParsedPage(
                            page_index=page_index,
                            text=page_text,
                            page_type=page_type,
                            tables=page_tables,
                            metadata=page_metadata,
                        )
                    )
                except Exception as e:
                    warning = f"page_parse_failed: page={page_index + 1}: {type(e).__name__}: {e}"
                    warnings.append(warning)
                    log_fail("PDF page parse", e, page=page_index + 1, path=str(path))

        text = normalize_text("\n\n".join(page.text for page in pages if page.text.strip()))
        log_ok("PDF parse", path=str(path), pages=len(pages), tables=len(tables), length=len(text))

        return DocumentParseResult(
            text=text,
            source=str(path),
            file_type=_FILE_TYPE_PDF,
            pages=pages,
            tables=tables,
            metadata={
                "parser": _PARSER_NAME,
                "pdf_backend": _TEXT_BACKEND_PYMUPDF,
                "ocr_backend": _TEXT_BACKEND_PADDLEOCR,
                "table_backends": [_TABLE_BACKEND_CAMELOT, _TABLE_BACKEND_PP_STRUCTURE],
                "page_type_counts": page_type_counts,
                "page_count": page_count,
                "table_count": len(tables),
            },
            warnings=warnings,
        )

    def _parse_text_page(
        self,
        path: Path,
        *,
        page_index: int,
        warnings: List[str],
    ) -> Tuple[str, List[ParsedTable]]:
        text = self.text_extractor.extract_page_text(path, page_index=page_index)

        tables: List[ParsedTable] = []
        try:
            tables = self.table_extractor.extract_tables(path, page_index=page_index)
        except Exception as e:
            warnings.append(f"table_parse_failed: page={page_index + 1}: {type(e).__name__}: {e}")
            log_fail("PDF table extraction", e, page=page_index + 1, path=str(path))

        return text, tables

    async def _parse_scanned_page(
        self,
        path: Path,
        *,
        page_index: int,
        render_dir: Path,
        warnings: List[str],
    ) -> Tuple[str, List[ParsedTable]]:
        image_path = self.page_renderer.render_page(path, page_index=page_index, output_dir=render_dir)
        ocr_text = await self.ocr_adapter.extract_text(image_path)

        tables: List[ParsedTable] = []
        try:
            tables = self.scanned_table_extractor.extract_tables(image_path, page_index=page_index)
        except Exception as e:
            warnings.append(f"table_parse_failed: page={page_index + 1}: {type(e).__name__}: {e}")
            log_fail("PP-Structure table extraction", e, page=page_index + 1, path=str(path))

        return ocr_text, tables

    def _format_page(self, page_index: int, text: str, tables: List[ParsedTable]) -> str:
        content_parts: List[str] = []

        if text.strip():
            content_parts.append(text.strip())

        table_text = _format_tables(tables)
        if table_text:
            content_parts.append(table_text)

        if not content_parts:
            return ""

        return "\n\n".join([f"## Page {page_index + 1}", *content_parts]).strip()

    def _build_page_metadata(self, page_type: str, page_tables: List[ParsedTable]) -> Dict[str, Any]:
        if page_type in {_PAGE_TYPE_TEXT, _PAGE_TYPE_MIXED}:
            return {
                "page_type": page_type,
                "ocr_used": False,
                "text_backend": _TEXT_BACKEND_PYMUPDF,
                "table_backend": _TABLE_BACKEND_CAMELOT if page_tables else _BACKEND_NONE,
            }

        if page_type == _PAGE_TYPE_SCANNED:
            return {
                "page_type": page_type,
                "ocr_used": True,
                "text_backend": _TEXT_BACKEND_PADDLEOCR,
                "table_backend": _TABLE_BACKEND_PP_STRUCTURE if page_tables else _BACKEND_NONE,
            }

        return {
            "page_type": page_type,
            "ocr_used": False,
            "text_backend": _BACKEND_NONE,
            "table_backend": _BACKEND_NONE,
        }
