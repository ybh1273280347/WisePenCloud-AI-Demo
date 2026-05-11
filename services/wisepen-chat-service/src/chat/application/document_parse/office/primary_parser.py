from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from chat.application.document_parse.models import DocumentParseResult, ParsedPage
from chat.application.document_parse.text_utils import normalize_text
from common.logger import log_event, log_ok


_PARSER_NAME = "docling"
_PAGE_TYPE_DOCUMENT = "document"


def _package_version(package_name: str) -> str:
    try:
        return version(package_name)
    except PackageNotFoundError:
        return "unknown"


class OfficePrimaryParser:
    """Office 文档主解析器。"""

    def __init__(self):
        self._converter = None
        log_ok("Docling OfficePrimaryParser init", handler_class=type(self).__name__)

    def parse(self, path: Path, *, file_type: str) -> DocumentParseResult:
        converter = self._get_converter()
        log_event(
            "Docling convert start",
            path=str(path),
            file_type=file_type,
            handler_class=type(self).__name__,
            converter_class=type(converter).__name__,
            docling_version=_package_version("docling"),
        )
        result = converter.convert(str(path))
        document = result.document
        text = normalize_text(result.document.export_to_markdown())

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
            file_type=file_type,
            pages=[page],
            tables=[],
            metadata={"parser": _PARSER_NAME},
            warnings=[],
        )
        log_ok(
            "Docling convert",
            path=str(path),
            file_type=file_type,
            handler_class=type(self).__name__,
            converter_class=type(converter).__name__,
            docling_version=_package_version("docling"),
            result_class=type(result).__name__,
            document_class=type(document).__name__,
            page_count=1,
            table_count=0,
            length=len(text),
        )
        return parse_result

    def _get_converter(self):
        if self._converter is not None:
            log_event(
                "Docling DocumentConverter reuse",
                converter_class=type(self._converter).__name__,
                docling_version=_package_version("docling"),
            )
            return self._converter

        log_event("Docling DocumentConverter import start", docling_version=_package_version("docling"))
        from docling.document_converter import DocumentConverter

        log_event(
            "Docling DocumentConverter init start",
            converter_class=DocumentConverter.__name__,
            docling_version=_package_version("docling"),
        )
        self._converter = DocumentConverter()
        log_ok(
            "Docling DocumentConverter init",
            converter_class=type(self._converter).__name__,
            docling_version=_package_version("docling"),
        )
        return self._converter
