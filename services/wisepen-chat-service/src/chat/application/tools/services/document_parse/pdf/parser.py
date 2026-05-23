import asyncio
import contextlib
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from chat.application.tools.common.ocr import OcrImageAdapter
from chat.application.tools.services.document_parse.base import BaseDocumentParser
from chat.application.tools.common.errors.document_parse import UnsupportedDocumentFormatError
from chat.application.tools.services.document_parse.models import (
    DocumentParseResult,
    ParsedPage,
    ParsedTable,
)
from chat.application.tools.services.document_parse.pdf.config import (
    PDF_MARKER_MIN_TEXT_CHARS,
    PDF_MAX_PAGES,
    PDF_PAGE_MIN_TEXT_CHARS,
    PDF_SCANNED_OCR_CONCURRENCY,
    PDF_SCANNED_OCR_ENABLED,
    PDF_SCANNED_OCR_MAX_PAGES,
    PDF_SCANNED_IMAGE_AREA_RATIO,
)
from chat.application.tools.services.document_parse.pdf.marker_extractor import (
    MarkerPdfExtractor,
)
from chat.application.tools.services.document_parse.pdf.page_classifier import (
    _PAGE_TYPE_EMPTY,
    _PAGE_TYPE_MIXED,
    _PAGE_TYPE_SCANNED,
    _PAGE_TYPE_TEXT,
    PageClassifier,
    PageProbe,
)
from chat.application.tools.services.document_parse.pdf.page_renderer import PageRenderer
from chat.application.tools.services.document_parse.pdf.text_extractor import TextExtractor
from chat.application.tools.services.document_parse.text_utils import normalize_text
from common.logger import log_event, log_fail

_PARSER_NAME = "PdfParser"
_FILE_TYPE_PDF = "pdf"

_BACKEND_MARKER = "marker-pdf"
_BACKEND_PYMUPDF = "pymupdf"
_BACKEND_PADDLEOCR = "paddleocr"
_BACKEND_NONE = "none"
_SCANNED_TABLE_BACKEND = "pp_structure"


def _format_table_rows(rows: List[List[Any]]) -> str:
    normalized_rows = _normalize_table_rows(rows)
    if not normalized_rows:
        return ""

    table_rows = _rectangularize_table_rows(normalized_rows)
    if not table_rows:
        return ""

    header = table_rows[0]
    separator = ["---"] * len(header)
    lines = [
        _format_markdown_table_row(header),
        _format_markdown_table_row(separator),
    ]
    lines.extend(_format_markdown_table_row(row) for row in table_rows[1:])
    return "\n".join(lines)


def _normalize_table_rows(rows: List[List[Any]]) -> List[List[str]]:
    normalized_rows: List[List[str]] = []
    for row in rows or []:
        if row is None:
            continue
        cells = row if isinstance(row, (list, tuple)) else [row]
        normalized_row = [_normalize_table_cell(cell) for cell in cells]
        if any(cell for cell in normalized_row):
            normalized_rows.append(normalized_row)
    return normalized_rows


def _normalize_table_cell(cell: Any) -> str:
    if cell is None:
        return ""
    if isinstance(cell, (list, tuple)):
        return " ".join(
            part for part in (_normalize_table_cell(value) for value in cell) if part
        )
    return " ".join(normalize_text(str(cell)).split())


def _rectangularize_table_rows(rows: List[List[str]]) -> List[List[str]]:
    max_columns = max((len(row) for row in rows), default=0)
    if max_columns <= 0:
        return []

    padded_rows = [row + [""] * (max_columns - len(row)) for row in rows]
    keep_columns = [
        index for index in range(max_columns) if any(row[index] for row in padded_rows)
    ]
    if not keep_columns:
        return []

    return [[row[index] for index in keep_columns] for row in padded_rows]


def _format_markdown_table_row(row: List[str]) -> str:
    return "| " + " | ".join(_escape_markdown_table_cell(cell) for cell in row) + " |"


def _escape_markdown_table_cell(cell: str) -> str:
    return cell.replace("\\", "\\\\").replace("|", "\\|")


