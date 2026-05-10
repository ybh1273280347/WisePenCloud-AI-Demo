import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

from chat.application.document_parse.document_parse_service import DocumentParseService
from chat.application.document_parse.models import DocumentParseResult
from chat.application.document_parse.suffixes import detect_document_type_by_suffix


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class RecordingAsyncParser:
    def __init__(self, file_type: str):
        self.file_type = file_type
        self.calls = []

    async def parse(self, path: Path) -> DocumentParseResult:
        self.calls.append(path)
        return DocumentParseResult(text=f"{self.file_type} text", source=str(path), file_type=self.file_type)


class RecordingOfficeParser:
    def __init__(self):
        self.calls = []

    def parse(self, path: Path, *, file_type: str) -> DocumentParseResult:
        self.calls.append((path, file_type))
        return DocumentParseResult(text=f"{file_type} text", source=str(path), file_type=file_type)


class RecordingSyncParser:
    def __init__(self, file_type: str):
        self.file_type = file_type
        self.calls = []

    def parse(self, path: Path) -> DocumentParseResult:
        self.calls.append(path)
        return DocumentParseResult(text=f"{self.file_type} text", source=str(path), file_type=self.file_type)


def make_service():
    pdf = RecordingAsyncParser("pdf")
    office = RecordingOfficeParser()
    epub = RecordingSyncParser("epub")
    spreadsheet = RecordingSyncParser("spreadsheet")
    service = DocumentParseService(
        pdf_parser=pdf,
        office_parser=office,
        epub_parser=epub,
        spreadsheet_parser=spreadsheet,
    )
    return service, pdf, office, epub, spreadsheet


async def assert_route(suffix: str, expected_text: str):
    service, pdf, office, epub, spreadsheet = make_service()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(b"placeholder")
        path = Path(tmp.name)

    try:
        result = await service.parse_path(path)
    finally:
        path.unlink(missing_ok=True)

    assert_true(result.text == expected_text, f"{suffix} should return {expected_text!r}")

    if suffix == ".pdf":
        assert_true(len(pdf.calls) == 1, "PDF should route to PdfParser")
    elif suffix in {".docx", ".pptx"}:
        assert_true(office.calls == [(path, suffix.lstrip("."))], "Office should route by suffix")
    elif suffix == ".epub":
        assert_true(len(epub.calls) == 1, "EPUB should route to EpubParser")
    else:
        assert_true(len(spreadsheet.calls) == 1, "Spreadsheet should route to pandas parser")


async def main() -> int:
    print("DocumentParse routing verification")

    assert_true(detect_document_type_by_suffix(Path("a.pdf")) == "pdf", "pdf suffix")
    assert_true(detect_document_type_by_suffix(Path("a.docx")) == "docx", "docx suffix")
    assert_true(detect_document_type_by_suffix(Path("a.pptx")) == "pptx", "pptx suffix")
    assert_true(detect_document_type_by_suffix(Path("a.epub")) == "epub", "epub suffix")
    assert_true(detect_document_type_by_suffix(Path("a.xlsx")) == "spreadsheet", "xlsx suffix")

    unsupported = [
        ".html",
        ".htm",
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".xml",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".tiff",
        ".tif",
        ".bmp",
        ".gif",
        ".mp3",
        ".wav",
        ".mp4",
        ".mov",
    ]
    for suffix in unsupported:
        try:
            detect_document_type_by_suffix(Path(f"a{suffix}"))
            assert_true(False, f"{suffix} should be rejected")
        except ValueError as e:
            assert_true("Unsupported document type:" in str(e), f"error should mention unsupported type for {suffix}")

    await assert_route(".pdf", "pdf text")
    await assert_route(".docx", "docx text")
    await assert_route(".pptx", "pptx text")
    await assert_route(".epub", "epub text")
    await assert_route(".xlsx", "spreadsheet text")
    await assert_route(".xls", "spreadsheet text")
    await assert_route(".ods", "spreadsheet text")

    print("PASS DocumentParse routing verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
