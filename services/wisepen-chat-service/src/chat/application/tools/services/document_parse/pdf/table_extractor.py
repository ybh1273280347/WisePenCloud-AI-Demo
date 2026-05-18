import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

from chat.application.tools.services.document_parse.models import ParsedTable
from chat.application.tools.services.document_parse.pdf.config import (
    PDF_CAMELOT_ENABLE_LATTICE,
    PDF_CAMELOT_ENABLE_STREAM,
    PDF_CAMELOT_LATTICE_PROCESS_BACKGROUND,
    PDF_CAMELOT_MAX_TABLES_PER_PAGE,
    PDF_CAMELOT_MAX_WHITESPACE,
    PDF_CAMELOT_MIN_ACCURACY,
    PDF_CAMELOT_MIN_COLS,
    PDF_CAMELOT_MIN_ROWS,
    PDF_CAMELOT_RUN_BOTH_FLAVORS,
    PDF_CAMELOT_STREAM_REQUIRE_GATE,
    PDF_CAMELOT_STRIP_TEXT,
    PDF_CAMELOT_SUPPRESS_NO_TABLE_WARNINGS,
    PDF_EXTRACT_TABLES,
    PDF_PYMUPDF_TABLE_MIN_COLS,
    PDF_PYMUPDF_TABLE_MIN_NON_EMPTY_RATIO,
    PDF_PYMUPDF_TABLE_MIN_ROWS,
    PDF_TABLE_MODE,
    PDF_TABLE_USE_CAMELOT,
    PDF_TABLE_USE_PYMUPDF_FIND_TABLES,
)
from common.logger import log_event, log_fail


