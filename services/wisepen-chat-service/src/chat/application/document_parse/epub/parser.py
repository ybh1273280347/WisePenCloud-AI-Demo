from pathlib import Path

from chat.application.document_parse.models import DocumentParseResult, ParsedPage
from chat.application.document_parse.suffixes import DOCUMENT_TYPE_EPUB
from chat.application.document_parse.text_utils import normalize_text
from common.logger import log_event, log_ok


_PARSER_NAME = "markitdown"
_PAGE_TYPE_DOCUMENT = "document"


class EpubParser:
    """EPUB 文档解析器。"""

    def __init__(self):
        log_ok("EpubParser init", handler_class=type(self).__name__, backend=_PARSER_NAME)

    def parse(self, path: Path) -> DocumentParseResult:
        from markitdown import MarkItDown

        log_event(
            "EpubParser parse start",
            path=str(path),
            handler_class=type(self).__name__,
            backend_class=MarkItDown.__name__,
        )
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

        parse_result = DocumentParseResult(
            text=text,
            source=str(path),
            file_type=DOCUMENT_TYPE_EPUB,
            pages=[page],
            tables=[],
            metadata={
                "parser": _PARSER_NAME,
                "selected_parser": _PARSER_NAME,
            },
            warnings=[],
        )
        log_ok(
            "EpubParser parse",
            path=str(path),
            handler_class=type(self).__name__,
            backend_class=type(converter).__name__,
            result_class=type(result).__name__,
            page_count=1,
            table_count=0,
            length=len(text),
        )
        return parse_result
