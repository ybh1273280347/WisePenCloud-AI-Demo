import asyncio
from pathlib import Path

from chat.application.tools.services.document_parse.models import ParsedTable
from chat.application.tools.services.document_parse.pdf.marker_extractor import (
    MarkerPdfResult,
)
from chat.application.tools.services.document_parse.pdf.page_classifier import (
    PageProbe,
)
from chat.application.tools.services.document_parse.pdf.page_renderer import PageRenderer
from chat.application.tools.services.document_parse.pdf.parser import (
    PdfParser,
    _format_table_rows,
)
from chat.application.tools.services.document_parse.pdf.text_extractor import TextExtractor


def test_marker_success_does_not_call_pymupdf_fallback(tmp_path: Path) -> None:
    async def run() -> None:
        pdf = _pdf(tmp_path, pages=["fallback text"])
        parser = _parser(
            marker_extractor=FakeMarkerExtractor("# Marker text\n\n" + "x" * 40),
            classifier=FailingClassifier(),
        )

        result = await parser.parse(pdf)

        assert result.text.startswith("# Marker text")
        assert result.tables == []
        assert result.metadata["pdf_backend"] == "marker-pdf"
        assert result.metadata["fallback_used"] is False
        assert result.pages[0].page_type == "marker"

    asyncio.run(run())


def test_marker_exception_enters_pymupdf_fallback(tmp_path: Path) -> None:
    async def run() -> None:
        pdf = _pdf(tmp_path, pages=["fallback text from pymupdf"])
        marker = FailingMarkerExtractor(RuntimeError("marker boom"))
        parser = _parser(marker_extractor=marker)

        result = await parser.parse(pdf)

        assert "fallback text from pymupdf" in result.text
        assert result.tables == []
        assert result.metadata["pdf_backend"] == "pymupdf"
        assert result.metadata["fallback_used"] is True
        assert result.warnings[0].startswith("marker_failed: RuntimeError")

    asyncio.run(run())


def test_marker_short_text_enters_pymupdf_fallback(tmp_path: Path) -> None:
    async def run() -> None:
        pdf = _pdf(tmp_path, pages=["fallback after short marker output"])
        parser = _parser(marker_extractor=FakeMarkerExtractor("short"))

        result = await parser.parse(pdf)

        assert "fallback after short marker output" in result.text
        assert result.metadata["pdf_backend"] == "pymupdf"
        assert result.metadata["fallback_used"] is True
        assert result.tables == []

    asyncio.run(run())


def test_pymupdf_text_page_outputs_text_without_table_metadata(tmp_path: Path) -> None:
    async def run() -> None:
        pdf = _pdf(tmp_path, pages=["plain pdf text page"])
        parser = _parser(marker_extractor=FakeMarkerExtractor(""))

        result = await parser.parse(pdf)

        assert "plain pdf text page" in result.text
        assert "table_backends" not in result.metadata
        assert "table_count" not in result.metadata

    asyncio.run(run())


def test_pymupdf_find_tables_appends_table_text(tmp_path: Path) -> None:
    async def run() -> None:
        pdf = _pdf(tmp_path, pages=["plain pdf text page"])
        parser = _parser(
            marker_extractor=FakeMarkerExtractor(""),
            classifier=FakeClassifier(),
            text_extractor=FakeTextExtractor("plain pdf text page"),
        )

        result = await parser.parse(pdf)

        assert "plain pdf text page" in result.text
        assert "| A | B |" in result.text
        assert "| --- | --- |" in result.text
        assert "| 1 | 2 |" in result.pages[0].text
        assert len(result.tables) == 1
        assert result.tables[0].rows == [["A", "B"], ["1", "2"]]
        assert "table_backends" not in result.metadata
        assert "table_count" not in result.metadata

    asyncio.run(run())


def test_pymupdf_scanned_page_renders_and_ocr(tmp_path: Path) -> None:
    async def run() -> None:
        pdf = _pdf(tmp_path, pages=[""])
        classifier = FakeClassifier([PageProbe(0, "scanned", "", 0, True, 1.0)])
        renderer = FakeRenderer()
        ocr = FakeOcrAdapter("ocr scanned text")
        parser = _parser(
            marker_extractor=FakeMarkerExtractor(""),
            classifier=classifier,
            page_renderer=renderer,
            ocr_adapter=ocr,
        )
        parser._extract_pp_structure_table_text = lambda image_path, page_index: (
            "",
            [],
        )

        result = await parser.parse(pdf)

        assert "ocr scanned text" in result.text
        assert renderer.calls == [0]
        assert ocr.calls == 1
        assert result.pages[0].metadata["ocr_used"] is True
        assert result.tables == []

    asyncio.run(run())


def test_scanned_page_pp_structure_appends_table_text(monkeypatch, tmp_path: Path) -> None:
    async def run() -> None:
        pdf = _pdf(tmp_path, pages=[""])
        classifier = FakeClassifier([PageProbe(0, "scanned", "", 0, True, 1.0)])
        renderer = FakeRenderer()
        ocr = FakeOcrAdapter("ocr scanned text")
        parser = _parser(
            marker_extractor=FakeMarkerExtractor(""),
            classifier=classifier,
            page_renderer=renderer,
            ocr_adapter=ocr,
        )

        monkeypatch.setattr(
            parser,
            "_extract_pp_structure_table_text",
            lambda image_path, page_index: (
                "### Table 1\n\n| H | V |\n| h1 | v1 |",
                [
                    ParsedTable(
                        table_id="table",
                        source="pp_structure",
                        rows=[["H", "V"], ["h1", "v1"]],
                        page_index=page_index,
                        metadata={},
                    )
                ],
            ),
        )

        result = await parser.parse(pdf)

        assert "ocr scanned text" in result.text
        assert "| H | V |" in result.text
        assert len(result.tables) == 1
        assert result.pages[0].tables[0].rows == [["H", "V"], ["h1", "v1"]]

    asyncio.run(run())