class TableExtractor:
    def __init__(
        self,
        *,
        table_mode: str = PDF_TABLE_MODE,
        extract_tables: bool = PDF_EXTRACT_TABLES,
        use_pymupdf_find_tables: bool = PDF_TABLE_USE_PYMUPDF_FIND_TABLES,
        use_camelot: bool = PDF_TABLE_USE_CAMELOT,
        enable_lattice: bool = PDF_CAMELOT_ENABLE_LATTICE,
        enable_stream: bool = PDF_CAMELOT_ENABLE_STREAM,
        stream_require_gate: bool = PDF_CAMELOT_STREAM_REQUIRE_GATE,
        run_both_flavors: bool = PDF_CAMELOT_RUN_BOTH_FLAVORS,
        min_rows: int = PDF_CAMELOT_MIN_ROWS,
        min_cols: int = PDF_CAMELOT_MIN_COLS,
        min_accuracy: float = PDF_CAMELOT_MIN_ACCURACY,
        max_whitespace: float = PDF_CAMELOT_MAX_WHITESPACE,
        max_tables_per_page: int = PDF_CAMELOT_MAX_TABLES_PER_PAGE,
        pymupdf_min_rows: int = PDF_PYMUPDF_TABLE_MIN_ROWS,
        pymupdf_min_cols: int = PDF_PYMUPDF_TABLE_MIN_COLS,
        pymupdf_min_non_empty_ratio: float = PDF_PYMUPDF_TABLE_MIN_NON_EMPTY_RATIO,
    ):
        if table_mode not in {"off", "auto", "full"}:
            raise ValueError("table_mode must be one of: off, auto, full")

        self._table_mode = table_mode
        self._extract_tables = extract_tables
        self._use_pymupdf_find_tables = use_pymupdf_find_tables
        self._use_camelot = use_camelot
        self._enable_lattice = enable_lattice
        self._enable_stream = enable_stream
        self._stream_require_gate = stream_require_gate
        self._run_both_flavors = run_both_flavors
        self._min_rows = min_rows
        self._min_cols = min_cols
        self._min_accuracy = min_accuracy
        self._max_whitespace = max_whitespace
        self._max_tables_per_page = max_tables_per_page
        self._pymupdf_min_rows = pymupdf_min_rows
        self._pymupdf_min_cols = pymupdf_min_cols
        self._pymupdf_min_non_empty_ratio = pymupdf_min_non_empty_ratio

    def extract_tables(self, path: Path, *, page_index: int) -> List[ParsedTable]:
        import fitz
        from chat.application.tools.services.document_parse.pdf.page_classifier import PageClassifier

        with fitz.open(str(path)) as doc:
            page = doc.load_page(page_index)
            probe = PageClassifier().probe_page(page, page_index=page_index)
            return self.extract_tables_from_page(
                path, page=page, page_index=page_index, probe=probe
            )

    def extract_tables_from_page(
        self,
        path: Path,
        *,
        page,
        page_index: int,
        probe=None,
    ) -> List[ParsedTable]:
        if not self.should_extract_tables(probe):
            log_event(
                "table_extract_skipped",
                page=page_index + 1,
                reason=self.table_skip_reason(probe),
            )
            return []

        if self._use_pymupdf_find_tables:
            pymupdf_tables = self._extract_pymupdf_tables(page, page_index=page_index)
            if pymupdf_tables:
                return pymupdf_tables[: self._max_tables_per_page]

        if not self.should_try_camelot(probe, pymupdf_tables=[]):
            log_event(
                "table_extract_skipped", page=page_index + 1, reason="camelot_disabled"
            )
            return []

        camelot_tables = self._extract_camelot_tables(
            path, page_index=page_index, probe=probe
        )
        return camelot_tables[: self._max_tables_per_page]

    def should_extract_tables(self, probe) -> bool:
        if not self._extract_tables:
            return False
        if self._table_mode == "off":
            return False
        if self._table_mode == "full":
            return True
        if probe is None:
            return False
        return bool(
            getattr(probe, "maybe_vector_table", False)
            or getattr(probe, "maybe_text_table", False)
        )

    def table_skip_reason(self, probe) -> str:
        if not self._extract_tables:
            return "extract_tables_disabled"
        if self._table_mode == "off":
            return "table_mode_off"
        if probe is None:
            return "missing_page_probe"
        if not getattr(probe, "maybe_vector_table", False) and not getattr(
            probe, "maybe_text_table", False
        ):
            return "not_table_candidate"
        return "none"

    def can_extract_pymupdf(self) -> bool:
        return (
            self._extract_tables
            and self._table_mode != "off"
            and self._use_pymupdf_find_tables
        )

    def can_extract_camelot(self) -> bool:
        return self._extract_tables and self._table_mode != "off" and self._use_camelot

    def should_try_camelot(self, probe, *, pymupdf_tables: List[ParsedTable]) -> bool:
        if not self.can_extract_camelot():
            return False
        if pymupdf_tables:
            return False
        return self.should_extract_tables(probe)

    def choose_camelot_flavors(self, probe) -> List[str]:
        flavors: List[str] = []
        if not self.can_extract_camelot():
            return flavors

        vector_signal = probe is None or getattr(probe, "maybe_vector_table", False)
        text_signal = probe is None or getattr(probe, "maybe_text_table", False)

        if self._table_mode == "full":
            vector_signal = True
            text_signal = True

        if self._run_both_flavors or (vector_signal and text_signal):
            if self._enable_lattice:
                flavors.append("lattice")
            if self._enable_stream:
                flavors.append("stream")
            return flavors

        if vector_signal and self._enable_lattice:
            return ["lattice"]
        if text_signal and self._enable_stream:
            return ["stream"]
        if self._enable_lattice:
            return ["lattice"]
        if self._enable_stream:
            return ["stream"]
        return flavors

    def should_run_stream_after_lattice(
        self, current_tables: List[ParsedTable]
    ) -> bool:
        if not current_tables:
            return True
        return not self._stream_require_gate or self._run_both_flavors

    def extract_pymupdf_tables_from_path(
        self, path: Path, *, page_index: int
    ) -> List[ParsedTable]:
        import fitz

        with fitz.open(str(path)) as doc:
            page = doc.load_page(page_index)
            return self._extract_pymupdf_tables(page, page_index=page_index)

    def _extract_pymupdf_tables(self, page, *, page_index: int) -> List[ParsedTable]:
        try:
            find_result = page.find_tables()
        except Exception as e:
            log_event("pymupdf_find_tables_error", page=page_index + 1, error=repr(e))
            return []

        tables = find_result.tables if hasattr(find_result, "tables") else []
        log_event(
            "pymupdf_find_tables_result", page=page_index + 1, table_count=len(tables)
        )

        parsed: List[ParsedTable] = []
        for table_index, table in enumerate(tables):
            rows = self._rows_from_pymupdf_table(table)
            if not rows:
                log_event(
                    "pymupdf_table_filtered",
                    page=page_index + 1,
                    table_index=table_index,
                    reason="empty_rows",
                )
                continue

            row_count = len(rows)
            col_count = len(rows[0]) if rows else 0

            if row_count < self._pymupdf_min_rows:
                log_event(
                    "pymupdf_table_filtered",
                    page=page_index + 1,
                    table_index=table_index,
                    reason="min_rows",
                    row_count=row_count,
                )
                continue

            if col_count < self._pymupdf_min_cols:
                log_event(
                    "pymupdf_table_filtered",
                    page=page_index + 1,
                    table_index=table_index,
                    reason="min_cols",
                    col_count=col_count,
                )
                continue

            total_cells = row_count * col_count
            non_empty = sum(1 for row in rows for cell in row if cell.strip())
            non_empty_ratio = non_empty / total_cells if total_cells > 0 else 0.0

            if non_empty_ratio < self._pymupdf_min_non_empty_ratio:
                log_event(
                    "pymupdf_table_filtered",
                    page=page_index + 1,
                    table_index=table_index,
                    reason="min_non_empty_ratio",
                    non_empty_ratio=round(non_empty_ratio, 3),
                    non_empty_cells=non_empty,
                    total_cells=total_cells,
                )
                continue

            parsed.append(
                ParsedTable(
                    table_id=f"pdf_page_{page_index}_pymupdf_{table_index}",
                    source="pymupdf_find_tables",
                    rows=rows,
                    page_index=page_index,
                    metadata={
                        "backend": "pymupdf_find_tables",
                        "page": page_index + 1,
                        "row_count": row_count,
                        "col_count": col_count,
                        "non_empty_cell_ratio": round(non_empty_ratio, 3),
                    },
                )
            )

        return parsed

    def _rows_from_pymupdf_table(self, table) -> List[List[str]]:
        try:
            extracted = table.extract()
        except Exception:
            return []

        if not extracted:
            return []

        rows: List[List[str]] = []
        for row in extracted:
            if row is None:
                continue
            cells = [str(cell).strip() if cell is not None else "" for cell in row]
            if any(cells):
                rows.append(cells)

        return rows

    def _extract_camelot_tables(
        self, path: Path, *, page_index: int, probe=None
    ) -> List[ParsedTable]:
        parsed_tables: List[ParsedTable] = []
        for flavor in self.choose_camelot_flavors(probe):
            flavor_tables = self._extract_tables_with_flavor(
                path,
                page_indices=[page_index],
                flavor=flavor,
            ).get(page_index, [])
            parsed_tables.extend(flavor_tables)
            if parsed_tables and flavor == "lattice" and self._stream_require_gate:
                break
        return parsed_tables

    def extract_camelot_tables_batch(
        self,
        path: Path,
        *,
        page_indices: List[int],
        flavor: str,
    ) -> Dict[int, List[ParsedTable]]:
        return self._extract_tables_with_flavor(
            path, page_indices=page_indices, flavor=flavor
        )

    def _extract_tables_with_flavor(
        self,
        path: Path,
        *,
        page_indices: List[int],
        flavor: str,
    ) -> Dict[int, List[ParsedTable]]:
        try:
            import camelot
        except ImportError:
            return {page_index: [] for page_index in page_indices}

        page_indices = sorted(set(page_indices))
        pages = ",".join(str(index + 1) for index in page_indices)
        result: Dict[int, List[ParsedTable]] = {
            page_index: [] for page_index in page_indices
        }
        try:
            with warnings.catch_warnings():
                if PDF_CAMELOT_SUPPRESS_NO_TABLE_WARNINGS:
                    warnings.filterwarnings(
                        "ignore",
                        message=r"No tables found in table area .*",
                        category=UserWarning,
                        module=r"camelot\.parsers\.base",
                    )
                kwargs: Dict[str, Any] = {
                    "pages": pages,
                    "flavor": flavor,
                    "strip_text": PDF_CAMELOT_STRIP_TEXT,
                    "parallel": False,
                }
                if flavor == "lattice":
                    kwargs["process_background"] = (
                        PDF_CAMELOT_LATTICE_PROCESS_BACKGROUND
                    )
                tables = camelot.read_pdf(str(path), **kwargs)
        except Exception as e:
            log_fail(
                "Camelot 表格提取", repr(e), pages=pages, path=str(path), flavor=flavor
            )
            return result

        for table_index, table in enumerate(tables):
            table_page_index = self._page_index_from_camelot_table(
                table, fallback=page_indices[0]
            )
            if table_page_index not in result:
                log_event(
                    "camelot_table_filtered",
                    page=table_page_index + 1,
                    flavor=flavor,
                    reason="page_not_requested",
                    requested_pages=pages,
                )
                continue

            rows = self._rows_from_table(table)
            if not rows:
                continue

            if not self._is_valid_camelot_table(
                table, rows, flavor=flavor, page_index=table_page_index
            ):
                continue

            if len(result[table_page_index]) >= self._max_tables_per_page:
                log_event(
                    "camelot_table_filtered",
                    page=table_page_index + 1,
                    flavor=flavor,
                    reason="max_tables_per_page",
                    max_tables_per_page=self._max_tables_per_page,
                )
                continue

            result[table_page_index].append(
                ParsedTable(
                    table_id=f"pdf_page_{table_page_index}_camelot_{flavor}_{table_index}",
                    source="camelot",
                    rows=rows,
                    page_index=table_page_index,
                    metadata=self._metadata_from_table(table, flavor=flavor),
                )
            )

        return result

    def _read_quality_metrics(self, table) -> Dict[str, Optional[Any]]:
        report = getattr(table, "parsing_report", None)
        if isinstance(report, dict):
            return {
                "accuracy": report.get("accuracy"),
                "whitespace": report.get("whitespace"),
                "order": report.get("order"),
                "page": report.get("page"),
            }

        return {
            "accuracy": getattr(table, "accuracy", None),
            "whitespace": getattr(table, "whitespace", None),
            "order": getattr(table, "order", None),
            "page": getattr(table, "page", None),
        }

    def _page_index_from_camelot_table(self, table, *, fallback: int) -> int:
        metrics = self._read_quality_metrics(table)
        page = metrics.get("page")
        if page is None:
            page = getattr(table, "page", None)
        try:
            return max(0, int(page) - 1)
        except Exception:
            return fallback

    def _is_valid_camelot_table(
        self, table, rows: List[List[str]], *, flavor: str, page_index: int
    ) -> bool:
        row_count = len(rows)
        col_count = len(rows[0]) if rows else 0

        if row_count < self._min_rows:
            log_event(
                "camelot_table_filtered",
                page=page_index + 1,
                flavor=flavor,
                reason="min_rows",
                row_count=row_count,
                col_count=col_count,
            )
            return False

        if col_count < self._min_cols:
            log_event(
                "camelot_table_filtered",
                page=page_index + 1,
                flavor=flavor,
                reason="min_cols",
                row_count=row_count,
                col_count=col_count,
            )
            return False

        metrics = self._read_quality_metrics(table)
        accuracy = metrics.get("accuracy")
        whitespace = metrics.get("whitespace")

        if accuracy is not None and accuracy < self._min_accuracy:
            log_event(
                "camelot_table_filtered",
                page=page_index + 1,
                flavor=flavor,
                reason="min_accuracy",
                accuracy=accuracy,
                whitespace=whitespace,
                row_count=row_count,
                col_count=col_count,
            )
            return False

        if whitespace is not None and whitespace > self._max_whitespace:
            log_event(
                "camelot_table_filtered",
                page=page_index + 1,
                flavor=flavor,
                reason="max_whitespace",
                accuracy=accuracy,
                whitespace=whitespace,
                row_count=row_count,
                col_count=col_count,
            )
            return False

        if accuracy is None or whitespace is None:
            log_event(
                "camelot_table_filtered",
                page=page_index + 1,
                flavor=flavor,
                reason="missing_quality_metrics",
                accuracy=accuracy,
                whitespace=whitespace,
                row_count=row_count,
                col_count=col_count,
            )

        return True

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
        metrics = self._read_quality_metrics(table)
        for key, value in metrics.items():
            if value is not None:
                metadata[key] = value
        return metadata
