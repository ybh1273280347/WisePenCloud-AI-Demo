from pathlib import Path

from chat.application.document_parse.base import BaseDocumentParser
from chat.application.document_parse.errors import EmptyParsedContentError
from chat.application.document_parse.models import DocumentParseResult, ParsedPage
from chat.application.document_parse.suffixes import DOCUMENT_TYPE_EPUB
from chat.application.document_parse.text_utils import normalize_text
from common.logger import log_event

_PARSER_NAME = "markitdown"
_PAGE_TYPE_DOCUMENT = "document"


class EpubParser(BaseDocumentParser):
    """EPUB 文档解析器。"""

    supported_extensions = (".epub",)

    def __init__(self):
        self._converter = None
        log_event(
            "EpubParser 初始化", handler_class=type(self).__name__, backend=_PARSER_NAME
        )

    async def parse(self, path: Path) -> DocumentParseResult:
        converter = self._get_converter()

        log_event(
            "EpubParser parse 开始",
            path=str(path),
            handler_class=type(self).__name__,
            backend_class=type(converter).__name__,
        )
        result = converter.convert(str(path))
        text = normalize_text(result.text_content)

        if not text:
            raise EmptyParsedContentError(str(path))

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
        log_event(
            "EpubParser parse 完成",
            path=str(path),
            handler_class=type(self).__name__,
            backend_class=type(converter).__name__,
            result_class=type(result).__name__,
            page_count=1,
            table_count=0,
            length=len(text),
        )
        return parse_result

    def _get_converter(self):
        if self._converter is not None:
            log_event(
                "EpubParser MarkItDown converter 复用",
                converter_class=type(self._converter).__name__,
            )
            return self._converter

        log_event("EpubParser MarkItDown converter import 开始")
        from markitdown import MarkItDown

        log_event(
            "EpubParser MarkItDown converter 初始化开始",
            converter_class=MarkItDown.__name__,
        )
        self._converter = MarkItDown()
        log_event(
            "EpubParser MarkItDown converter 初始化完成",
            converter_class=type(self._converter).__name__,
        )
        return self._converter