def test_pymupdf_fallback_truncates_over_max_pages(tmp_path: Path) -> None:
    async def run() -> None:
        pdf = _pdf(tmp_path, pages=["page one", "page two", "page three"])
        parser = _parser(marker_extractor=FakeMarkerExtractor(""), max_pages=2)

        result = await parser.parse(pdf)

        assert len(result.pages) == 2
        assert result.metadata["page_count"] == 3
        assert result.metadata["parsed_page_count"] == 2
        assert any(warning.startswith("page_truncated:") for warning in result.warnings)

    asyncio.run(run())


def test_table_rows_format_as_stable_markdown() -> None:
    markdown = _format_table_rows(
        [
            ["Name", "Value | Unit", ""],
            ["Alpha\nBeta", None, "tail"],
            ["", "", ""],
        ]
    )

    assert markdown == "\n".join(
        [
            "| Name | Value \\| Unit |  |",
            "| --- | --- | --- |",
            "| Alpha Beta |  | tail |",
        ]
    )


def test_pp_structure_html_rows_expand_spans() -> None:
    parser = _parser(marker_extractor=FakeMarkerExtractor(""))

    rows = parser._html_table_to_rows(
        """
        <table>
          <tr><th rowspan="2">Region</th><th colspan="2">Sales</th></tr>
          <tr><th>Q1</th><th>Q2</th></tr>
          <tr><td>North</td><td>1</td><td>2</td></tr>
        </table>
        """
    )

    assert rows == [
        ["Region", "Sales", ""],
        ["Region", "Q1", "Q2"],
        ["North", "1", "2"],
    ]


def test_pp_structure_positioned_cells_to_rows() -> None:
    parser = _parser(marker_extractor=FakeMarkerExtractor(""))

    rows = parser._pp_structure_item_to_rows(
        {
            "type": "table",
            "res": {
                "cells": [
                    {"row": 0, "col": 0, "text": "A"},
                    {"row": 0, "col": 1, "text": "B"},
                    {"row": 1, "col": 0, "text": "1"},
                    {"row": 1, "col": 1, "text": "2"},
                ]
            },
        }
    )

    assert rows == [["A", "B"], ["1", "2"]]


def test_pdf_business_code_has_no_removed_table_pipeline_refs() -> None:
    root = Path(__file__).resolve().parents[3]
    forbidden = [
        "PDF" + "_TABLE_",
        "PDF" + "_CAMELOT_",
        "PDF" + "_PYMU" + "PDF" + "_TABLE_",
        "PDF" + "_SCANNED" + "_TABLE_",
        "Came" + "lot",
        "came" + "lot",
        "Table" + "Extractor",
        "Scanned" + "Table" + "Extractor",
        "table" + "_candidate",
        "came" + "lot_candidate",
        "scanned" + "_table" + "_candidate",
    ]

    for base in [root / "src", root / "tests" / "application" / "document_parse"]:
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for needle in forbidden:
                assert needle not in text, str(path)


def _parser(
    *,
    marker_extractor,
    classifier=None,
    text_extractor=None,
    page_renderer=None,
    ocr_adapter=None,
    max_pages: int = 80,
) -> PdfParser:
    return PdfParser(
        classifier=classifier or FakeClassifier(),
        text_extractor=text_extractor or TextExtractor(),
        page_renderer=page_renderer or PageRenderer(),
        marker_extractor=marker_extractor,
        ocr_adapter=ocr_adapter or FakeOcrAdapter(""),
        max_pages=max_pages,
    )


def _pdf(tmp_path: Path, *, pages) -> Path:
    import fitz

    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return path


class FakeMarkerExtractor:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def extract(self, path: Path) -> MarkerPdfResult:
        self.calls += 1
        return MarkerPdfResult(text=self.text, metadata={"fake": True})


class FailingMarkerExtractor:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def extract(self, path: Path) -> MarkerPdfResult:
        raise self.error


class FakeClassifier:
    def __init__(self, probes=None) -> None:
        self.probes = probes or []

    def probe_page(self, page, *, page_index: int) -> PageProbe:
        if self.probes:
            return self.probes[page_index]
        text = page.get_text("text", sort=True).strip()
        return PageProbe(
            page_index=page_index,
            page_type="text" if text else "empty",
            text=text,
            text_length=len(text),
            has_images=False,
            image_area_ratio=0.0,
        )


class FakeTextExtractor:
    def __init__(self, text: str) -> None:
        self.text = text

    def extract_page_text_from_page(self, page) -> str:
        page.find_tables = lambda: FakeFindTables()
        return self.text


class FakeFindTables:
    tables = []

    def __init__(self) -> None:
        self.tables = [FakeTable()]


class FakeTable:
    def extract(self):
        return [["A", "B"], ["1", "2"]]


class FailingClassifier:
    def probe_page(self, page, *, page_index: int) -> PageProbe:
        raise AssertionError("PyMuPDF fallback should not run")


class FakeRenderer:
    def __init__(self) -> None:
        self.calls = []

    def render_page(self, path: Path, *, page_index: int, output_dir: Path) -> Path:
        self.calls.append(page_index)
        output_dir.mkdir(parents=True, exist_ok=True)
        image_path = output_dir / f"page_{page_index + 1}.png"
        image_path.write_bytes(b"fake image")
        return image_path


class FakeOcrAdapter:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def extract_text(self, image_path: Path) -> str:
        self.calls += 1
        return self.text