@dataclass(slots=True)
class _PageDraft:
    """Mutable state used while PDF fallback stages enrich one page."""

    page_index: int
    page_type: str
    text: str
    probe: Optional[PageProbe] = None
    ocr_used: bool = False
    text_backend: str = _BACKEND_NONE
    image_path: Optional[Path] = None
    tables: List[ParsedTable] = field(default_factory=list)


class PdfParser(BaseDocumentParser):
    supported_extensions = (".pdf",)

    def __init__(
        self,
        *,
        classifier: PageClassifier,
        text_extractor: TextExtractor,
        page_renderer: PageRenderer,
        ocr_adapter: OcrImageAdapter,
        marker_extractor: Optional[MarkerPdfExtractor] = None,
        scanned_ocr_enabled: bool = PDF_SCANNED_OCR_ENABLED,
        scanned_ocr_max_pages: int = PDF_SCANNED_OCR_MAX_PAGES,
        scanned_ocr_concurrency: int = PDF_SCANNED_OCR_CONCURRENCY,
        max_pages: int = PDF_MAX_PAGES,
        marker_min_text_chars: int = PDF_MARKER_MIN_TEXT_CHARS,
    ):
        self.classifier = classifier
        self.text_extractor = text_extractor
        self.page_renderer = page_renderer
        self.ocr_adapter = ocr_adapter
        self.marker_extractor = marker_extractor or MarkerPdfExtractor()
        self._scanned_ocr_enabled = scanned_ocr_enabled
        self._scanned_ocr_max_pages = scanned_ocr_max_pages
        self._scanned_ocr_concurrency = max(1, scanned_ocr_concurrency)
        self._max_pages = max_pages
        self._marker_min_text_chars = marker_min_text_chars
        log_event(
            "PdfParser 初始化",
            handler_class=type(self).__name__,
            classifier_class=type(classifier).__name__,
            text_extractor_class=type(text_extractor).__name__,
            page_renderer_class=type(page_renderer).__name__,
            marker_extractor_class=type(self.marker_extractor).__name__,
            ocr_adapter_class=type(ocr_adapter).__name__,
            scanned_ocr_enabled=scanned_ocr_enabled,
            scanned_ocr_max_pages=scanned_ocr_max_pages,
            scanned_ocr_concurrency=self._scanned_ocr_concurrency,
            max_pages=max_pages,
            marker_min_text_chars=marker_min_text_chars,
        )

    async def parse(self, path: Path) -> DocumentParseResult:
        log_event("PdfParser 选择", path=str(path), handler_class=type(self).__name__)
        marker_started = time.perf_counter()
        page_count = self._safe_page_count(path)

        try:
            marker_result = await asyncio.to_thread(self.marker_extractor.extract, path)
            text = normalize_text(marker_result.text)
            if len(text) >= self._marker_min_text_chars:
                log_event(
                    "PDF marker parse 完成",
                    path=str(path),
                    length=len(text),
                    page_count=page_count,
                    elapsed_ms=round((time.perf_counter() - marker_started) * 1000, 2),
                )
                return self._build_marker_result(
                    path,
                    text=text,
                    page_count=page_count,
                    marker_metadata=marker_result.metadata,
                )

            reason = "empty_text" if not text else "text_too_short"
            log_fail(
                "PDF marker parse",
                reason,
                path=str(path),
                length=len(text),
                min_text_chars=self._marker_min_text_chars,
            )
            return await self._parse_with_pymupdf(
                path,
                marker_warning=(
                    f"marker_failed: {reason}: length={len(text)}, "
                    f"min_text_chars={self._marker_min_text_chars}"
                ),
            )
        except Exception as e:
            log_fail("PDF marker parse", repr(e), path=str(path))
            return await self._parse_with_pymupdf(
                path,
                marker_warning=f"marker_failed: {type(e).__name__}: {e}",
            )

    def _build_marker_result(
        self,
        path: Path,
        *,
        text: str,
        page_count: Optional[int],
        marker_metadata: Dict[str, Any],
    ) -> DocumentParseResult:
        parsed_page_count = page_count if page_count is not None else 1
        page = ParsedPage(
            page_index=0,
            text=text,
            page_type="marker",
            tables=[],
            metadata={"backend": _BACKEND_MARKER},
        )
        return DocumentParseResult(
            text=text,
            source=str(path),
            file_type=_FILE_TYPE_PDF,
            pages=[page],
            tables=[],
            metadata={
                "parser": _PARSER_NAME,
                "selected_parser": _PARSER_NAME,
                "pdf_backend": _BACKEND_MARKER,
                "fallback_used": False,
                "page_count": page_count,
                "parsed_page_count": parsed_page_count,
                "marker_metadata": marker_metadata,
            },
        )

    async def _parse_with_pymupdf(
        self,
        path: Path,
        *,
        marker_warning: Optional[str] = None,
    ) -> DocumentParseResult:
        import fitz

        parse_started = time.perf_counter()
        warnings: List[str] = []
        if marker_warning:
            warnings.append(marker_warning)

        with fitz.open(str(path)) as doc:
            total_page_count = len(doc)
            effective_page_count = min(total_page_count, self._max_pages)

            if total_page_count > self._max_pages:
                warning = (
                    f"page_truncated: PDF 共 {total_page_count} 页，"
                    f"仅解析前 {self._max_pages} 页"
                )
                warnings.append(warning)
                log_fail(
                    "PDF 页数截断",
                    warning,
                    path=str(path),
                    total_pages=total_page_count,
                    max_pages=self._max_pages,
                )

            page_type_counts: Dict[str, int] = {
                _PAGE_TYPE_TEXT: 0,
                _PAGE_TYPE_MIXED: 0,
                _PAGE_TYPE_SCANNED: 0,
                _PAGE_TYPE_EMPTY: 0,
            }

            with tempfile.TemporaryDirectory(prefix="wisepen-pdf-pages-") as tmp_dir:
                render_dir = Path(tmp_dir)
                drafts, probe_ms = await self._extract_drafts(
                    doc,
                    effective_page_count=effective_page_count,
                    page_type_counts=page_type_counts,
                    warnings=warnings,
                )
                await self._run_ocr_stages(
                    path, drafts, warnings=warnings, render_dir=render_dir
                )

        pages = [self._page_from_draft(draft) for draft in drafts]
        tables: List[ParsedTable] = []
        for draft in drafts:
            tables.extend(draft.tables)
        text = normalize_text(
            "\n\n".join(page.text for page in pages if page.text.strip())
        )
        metrics = self._build_stage_metrics(
            total_page_count=total_page_count,
            effective_page_count=effective_page_count,
            drafts=drafts,
            probe_ms=probe_ms,
        )
        log_event(
            "PDF fallback parse 完成",
            path=str(path),
            pages=len(pages),
            length=len(text),
            total_ms=round((time.perf_counter() - parse_started) * 1000, 2),
            **metrics,
        )

        return DocumentParseResult(
            text=text,
            source=str(path),
            file_type=_FILE_TYPE_PDF,
            pages=pages,
            tables=tables,
            metadata={
                "parser": _PARSER_NAME,
                "selected_parser": _PARSER_NAME,
                "pdf_backend": _BACKEND_PYMUPDF,
                "fallback_used": True,
                "ocr_backend": _BACKEND_PADDLEOCR,
                "page_type_counts": page_type_counts,
                "page_count": total_page_count,
                "parsed_page_count": effective_page_count,
                "pdf_parse_metrics": metrics,
            },
            warnings=warnings,
        )

    async def _extract_drafts(
        self,
        doc,
        *,
        effective_page_count: int,
        page_type_counts: Dict[str, int],
        warnings: List[str],
    ) -> Tuple[List[_PageDraft], float]:
        probe_started = time.perf_counter()
        drafts: List[_PageDraft] = []

        for page_index in range(effective_page_count):
            try:
                page = doc.load_page(page_index)
                probe = self._probe_page(page, page_index=page_index, warnings=warnings)
                page_type_counts[probe.page_type] = (
                    page_type_counts.get(probe.page_type, 0) + 1
                )

                if probe.page_type in {_PAGE_TYPE_TEXT, _PAGE_TYPE_MIXED}:
                    drafts.append(
                        self._draft_text_page(page, probe, warnings=warnings)
                    )
                elif probe.page_type == _PAGE_TYPE_SCANNED:
                    drafts.append(self._draft_scanned_page(probe))
                elif probe.page_type == _PAGE_TYPE_EMPTY:
                    drafts.append(self._draft_empty_page(probe))
                else:
                    raise UnsupportedDocumentFormatError(probe.page_type)

            except Exception as e:
                warning = (
                    f"page_parse_failed: page={page_index + 1}: {type(e).__name__}: {e}"
                )
                warnings.append(warning)
                log_fail("PDF 页面 probe", repr(e), page=page_index + 1)

        probe_ms = round((time.perf_counter() - probe_started) * 1000, 2)
        log_event(
            "PDF probe 阶段完成",
            parsed_page_count=len(drafts),
            probe_ms=probe_ms,
            text_pages=sum(
                1 for d in drafts if d.page_type in {_PAGE_TYPE_TEXT, _PAGE_TYPE_MIXED}
            ),
            scanned_pages=sum(1 for d in drafts if d.page_type == _PAGE_TYPE_SCANNED),
        )

        return drafts, probe_ms

    def _probe_page(self, page, *, page_index: int, warnings: List[str]) -> PageProbe:
        try:
            return self.classifier.probe_page(page, page_index=page_index)
        except Exception as e:
            warning = (
                f"page_probe_fallback: page={page_index + 1}: "
                f"{type(e).__name__}: {e}"
            )
            warnings.append(warning)
            log_fail("PDF 页面 classifier", repr(e), page=page_index + 1)
            return self.classifier.fallback_probe_page(page, page_index=page_index)

    def _draft_text_page(
        self, page, probe: PageProbe, *, warnings: List[str]
    ) -> _PageDraft:
        text = self.text_extractor.extract_page_text_from_page(page)
        if not text:
            text = probe.text
        table_text, tables = self._extract_pymupdf_table_text(
            page, page_index=probe.page_index, warnings=warnings
        )
        if table_text:
            text = self._append_text_block(text, table_text)
        return _PageDraft(
            page_index=probe.page_index,
            page_type=probe.page_type,
            text=text,
            probe=probe,
            text_backend=_BACKEND_PYMUPDF,
            tables=tables,
        )

    def _draft_scanned_page(self, probe: PageProbe) -> _PageDraft:
        return _PageDraft(
            page_index=probe.page_index,
            page_type=_PAGE_TYPE_SCANNED,
            text="",
            probe=probe,
            text_backend=_BACKEND_NONE,
        )

    def _draft_empty_page(self, probe: PageProbe) -> _PageDraft:
        return _PageDraft(
            page_index=probe.page_index,
            page_type=_PAGE_TYPE_EMPTY,
            text="",
            probe=probe,
            text_backend=_BACKEND_NONE,
        )

    async def _run_ocr_stages(
        self,
        path: Path,
        drafts: List[_PageDraft],
        *,
        warnings: List[str],
        render_dir: Path,
    ) -> None:
        ocr_drafts = [draft for draft in drafts if self._should_run_ocr(draft)]
        if not ocr_drafts:
            return

        started = time.perf_counter()
        render_drafts = self._select_ocr_render_drafts(ocr_drafts)
        await self._render_ocr_pages(
            path, render_drafts, warnings=warnings, render_dir=render_dir
        )
        await self._extract_page_ocr(render_drafts, warnings=warnings)
        await self._extract_scanned_table_text(
            [draft for draft in render_drafts if draft.page_type == _PAGE_TYPE_SCANNED],
            warnings=warnings,
        )
        log_event(
            "PDF OCR 阶段完成",
            ocr_candidate_pages=len(ocr_drafts),
            rendered_pages=len(render_drafts),
            scanned_ocr_pages=sum(
                1 for d in ocr_drafts if d.page_type == _PAGE_TYPE_SCANNED and d.ocr_used
            ),
            mixed_ocr_pages=sum(
                1 for d in ocr_drafts if d.page_type == _PAGE_TYPE_MIXED and d.ocr_used
            ),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    def _should_run_ocr(self, draft: _PageDraft) -> bool:
        if draft.page_type == _PAGE_TYPE_SCANNED:
            return True
        if draft.page_type != _PAGE_TYPE_MIXED:
            return False

        probe = draft.probe
        if probe is None or not probe.has_images:
            return False

        if probe.text_length < PDF_PAGE_MIN_TEXT_CHARS:
            return True

        if probe.image_area_ratio is None:
            return True

        return probe.image_area_ratio >= PDF_SCANNED_IMAGE_AREA_RATIO

    def _select_ocr_render_drafts(
        self, drafts: List[_PageDraft]
    ) -> List[_PageDraft]:
        if not self._scanned_ocr_enabled or self._scanned_ocr_max_pages <= 0:
            for draft in drafts:
                log_event(
                    "pdf_ocr_skipped",
                    page=draft.page_index + 1,
                    page_type=draft.page_type,
                    reason="ocr_disabled",
                )
            return []

        selected = drafts[: self._scanned_ocr_max_pages]
        for draft in drafts[self._scanned_ocr_max_pages :]:
            log_event(
                "pdf_ocr_skipped",
                page=draft.page_index + 1,
                page_type=draft.page_type,
                reason="exceeded_max_scanned_ocr_pages",
                max_scanned_ocr_pages=self._scanned_ocr_max_pages,
            )
        return selected

    async def _render_ocr_pages(
        self,
        path: Path,
        drafts: List[_PageDraft],
        *,
        warnings: List[str],
        render_dir: Path,
    ) -> None:
        if not drafts:
            return

        semaphore = asyncio.Semaphore(self._scanned_ocr_concurrency)

        async def _render_one(draft: _PageDraft) -> None:
            async with semaphore:
                try:
                    draft.image_path = await asyncio.to_thread(
                        self.page_renderer.render_page,
                        path,
                        page_index=draft.page_index,
                        output_dir=render_dir,
                    )
                except Exception as e:
                    warning = (
                        f"page_render_failed: page={draft.page_index + 1}: "
                        f"{type(e).__name__}: {e}"
                    )
                    warnings.append(warning)
                    log_fail(
                        "PDF OCR render",
                        repr(e),
                        page=draft.page_index + 1,
                        path=str(path),
                    )

        await asyncio.gather(*(_render_one(draft) for draft in drafts))

    async def _extract_page_ocr(
        self,
        drafts: List[_PageDraft],
        *,
        warnings: List[str],
    ) -> None:
        eligible = [draft for draft in drafts if draft.image_path is not None]
        if not eligible:
            return

        semaphore = asyncio.Semaphore(self._scanned_ocr_concurrency)

        async def _ocr_one(draft: _PageDraft) -> None:
            if draft.image_path is None:
                return
            async with semaphore:
                try:
                    ocr_text = await self.ocr_adapter.extract_text(draft.image_path)
                    draft.text = self._append_text_block(draft.text, ocr_text)
                    draft.ocr_used = True
                    draft.text_backend = self._merge_text_backend(
                        draft.text_backend, _BACKEND_PADDLEOCR
                    )
                except Exception as e:
                    warning = (
                        f"ocr_failed: page={draft.page_index + 1}: "
                        f"{type(e).__name__}: {e}"
                    )
                    warnings.append(warning)
                    log_fail("PDF OCR", repr(e), page=draft.page_index + 1)

        await asyncio.gather(*(_ocr_one(draft) for draft in eligible))

    async def _extract_scanned_table_text(
        self,
        drafts: List[_PageDraft],
        *,
        warnings: List[str],
    ) -> None:
        eligible = [draft for draft in drafts if draft.image_path is not None]
        if not eligible:
            return

        async def _extract_one(draft: _PageDraft) -> None:
            if draft.image_path is None:
                return
            try:
                table_text, tables = await asyncio.to_thread(
                    self._extract_pp_structure_table_text,
                    draft.image_path,
                    page_index=draft.page_index,
                )
                if table_text:
                    draft.text = self._append_text_block(draft.text, table_text)
                    draft.tables.extend(tables)
            except Exception as e:
                warning = (
                    f"scanned_table_text_failed: page={draft.page_index + 1}: "
                    f"{type(e).__name__}: {e}"
                )
                warnings.append(warning)
                log_fail(
                    "PDF scanned table text",
                    repr(e),
                    page=draft.page_index + 1,
                )

        await asyncio.gather(*(_extract_one(draft) for draft in eligible))

    def _extract_pymupdf_table_text(
        self,
        page,
        *,
        page_index: int,
        warnings: List[str],
    ) -> Tuple[str, List[ParsedTable]]:
        try:
            find_result = page.find_tables()
        except Exception as e:
            warning = (
                f"pymupdf_table_text_failed: page={page_index + 1}: "
                f"{type(e).__name__}: {e}"
            )
            warnings.append(warning)
            log_fail("PDF pymupdf table text", repr(e), page=page_index + 1)
            return "", []

        tables = getattr(find_result, "tables", None) or []
        blocks: List[str] = []
        parsed_tables: List[ParsedTable] = []
        for table_index, table in enumerate(tables, 1):
            try:
                rows = table.extract()
            except Exception as e:
                warning = (
                    f"pymupdf_table_text_failed: page={page_index + 1}, "
                    f"table={table_index}: {type(e).__name__}: {e}"
                )
                warnings.append(warning)
                log_fail(
                    "PDF pymupdf table text",
                    repr(e),
                    page=page_index + 1,
                    table=table_index,
                )
                continue

            normalized_rows = _normalize_table_rows(rows or [])
            table_text = _format_table_rows(normalized_rows)
            if table_text:
                blocks.append(table_text)
                parsed_tables.append(
                    ParsedTable(
                        table_id=f"pdf_page_{page_index}_table_{table_index}",
                        source=_BACKEND_PYMUPDF,
                        rows=normalized_rows,
                        page_index=page_index,
                        metadata={},
                    )
                )

        if not blocks:
            return "", []

        return self._format_table_text_blocks(blocks), parsed_tables

    def _extract_pp_structure_table_text(
        self,
        image_path: Path,
        *,
        page_index: int,
    ) -> Tuple[str, List[ParsedTable]]:
        with self._redirect_stdout_to_stderr():
            from paddleocr import PPStructure

            try:
                engine = PPStructure(show_log=False)
            except TypeError:
                engine = PPStructure()
            results = engine(str(image_path))

        blocks: List[str] = []
        parsed_tables: List[ParsedTable] = []
        for table_index, item in enumerate(results or [], 1):
            rows = self._pp_structure_item_to_rows(item)
            table_text = _format_table_rows(rows)
            if table_text:
                blocks.append(table_text)
                parsed_tables.append(
                    ParsedTable(
                        table_id=f"pdf_page_{page_index}_scanned_table_{table_index}",
                        source=_SCANNED_TABLE_BACKEND,
                        rows=rows,
                        page_index=page_index,
                        metadata={},
                    )
                )

        if not blocks:
            return "", []

        log_event(
            "PDF scanned table text 完成",
            page=page_index + 1,
            backend=_SCANNED_TABLE_BACKEND,
            table_count=len(blocks),
        )
        return self._format_table_text_blocks(blocks), parsed_tables

    def _pp_structure_item_to_rows(self, item: Any) -> List[List[str]]:
        if not isinstance(item, dict):
            return []
        if item.get("type") != "table":
            return []

        result = item.get("res")
        if isinstance(result, dict):
            html = result.get("html")
            if isinstance(html, str):
                return self._html_table_to_rows(html)
            cells = result.get("cells")
            if isinstance(cells, list):
                rows = self._cells_to_rows(cells)
                if rows:
                    return rows
            rows = result.get("rows")
            if isinstance(rows, list):
                return _normalize_table_rows(rows)

        if isinstance(result, list):
            return _normalize_table_rows(result)

        return []

    def _cells_to_rows(self, cells: List[Any]) -> List[List[str]]:
        grid: Dict[Tuple[int, int], str] = {}
        max_row = -1
        max_col = -1

        for cell in cells:
            if not isinstance(cell, dict):
                continue
            row_index = self._first_int_value(
                cell, ("row", "row_index", "row_id", "start_row", "row_start")
            )
            col_index = self._first_int_value(
                cell, ("col", "col_index", "column", "column_index", "start_col", "col_start")
            )
            if row_index is None or col_index is None:
                continue

            rowspan = max(
                1, self._first_int_value(cell, ("rowspan", "row_span"), default=1) or 1
            )
            colspan = max(
                1, self._first_int_value(cell, ("colspan", "col_span"), default=1) or 1
            )
            text = self._first_text_value(cell, ("text", "content", "value"))
            normalized_text = _normalize_table_cell(text)
            if not normalized_text:
                continue

            max_row = max(max_row, row_index + rowspan - 1)
            max_col = max(max_col, col_index + colspan - 1)
            existing = grid.get((row_index, col_index))
            grid[(row_index, col_index)] = (
                self._join_cell_text(existing, normalized_text)
                if existing
                else normalized_text
            )
            for row_offset in range(rowspan):
                for col_offset in range(colspan):
                    row = row_index + row_offset
                    col = col_index + col_offset
                    if row == row_index and col == col_index:
                        continue
                    grid.setdefault((row, col), "")

        if max_row < 0 or max_col < 0:
            return []

        rows = [
            [grid.get((row, col), "") for col in range(max_col + 1)]
            for row in range(max_row + 1)
        ]
        return _normalize_table_rows(rows)

    def _html_table_to_rows(self, html: str) -> List[List[str]]:
        from html.parser import HTMLParser

        class TableParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.rows: List[List[str]] = []
                self._current_row: Optional[List[str]] = None
                self._current_cell: Optional[List[str]] = None
                self._current_colspan = 1
                self._current_rowspan = 1
                self._in_cell = False
                self._rowspans: Dict[int, Tuple[int, str]] = {}

            def handle_starttag(self, tag: str, attrs) -> None:
                if tag == "tr":
                    self._current_row = []
                elif tag in {"td", "th"} and self._current_row is not None:
                    self._apply_pending_rowspans()
                    self._current_cell = []
                    attr_map = dict(attrs or [])
                    self._current_colspan = self._parse_span(attr_map.get("colspan"))
                    self._current_rowspan = self._parse_span(attr_map.get("rowspan"))
                    self._in_cell = True

            def handle_data(self, data: str) -> None:
                if self._in_cell and self._current_cell is not None:
                    text = data.strip()
                    if text:
                        self._current_cell.append(text)

            def handle_endtag(self, tag: str) -> None:
                if tag in {"td", "th"} and self._current_row is not None:
                    cell_text = normalize_text(" ".join(self._current_cell or []))
                    start_col = len(self._current_row)
                    self._current_row.append(cell_text)
                    for _ in range(1, self._current_colspan):
                        self._current_row.append("")
                    if self._current_rowspan > 1:
                        for col_offset in range(self._current_colspan):
                            self._rowspans[start_col + col_offset] = (
                                self._current_rowspan - 1,
                                cell_text if col_offset == 0 else "",
                            )
                    self._current_cell = None
                    self._current_colspan = 1
                    self._current_rowspan = 1
                    self._in_cell = False
                elif tag == "tr" and self._current_row is not None:
                    self._apply_pending_rowspans()
                    if any(cell for cell in self._current_row):
                        self.rows.append(self._current_row)
                    self._current_row = None

            def _apply_pending_rowspans(self) -> None:
                if self._current_row is None:
                    return
                while len(self._current_row) in self._rowspans:
                    column = len(self._current_row)
                    remaining, text = self._rowspans[column]
                    self._current_row.append(text)
                    if remaining <= 1:
                        del self._rowspans[column]
                    else:
                        self._rowspans[column] = (remaining - 1, text)

            def _parse_span(self, value: Optional[str]) -> int:
                if value is None:
                    return 1
                try:
                    span = int(value)
                except ValueError:
                    return 1
                return max(1, span)

        parser = TableParser()
        parser.feed(html)
        return _normalize_table_rows(parser.rows)

    def _first_int_value(
        self,
        item: Dict[str, Any],
        keys: Tuple[str, ...],
        *,
        default: Optional[int] = None,
    ) -> Optional[int]:
        for key in keys:
            value = item.get(key)
            if isinstance(value, int):
                return max(0, value)
            if isinstance(value, float) and value.is_integer():
                return max(0, int(value))
        return default

    def _first_text_value(self, item: Dict[str, Any], keys: Tuple[str, ...]) -> str:
        for key in keys:
            value = item.get(key)
            if value is not None:
                return str(value)
        return ""

    def _join_cell_text(self, left: Optional[str], right: str) -> str:
        if not left:
            return right
        if not right or right == left:
            return left
        return f"{left} {right}"

    def _page_from_draft(self, draft: _PageDraft) -> ParsedPage:
        return ParsedPage(
            page_index=draft.page_index,
            text=self._format_page(draft.page_index, draft.text),
            page_type=draft.page_type,
            tables=draft.tables,
            metadata=self._build_page_metadata(
                page_type=draft.page_type,
                ocr_used=draft.ocr_used,
                text_backend=draft.text_backend,
            ),
        )

    def _format_page(self, page_index: int, text: str) -> str:
        normalized = normalize_text(text)
        if not normalized:
            return ""
        return f"## Page {page_index + 1}\n\n{normalized}".strip()

    def _build_stage_metrics(
        self,
        *,
        total_page_count: int,
        effective_page_count: int,
        drafts: List[_PageDraft],
        probe_ms: float,
    ) -> Dict[str, Any]:
        text_pages = [
            d for d in drafts if d.page_type in {_PAGE_TYPE_TEXT, _PAGE_TYPE_MIXED}
        ]
        scanned_pages = [d for d in drafts if d.page_type == _PAGE_TYPE_SCANNED]
        return {
            "total_pages": total_page_count,
            "parsed_page_count": effective_page_count,
            "probe_ms": probe_ms,
            "text_pages": len(text_pages),
            "scanned_pages": len(scanned_pages),
            "scanned_ocr_pages": sum(1 for d in scanned_pages if d.ocr_used),
        }

    def _build_page_metadata(
        self,
        page_type: str,
        ocr_used: bool = False,
        text_backend: str = _BACKEND_NONE,
    ) -> Dict[str, Any]:
        return {
            "page_type": page_type,
            "ocr_used": ocr_used,
            "text_backend": text_backend,
        }

    def _safe_page_count(self, path: Path) -> Optional[int]:
        try:
            import fitz

            with fitz.open(str(path)) as doc:
                return len(doc)
        except Exception:
            return None

    def _format_table_text_blocks(self, blocks: List[str]) -> str:
        content: List[str] = []
        for index, block in enumerate(blocks, 1):
            content.append(f"### Table {index}")
            content.append("")
            content.append(block)
            content.append("")
        return normalize_text("\n".join(content))

    def _append_text_block(self, text: str, block: str) -> str:
        parts = [part for part in [normalize_text(text), normalize_text(block)] if part]
        return normalize_text("\n\n".join(parts))

    def _merge_text_backend(self, current: str, addition: str) -> str:
        if not current or current == _BACKEND_NONE:
            return addition
        if current == addition:
            return current
        backends = current.split("+")
        if addition not in backends:
            backends.append(addition)
        return "+".join(backends)

    @contextlib.contextmanager
    def _redirect_stdout_to_stderr(self):
        import sys

        original_stdout = sys.stdout
        sys.stdout = sys.stderr
        try:
            yield
        finally:
            sys.stdout = original_stdout
