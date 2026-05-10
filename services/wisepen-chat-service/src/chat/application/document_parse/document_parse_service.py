import asyncio
import tempfile
from pathlib import Path
from typing import Any

from chat.application.document_parse import DocumentParseResult
from chat.application.document_parse.suffixes import (
    DOCUMENT_TYPE_DOCX,
    DOCUMENT_TYPE_EPUB,
    DOCUMENT_TYPE_PDF,
    DOCUMENT_TYPE_PPTX,
    DOCUMENT_TYPE_SPREADSHEET,
    detect_document_type_by_suffix,
)


class DocumentParseService:
    """文档解析服务。"""

    def __init__(
        self,
        *,
        pdf_parser: Any,
        office_parser: Any,
        epub_parser: Any,
        spreadsheet_parser: Any,
    ):
        self.pdf_parser = pdf_parser
        self.office_parser = office_parser
        self.epub_parser = epub_parser
        self.spreadsheet_parser = spreadsheet_parser

    async def parse_path(self, path: Path) -> DocumentParseResult:
        if not path.is_file():
            raise FileNotFoundError(f"Document file not found: {path}")

        document_type = detect_document_type_by_suffix(path)

        if document_type == DOCUMENT_TYPE_PDF:
            result = await self.pdf_parser.parse(path)
        elif document_type in {DOCUMENT_TYPE_DOCX, DOCUMENT_TYPE_PPTX}:
            result = await asyncio.to_thread(
                self.office_parser.parse,
                path,
                file_type=document_type,
            )
        elif document_type == DOCUMENT_TYPE_EPUB:
            result = await asyncio.to_thread(self.epub_parser.parse, path)
        elif document_type == DOCUMENT_TYPE_SPREADSHEET:
            result = await asyncio.to_thread(self.spreadsheet_parser.parse, path)
        else:
            raise ValueError(f"Unsupported document type: {document_type}")

        if not result.text.strip():
            raise ValueError(f"No text extracted from document: {path}")

        return result

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