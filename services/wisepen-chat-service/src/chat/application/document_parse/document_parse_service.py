import asyncio
import tempfile
from pathlib import Path

from chat.application.document_parse.epub import EpubParser
from chat.application.document_parse.office import OfficeParser
from chat.application.document_parse.pdf import PdfParser
from chat.application.document_parse.spreadsheet import SpreadsheetParser
from chat.application.document_parse.models import DocumentParseResult
from chat.application.document_parse.suffixes import (
    DOCUMENT_TYPE_DOCX,
    DOCUMENT_TYPE_EPUB,
    DOCUMENT_TYPE_PDF,
    DOCUMENT_TYPE_PPTX,
    DOCUMENT_TYPE_SPREADSHEET,
    detect_document_type_by_suffix,
)
from common.logger import log_event, log_ok


class DocumentParseService:
    """文档解析服务。"""

    def __init__(
        self,
        *,
        pdf_parser: PdfParser,
        office_parser: OfficeParser,
        epub_parser: EpubParser,
        spreadsheet_parser: SpreadsheetParser,
    ):
        self.pdf_parser = pdf_parser
        self.office_parser = office_parser
        self.epub_parser = epub_parser
        self.spreadsheet_parser = spreadsheet_parser
        log_ok(
            "DocumentParseService init",
            pdf_parser=type(pdf_parser).__name__,
            office_parser=type(office_parser).__name__,
            epub_parser=type(epub_parser).__name__,
            spreadsheet_parser=type(spreadsheet_parser).__name__,
        )

    async def parse_path(self, path: Path) -> DocumentParseResult:
        if not path.is_file():
            raise FileNotFoundError(f"Document file not found: {path}")

        document_type = detect_document_type_by_suffix(path)
        handler = self._handler_for_document_type(document_type)
        log_event(
            "DocumentParse route",
            path=str(path),
            suffix=path.suffix.lower(),
            document_type=document_type,
            handler_class=type(handler).__name__,
        )

        if document_type == DOCUMENT_TYPE_PDF:
            result = await handler.parse(path)
        elif document_type in {DOCUMENT_TYPE_DOCX, DOCUMENT_TYPE_PPTX}:
            result = await asyncio.to_thread(
                handler.parse,
                path,
                file_type=document_type,
            )
        elif document_type == DOCUMENT_TYPE_EPUB:
            result = await asyncio.to_thread(handler.parse, path)
        elif document_type == DOCUMENT_TYPE_SPREADSHEET:
            result = await asyncio.to_thread(handler.parse, path)
        else:
            raise ValueError(f"Unsupported document type: {document_type}")

        if not result.text.strip():
            raise ValueError(f"No text extracted from document: {path}")

        log_ok(
            "DocumentParse route",
            path=str(path),
            document_type=document_type,
            handler_class=type(handler).__name__,
            parser=result.metadata.get("parser"),
            selected_parser=result.metadata.get("selected_parser"),
            page_count=len(result.pages),
            table_count=len(result.tables),
            length=len(result.text),
        )
        return result

    def _handler_for_document_type(self, document_type: str):
        if document_type == DOCUMENT_TYPE_PDF:
            return self.pdf_parser
        if document_type in {DOCUMENT_TYPE_DOCX, DOCUMENT_TYPE_PPTX}:
            return self.office_parser
        if document_type == DOCUMENT_TYPE_EPUB:
            return self.epub_parser
        if document_type == DOCUMENT_TYPE_SPREADSHEET:
            return self.spreadsheet_parser
        raise ValueError(f"Unsupported document type: {document_type}")

    async def parse_bytes(self, data: bytes, *, filename: str) -> DocumentParseResult:
        suffix = Path(filename).suffix.lower()
        if not suffix:
            raise ValueError("Missing file suffix")

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)

        try:
            return await self.parse_path(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
