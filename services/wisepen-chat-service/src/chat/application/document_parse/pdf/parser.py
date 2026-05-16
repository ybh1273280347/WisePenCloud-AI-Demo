import asyncio
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from chat.application.document_parse.base import BaseDocumentParser
from chat.application.document_parse.errors import UnsupportedDocumentFormatError
from chat.application.document_parse.models import (
    DocumentParseResult,
    ParsedPage,
    ParsedTable,
)
from chat.application.document_parse.pdf.config import (
    PDF_CAMELOT_BATCH_SIZE,
    PDF_CAMELOT_CONCURRENCY,
    PDF_MAX_PAGES,
    PDF_PYMUPDF_TABLE_CONCURRENCY,
    PDF_SCANNED_OCR_CONCURRENCY,
    PDF_SCANNED_OCR_ENABLED,
    PDF_SCANNED_OCR_MAX_PAGES,
    PDF_SCANNED_TABLE_CONCURRENCY,
    PDF_SCANNED_TABLE_REQUIRE_GATE,
    PDF_SCANNED_TABLES_ENABLED,
    PDF_SCANNED_TABLES_MAX_PAGES,
)
from chat.application.document_parse.pdf.page_classifier import (
    _PAGE_TYPE_EMPTY,
    _PAGE_TYPE_MIXED,
    _PAGE_TYPE_SCANNED,
    _PAGE_TYPE_TEXT,
    PageClassifier,
    PageProbe,
)
from chat.application.document_parse.pdf.page_renderer import PageRenderer
from chat.application.document_parse.pdf.scanned_table_extractor import (
    ScannedTableExtractor,
)
from chat.application.document_parse.pdf.table_extractor import TableExtractor
from chat.application.document_parse.pdf.text_extractor import TextExtractor
from chat.application.document_parse.text_utils import normalize_text
from chat.application.ocr import OcrImageAdapter
from common.logger import log_event, log_fail

_PARSER_NAME = "PdfParser"
_FILE_TYPE_PDF = "pdf"

_TEXT_BACKEND_PYMUPDF = "pymupdf"
_TEXT_BACKEND_PADDLEOCR = "paddleocr"
_TABLE_BACKEND_PYMUPDF_FIND_TABLES = "pymupdf_find_tables"
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


@dataclass(slots=True)
class _PageDraft:
    page_index: int
    page_type: str
    text: str
    probe: Optional[PageProbe] = None
    tables: List[ParsedTable] = field(default_factory=list)
    ocr_used: bool = False
    text_backend: str = _BACKEND_NONE
    table_backend: str = _BACKEND_NONE
    table_candidate: bool = False
    table_candidate_reason: str = "none"
    camelot_candidate: bool = False
    camelot_candidate_reason: str = "none"
    scanned_table_candidate: bool = False
    scanned_table_candidate_reason: str = "none"
    pymupdf_table_checked: bool = False
    pp_structure_checked: bool = False
    image_path: Optional[Path] = None


