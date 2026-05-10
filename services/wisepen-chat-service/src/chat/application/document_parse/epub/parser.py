from pathlib import Path

from chat.application.document_parse import DocumentParseResult, ParsedPage
from chat.application.document_parse.suffixes import DOCUMENT_TYPE_EPUB
from chat.application.document_parse.text_utils import normalize_text


_PARSER_NAME = "markitdown"
_PAGE_TYPE_DOCUMENT = "document"


class EpubParser:
    """EPUB 文档解析器。"""

    def parse(self, path: Path) -> DocumentParseResult:
        from markitdown import MarkItDown

        converter = MarkItDown()
        result = converter.convert(str(path))
        text = normalize_text(result.text_content)

        if not text:
            raise ValueError(f"No text extracted from document: {path}")

        page = ParsedPage(
            page_index=0,
            text=text,
            page_type=_PAGE_TYPE_DOCUMENT,
            tables=[],
            metadata={"parser": _PARSER_NAME},
        )

        return DocumentParseResult(
            text=text,
            source=str(path),
            file_type=DOCUMENT_TYPE_EPUB,
            pages=[page],
            tables=[],
            metadata={"parser": _PARSER_NAME},
            warnings=[],
        )