class PdfParser(BaseDocumentParser):
    supported_extensions = (".pdf",)

    def __init__(
        self,
        *,
        classifier: PageClassifier,
        text_extractor: TextExtractor,
        page_renderer: PageRenderer,
        table_extractor: TableExtractor,
        ocr_adapter: OcrImageAdapter,
        scanned_table_extractor: ScannedTableExtractor,
        scanned_ocr_enabled: bool = PDF_SCANNED_OCR_ENABLED,
        scanned_ocr_max_pages: int = PDF_SCANNED_OCR_MAX_PAGES,
        scanned_ocr_concurrency: int = PDF_SCANNED_OCR_CONCURRENCY,
        scanned_tables_enabled: bool = PDF_SCANNED_TABLES_ENABLED,
        scanned_tables_max_pages: int = PDF_SCANNED_TABLES_MAX_PAGES,
        scanned_table_concurrency: int = PDF_SCANNED_TABLE_CONCURRENCY,
        scanned_table_require_gate: bool = PDF_SCANNED_TABLE_REQUIRE_GATE,
        max_pages: int = PDF_MAX_PAGES,
        pymupdf_table_concurrency: int = PDF_PYMUPDF_TABLE_CONCURRENCY,
        camelot_concurrency: int = PDF_CAMELOT_CONCURRENCY,
        camelot_batch_size: int = PDF_CAMELOT_BATCH_SIZE,
    ):
        self.classifier = classifier
        self.text_extractor = text_extractor
        self.page_renderer = page_renderer
        self.table_extractor = table_extractor
        self.ocr_adapter = ocr_adapter
        self.scanned_table_extractor = scanned_table_extractor
        self._scanned_ocr_enabled = scanned_ocr_enabled
        self._scanned_ocr_max_pages = scanned_ocr_max_pages
        self._scanned_ocr_concurrency = max(1, scanned_ocr_concurrency)
        self._scanned_tables_enabled = scanned_tables_enabled
        self._scanned_tables_max_pages = scanned_tables_max_pages
        self._scanned_table_concurrency = max(1, scanned_table_concurrency)
        self._scanned_table_require_gate = scanned_table_require_gate
        self._max_pages = max_pages
        self._pymupdf_table_concurrency = max(1, pymupdf_table_concurrency)
        self._camelot_concurrency = max(1, camelot_concurrency)
        self._camelot_batch_size = max(1, camelot_batch_size)
        log_event(
            "PdfParser 初始化",
            handler_class=type(self).__name__,
            classifier_class=type(classifier).__name__,
            text_extractor_class=type(text_extractor).__name__,
            page_renderer_class=type(page_renderer).__name__,
            table_extractor_class=type(table_extractor).__name__,
            ocr_adapter_class=type(ocr_adapter).__name__,
            scanned_table_extractor_class=type(scanned_table_extractor).__name__,
            scanned_ocr_enabled=scanned_ocr_enabled,
            scanned_ocr_max_pages=scanned_ocr_max_pages,
            scanned_ocr_concurrency=self._scanned_ocr_concurrency,
            scanned_tables_enabled=scanned_tables_enabled,
            scanned_tables_max_pages=scanned_tables_max_pages,
            scanned_table_concurrency=self._scanned_table_concurrency,
            scanned_table_require_gate=scanned_table_require_gate,
            max_pages=max_pages,
            pymupdf_table_concurrency=self._pymupdf_table_concurrency,
            camelot_concurrency=self._camelot_concurrency,
            camelot_batch_size=self._camelot_batch_size,
        )

    async def parse(self, path: Path) -> DocumentParseResult:
        log_event("PdfParser 选择", path=str(path), handler_class=type(self).__name__)
        return await self._parse(path)

    async def _parse(self, path: Path) -> DocumentParseResult:
        import fitz

        parse_started = time.perf_counter()
        with fitz.open(str(path)) as doc:
            total_page_count = len(doc)
            effective_page_count = min(total_page_count, self._max_pages)

            if total_page_count > self._max_pages:
                log_fail(
                    "PDF 页数截断",
                    f"PDF 共 {total_page_count} 页，超过上限 {self._max_pages} 页，仅解析前 {effective_page_count} 页",
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
            warnings: List[str] = []

            if total_page_count > self._max_pages:
                warnings.append(
                    f"page_truncated: PDF 共 {total_page_count} 页，仅解析前 {self._max_pages} 页"
                )

            with tempfile.TemporaryDirectory(prefix="wisepen-pdf-pages-") as tmp_dir:
                render_dir = Path(tmp_dir)
                drafts, probe_ms = await self._extract_drafts(
                    doc,
                    path=path,
                    effective_page_count=effective_page_count,
                    page_type_counts=page_type_counts,
                    warnings=warnings,
                    render_dir=render_dir,
                )
                await self._run_table_stages(path, drafts, warnings=warnings)
                await self._run_scanned_stages(
                    path, drafts, warnings=warnings, render_dir=render_dir
                )

        pages: List[ParsedPage] = []
        all_tables: List[ParsedTable] = []

        for draft in drafts:
            page_tables = draft.tables

            final_table_backend = (
                self._resolve_table_backend(page_tables)
                if page_tables
                else draft.table_backend
            )
            page_text = self._format_page(draft.page_index, draft.text, page_tables)
            all_tables.extend(page_tables)

            page_metadata = self._build_page_metadata(
                page_type=draft.page_type,
                ocr_used=draft.ocr_used,
                text_backend=draft.text_backend,
                table_backend=final_table_backend,
                table_candidate=draft.table_candidate,
                table_candidate_reason=draft.table_candidate_reason,
                camelot_candidate=draft.camelot_candidate,
                camelot_candidate_reason=draft.camelot_candidate_reason,
                scanned_table_candidate=draft.scanned_table_candidate,
                scanned_table_candidate_reason=draft.scanned_table_candidate_reason,
            )

            pages.append(
                ParsedPage(
                    page_index=draft.page_index,
                    text=page_text,
                    page_type=draft.page_type,
                    tables=page_tables,
                    metadata=page_metadata,
                )
            )

        text = normalize_text(
            "\n\n".join(page.text for page in pages if page.text.strip())
        )
        metrics = self._build_stage_metrics(
            total_page_count=total_page_count,
            effective_page_count=effective_page_count,
            drafts=drafts,
            probe_ms=probe_ms,
            total_tables=len(all_tables),
        )
        log_event(
            "PDF parse 完成",
            path=str(path),
            pages=len(pages),
            tables=len(all_tables),
            length=len(text),
            total_ms=round((time.perf_counter() - parse_started) * 1000, 2),
            **metrics,
        )

        return DocumentParseResult(
            text=text,
            source=str(path),
            file_type=_FILE_TYPE_PDF,
            pages=pages,
            tables=all_tables,
            metadata={
                "parser": _PARSER_NAME,
                "selected_parser": _PARSER_NAME,
                "pdf_backend": _TEXT_BACKEND_PYMUPDF,
                "ocr_backend": _TEXT_BACKEND_PADDLEOCR,
                "table_backends": [
                    _TABLE_BACKEND_PYMUPDF_FIND_TABLES,
                    _TABLE_BACKEND_CAMELOT,
                    _TABLE_BACKEND_PP_STRUCTURE,
                ],
                "page_type_counts": page_type_counts,
                "page_count": total_page_count,
                "parsed_page_count": effective_page_count,
                "table_count": len(all_tables),
                "pdf_parse_metrics": metrics,
            },
            warnings=warnings,
        )

    async def _extract_drafts(
        self,
        doc,
        *,
        path: Path,
        effective_page_count: int,
        page_type_counts: Dict[str, int],
        warnings: List[str],
        render_dir: Path,
    ) -> Tuple[List[_PageDraft], float]:
        del path, render_dir

        probe_started = time.perf_counter()
        drafts: List[_PageDraft] = []

        for page_index in range(effective_page_count):
            try:
                page = doc.load_page(page_index)
                probe = self.classifier.probe_page(page, page_index=page_index)
                page_type_counts[probe.page_type] = (
                    page_type_counts.get(probe.page_type, 0) + 1
                )

                if probe.page_type in {_PAGE_TYPE_TEXT, _PAGE_TYPE_MIXED}:
                    drafts.append(self._draft_text_page(probe))
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
            table_candidate_pages=sum(1 for d in drafts if d.table_candidate),
            scanned_table_candidate_pages=sum(
                1 for d in drafts if d.scanned_table_candidate
            ),
        )

        return drafts, probe_ms

    def _draft_text_page(self, probe: PageProbe) -> _PageDraft:
        table_candidate = self.table_extractor.should_extract_tables(probe)
        return _PageDraft(
            page_index=probe.page_index,
            page_type=probe.page_type,
            text=probe.text,
            probe=probe,
            text_backend=_TEXT_BACKEND_PYMUPDF,
            table_candidate=table_candidate,
            table_candidate_reason=probe.table_candidate_reason
            if table_candidate
            else self.table_extractor.table_skip_reason(probe),
            scanned_table_candidate=False,
            scanned_table_candidate_reason="not_scanned",
        )

    def _draft_scanned_page(self, probe: PageProbe) -> _PageDraft:
        return _PageDraft(
            page_index=probe.page_index,
            page_type=_PAGE_TYPE_SCANNED,
            text="",
            probe=probe,
            text_backend=_BACKEND_NONE,
            table_candidate=False,
            table_candidate_reason="scanned_page",
            scanned_table_candidate=probe.maybe_scanned_table,
            scanned_table_candidate_reason=probe.scanned_table_candidate_reason,
        )

    def _draft_empty_page(self, probe: PageProbe) -> _PageDraft:
        return _PageDraft(
            page_index=probe.page_index,
            page_type=_PAGE_TYPE_EMPTY,
            text="",
            probe=probe,
            table_candidate=False,
            table_candidate_reason="empty_page",
            scanned_table_candidate=False,
            scanned_table_candidate_reason="empty_page",
        )

    async def _run_table_stages(
        self,
        path: Path,
        drafts: List[_PageDraft],
        *,
        warnings: List[str],
    ) -> None:
        table_candidates = [
            draft
            for draft in drafts
            if draft.page_type in {_PAGE_TYPE_TEXT, _PAGE_TYPE_MIXED}
            and draft.table_candidate
        ]
        skipped_count = sum(
            1
            for draft in drafts
            if draft.page_type in {_PAGE_TYPE_TEXT, _PAGE_TYPE_MIXED}
            and not draft.table_candidate
        )

        await self._extract_pymupdf_tables_concurrently(
            path, table_candidates, warnings=warnings
        )
        await self._extract_camelot_batches(path, table_candidates, warnings=warnings)

        log_event(
            "PDF table 阶段完成",
            table_candidate_pages=len(table_candidates),
            pymupdf_table_pages=sum(
                1 for d in table_candidates if d.pymupdf_table_checked
            ),
            pymupdf_table_result_pages=sum(
                1
                for d in table_candidates
                if self._has_backend_table(d, _TABLE_BACKEND_PYMUPDF_FIND_TABLES)
            ),
            camelot_candidate_pages=sum(
                1 for d in table_candidates if d.camelot_candidate
            ),
            camelot_skipped_pages=skipped_count
            + sum(1 for d in table_candidates if not d.camelot_candidate),
            total_tables=sum(len(d.tables) for d in table_candidates),
        )

    async def _extract_pymupdf_tables_concurrently(
        self,
        path: Path,
        candidates: List[_PageDraft],
        *,
        warnings: List[str],
    ) -> None:
        if not candidates:
            return
        if not self.table_extractor.can_extract_pymupdf():
            for draft in candidates:
                log_event(
                    "pymupdf_table_skipped",
                    page=draft.page_index + 1,
                    reason="pymupdf_disabled",
                )
            return

        started = time.perf_counter()
        semaphore = asyncio.Semaphore(self._pymupdf_table_concurrency)

        async def _extract_one(draft: _PageDraft) -> None:
            async with semaphore:
                try:
                    tables = await asyncio.to_thread(
                        self.table_extractor.extract_pymupdf_tables_from_path,
                        path,
                        page_index=draft.page_index,
                    )
                    draft.pymupdf_table_checked = True
                    if tables:
                        draft.tables.extend(tables)
                        draft.table_backend = _TABLE_BACKEND_PYMUPDF_FIND_TABLES
                except Exception as e:
                    warning = f"table_parse_failed: page={draft.page_index + 1}: {type(e).__name__}: {e}"
                    warnings.append(warning)
                    log_fail(
                        "PDF pymupdf 表格提取",
                        repr(e),
                        page=draft.page_index + 1,
                        path=str(path),
                    )

        await asyncio.gather(*(_extract_one(draft) for draft in candidates))
        log_event(
            "PDF pymupdf table 阶段完成",
            page_count=len(candidates),
            concurrency=self._pymupdf_table_concurrency,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
            table_pages=sum(1 for d in candidates if d.pymupdf_table_checked),
            table_result_pages=sum(
                1
                for d in candidates
                if self._has_backend_table(d, _TABLE_BACKEND_PYMUPDF_FIND_TABLES)
            ),
        )

    async def _extract_camelot_batches(
        self,
        path: Path,
        candidates: List[_PageDraft],
        *,
        warnings: List[str],
    ) -> None:
        if not candidates:
            return
        if not self.table_extractor.can_extract_camelot():
            for draft in candidates:
                draft.camelot_candidate = False
                draft.camelot_candidate_reason = "camelot_disabled"
            return

        pending: Dict[int, _PageDraft] = {}
        flavor_map: Dict[str, List[int]] = {"lattice": [], "stream": []}
        for draft in candidates:
            pymupdf_tables = [
                table
                for table in draft.tables
                if table.source == _TABLE_BACKEND_PYMUPDF_FIND_TABLES
            ]
            if not self.table_extractor.should_try_camelot(
                draft.probe, pymupdf_tables=pymupdf_tables
            ):
                draft.camelot_candidate = False
                draft.camelot_candidate_reason = (
                    "pymupdf_found_table" if pymupdf_tables else "not_camelot_candidate"
                )
                continue

            flavors = self.table_extractor.choose_camelot_flavors(draft.probe)
            if not flavors:
                draft.camelot_candidate = False
                draft.camelot_candidate_reason = "no_camelot_flavor_enabled"
                continue

            draft.camelot_candidate = True
            draft.camelot_candidate_reason = ",".join(flavors)
            pending[draft.page_index] = draft
            for flavor in flavors:
                flavor_map.setdefault(flavor, []).append(draft.page_index)

        if not pending:
            return

        started = time.perf_counter()
        await self._run_camelot_flavor_batches(
            path,
            flavor="lattice",
            page_indices=flavor_map.get("lattice", []),
            pending=pending,
            warnings=warnings,
        )

        stream_pages = [
            page_index
            for page_index in flavor_map.get("stream", [])
            if self.table_extractor.should_run_stream_after_lattice(
                pending[page_index].tables
            )
        ]
        skipped_stream_pages = set(flavor_map.get("stream", [])) - set(stream_pages)
        for page_index in skipped_stream_pages:
            log_event(
                "table_extract_skipped",
                page=page_index + 1,
                reason="stream_skipped_lattice_found",
            )

        await self._run_camelot_flavor_batches(
            path,
            flavor="stream",
            page_indices=stream_pages,
            pending=pending,
            warnings=warnings,
        )

        log_event(
            "PDF camelot batch 阶段完成",
            candidate_pages=len(pending),
            lattice_pages=len(flavor_map.get("lattice", [])),
            stream_pages=len(stream_pages),
            skipped_stream_pages=len(skipped_stream_pages),
            concurrency=self._camelot_concurrency,
            batch_size=self._camelot_batch_size,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
            table_pages=sum(
                1
                for d in pending.values()
                if self._has_backend_table(d, _TABLE_BACKEND_CAMELOT)
            ),
        )

    async def _run_camelot_flavor_batches(
        self,
        path: Path,
        *,
        flavor: str,
        page_indices: List[int],
        pending: Dict[int, _PageDraft],
        warnings: List[str],
    ) -> None:
        if not page_indices:
            return

        semaphore = asyncio.Semaphore(self._camelot_concurrency)
        batches = self._chunk_indices(
            sorted(set(page_indices)), self._camelot_batch_size
        )

        async def _extract_batch(batch: List[int]) -> None:
            async with semaphore:
                try:
                    result = await asyncio.to_thread(
                        self.table_extractor.extract_camelot_tables_batch,
                        path,
                        page_indices=batch,
                        flavor=flavor,
                    )
                except Exception as e:
                    warning = (
                        f"table_parse_failed: pages={','.join(str(i + 1) for i in batch)}: "
                        f"{type(e).__name__}: {e}"
                    )
                    warnings.append(warning)
                    log_fail(
                        "PDF camelot 表格提取",
                        repr(e),
                        pages=[i + 1 for i in batch],
                        path=str(path),
                        flavor=flavor,
                    )
                    return

                for page_index, tables in result.items():
                    draft = pending.get(page_index)
                    if draft is None or not tables:
                        continue
                    draft.tables.extend(tables)
                    draft.table_backend = self._resolve_table_backend(draft.tables)

        await asyncio.gather(*(_extract_batch(batch) for batch in batches))

    async def _run_scanned_stages(
        self,
        path: Path,
        drafts: List[_PageDraft],
        *,
        warnings: List[str],
        render_dir: Path,
    ) -> None:
        scanned_drafts = [
            draft for draft in drafts if draft.page_type == _PAGE_TYPE_SCANNED
        ]
        if not scanned_drafts:
            return

        started = time.perf_counter()
        render_drafts = self._select_scanned_render_drafts(scanned_drafts)
        await self._render_scanned_pages(
            path, render_drafts, warnings=warnings, render_dir=render_dir
        )
        await self._extract_scanned_ocr(scanned_drafts, warnings=warnings)
        self._refresh_scanned_table_candidates(scanned_drafts)
        await self._extract_scanned_tables(path, scanned_drafts, warnings=warnings)
        log_event(
            "PDF scanned 阶段完成",
            scanned_pages=len(scanned_drafts),
            scanned_render_pages=len(render_drafts),
            scanned_ocr_pages=sum(1 for d in scanned_drafts if d.ocr_used),
            scanned_table_candidate_pages=sum(
                1 for d in scanned_drafts if d.scanned_table_candidate
            ),
            pp_structure_pages=sum(1 for d in scanned_drafts if d.pp_structure_checked),
            pp_structure_result_pages=sum(
                1
                for d in scanned_drafts
                if self._has_backend_table(d, _TABLE_BACKEND_PP_STRUCTURE)
            ),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    def _select_scanned_render_drafts(
        self, drafts: List[_PageDraft]
    ) -> List[_PageDraft]:
        selected: Dict[int, _PageDraft] = {}
        if self._scanned_ocr_enabled and self._scanned_ocr_max_pages > 0:
            for draft in drafts[: self._scanned_ocr_max_pages]:
                selected[draft.page_index] = draft
        if self._scanned_tables_enabled and self._scanned_tables_max_pages > 0:
            for draft in drafts[: self._scanned_tables_max_pages]:
                selected[draft.page_index] = draft
        return [selected[index] for index in sorted(selected)]

    async def _render_scanned_pages(
        self,
        path: Path,
        drafts: List[_PageDraft],
        *,
        warnings: List[str],
        render_dir: Path,
    ) -> None:
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
                    warning = f"page_render_failed: page={draft.page_index + 1}: {type(e).__name__}: {e}"
                    warnings.append(warning)
                    log_fail(
                        "PDF scanned render",
                        repr(e),
                        page=draft.page_index + 1,
                        path=str(path),
                    )

        await asyncio.gather(*(_render_one(draft) for draft in drafts))

    async def _extract_scanned_ocr(
        self,
        drafts: List[_PageDraft],
        *,
        warnings: List[str],
    ) -> None:
        if not self._scanned_ocr_enabled:
            for draft in drafts:
                log_event(
                    "scanned_ocr_skipped",
                    page=draft.page_index + 1,
                    reason="ocr_disabled",
                )
            return

        eligible = [draft for draft in drafts if draft.image_path is not None]
        limited = eligible[: self._scanned_ocr_max_pages]
        for draft in eligible[self._scanned_ocr_max_pages :]:
            log_event(
                "scanned_ocr_skipped",
                page=draft.page_index + 1,
                reason="exceeded_max_scanned_ocr_pages",
                max_scanned_ocr_pages=self._scanned_ocr_max_pages,
            )

        semaphore = asyncio.Semaphore(self._scanned_ocr_concurrency)

        async def _ocr_one(draft: _PageDraft) -> None:
            if draft.image_path is None:
                return
            async with semaphore:
                try:
                    draft.text = await self.ocr_adapter.extract_text(draft.image_path)
                    draft.ocr_used = True
                    draft.text_backend = _TEXT_BACKEND_PADDLEOCR
                except Exception as e:
                    warning = f"ocr_failed: page={draft.page_index + 1}: {type(e).__name__}: {e}"
                    warnings.append(warning)
                    log_fail("PDF scanned OCR", repr(e), page=draft.page_index + 1)

        await asyncio.gather(*(_ocr_one(draft) for draft in limited))

    def _refresh_scanned_table_candidates(self, drafts: List[_PageDraft]) -> None:
        for scanned_number, draft in enumerate(drafts, 1):
            if not self._scanned_tables_enabled:
                draft.scanned_table_candidate = False
                draft.scanned_table_candidate_reason = "scanned_tables_disabled"
                continue
            if scanned_number > self._scanned_tables_max_pages:
                draft.scanned_table_candidate = False
                draft.scanned_table_candidate_reason = (
                    "exceeded_max_scanned_table_pages"
                )
                continue
            if not self._scanned_table_require_gate:
                draft.scanned_table_candidate = True
                draft.scanned_table_candidate_reason = "gate_disabled"
            elif draft.image_path is None:
                draft.scanned_table_candidate = False
                draft.scanned_table_candidate_reason = "missing_rendered_image"
            else:
                is_candidate, reason = self.scanned_table_extractor.looks_like_table(
                    draft.image_path,
                    ocr_text=draft.text if draft.ocr_used else "",
                )
                draft.scanned_table_candidate = is_candidate
                draft.scanned_table_candidate_reason = reason

    async def _extract_scanned_tables(
        self,
        path: Path,
        drafts: List[_PageDraft],
        *,
        warnings: List[str],
    ) -> None:
        if not self._scanned_tables_enabled:
            return

        candidates = [
            draft
            for draft in drafts
            if draft.scanned_table_candidate and draft.image_path is not None
        ]
        limited = candidates[: self._scanned_tables_max_pages]
        for draft in candidates[self._scanned_tables_max_pages :]:
            draft.scanned_table_candidate = False
            draft.scanned_table_candidate_reason = "exceeded_max_scanned_table_pages"
            log_event(
                "scanned_table_skipped",
                page=draft.page_index + 1,
                reason="exceeded_max_scanned_table_pages",
                max_scanned_table_pages=self._scanned_tables_max_pages,
            )

        semaphore = asyncio.Semaphore(self._scanned_table_concurrency)

        async def _extract_one(draft: _PageDraft) -> None:
            if draft.image_path is None:
                return
            async with semaphore:
                try:
                    draft.pp_structure_checked = True
                    tables = await asyncio.to_thread(
                        self.scanned_table_extractor.extract_tables,
                        draft.image_path,
                        page_index=draft.page_index,
                    )
                    if tables:
                        draft.tables.extend(tables)
                        draft.table_backend = _TABLE_BACKEND_PP_STRUCTURE
                except Exception as e:
                    warning = f"table_parse_failed: page={draft.page_index + 1}: {type(e).__name__}: {e}"
                    warnings.append(warning)
                    log_fail(
                        "PP-Structure 表格提取",
                        repr(e),
                        page=draft.page_index + 1,
                        path=str(path),
                    )

        await asyncio.gather(*(_extract_one(draft) for draft in limited))

    def _format_page(
        self, page_index: int, text: str, tables: List[ParsedTable]
    ) -> str:
        content_parts: List[str] = []

        if text.strip():
            content_parts.append(text.strip())

        table_text = _format_tables(tables)
        if table_text:
            content_parts.append(table_text)

        if not content_parts:
            return ""

        return "\n\n".join([f"## Page {page_index + 1}", *content_parts]).strip()

    def _resolve_table_backend(self, page_tables: List[ParsedTable]) -> str:
        if not page_tables:
            return _BACKEND_NONE
        source = page_tables[0].source
        if source == "pymupdf_find_tables":
            return _TABLE_BACKEND_PYMUPDF_FIND_TABLES
        if source == "camelot":
            return _TABLE_BACKEND_CAMELOT
        if source == "pp_structure":
            return _TABLE_BACKEND_PP_STRUCTURE
        return _BACKEND_NONE

    def _has_backend_table(self, draft: _PageDraft, backend: str) -> bool:
        return any(table.source == backend for table in draft.tables)

    def _chunk_indices(self, indices: List[int], size: int) -> List[List[int]]:
        return [indices[start : start + size] for start in range(0, len(indices), size)]

    def _build_stage_metrics(
        self,
        *,
        total_page_count: int,
        effective_page_count: int,
        drafts: List[_PageDraft],
        probe_ms: float,
        total_tables: int,
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
            "table_candidate_pages": sum(1 for d in text_pages if d.table_candidate),
            "pymupdf_table_pages": sum(
                1 for d in text_pages if d.pymupdf_table_checked
            ),
            "pymupdf_table_result_pages": sum(
                1
                for d in text_pages
                if self._has_backend_table(d, _TABLE_BACKEND_PYMUPDF_FIND_TABLES)
            ),
            "camelot_candidate_pages": sum(
                1 for d in text_pages if d.camelot_candidate
            ),
            "camelot_skipped_pages": sum(
                1 for d in text_pages if not d.camelot_candidate
            ),
            "scanned_ocr_pages": sum(1 for d in scanned_pages if d.ocr_used),
            "scanned_table_candidate_pages": sum(
                1 for d in scanned_pages if d.scanned_table_candidate
            ),
            "pp_structure_pages": sum(
                1 for d in scanned_pages if d.pp_structure_checked
            ),
            "pp_structure_result_pages": sum(
                1
                for d in scanned_pages
                if self._has_backend_table(d, _TABLE_BACKEND_PP_STRUCTURE)
            ),
            "total_tables": total_tables,
        }

    def _build_page_metadata(
        self,
        page_type: str,
        ocr_used: bool = False,
        text_backend: str = _BACKEND_NONE,
        table_backend: str = _BACKEND_NONE,
        table_candidate: bool = False,
        table_candidate_reason: str = "none",
        camelot_candidate: bool = False,
        camelot_candidate_reason: str = "none",
        scanned_table_candidate: bool = False,
        scanned_table_candidate_reason: str = "none",
    ) -> Dict[str, Any]:
        return {
            "page_type": page_type,
            "ocr_used": ocr_used,
            "text_backend": text_backend,
            "table_backend": table_backend,
            "table_candidate": table_candidate,
            "table_candidate_reason": table_candidate_reason,
            "camelot_candidate": camelot_candidate,
            "camelot_candidate_reason": camelot_candidate_reason,
            "scanned_table_candidate": scanned_table_candidate,
            "scanned_table_candidate_reason": scanned_table_candidate_reason,
        }
