from pathlib import Path
from typing import Any

from chat.application.document_parse import DocumentParseResult, ParsedPage
from chat.application.document_parse.text_utils import normalize_text


_PARSER_NAME = "markitdown"
_PAGE_TYPE_DOCUMENT = "document"


class OfficeFallbackParser:
    def __init__(self):
        self._converter = None

    def parse(self, path: Path, *, file_type: str) -> DocumentParseResult:
        converter = self._get_converter()
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
            file_type=file_type,
            pages=[page],
            tables=[],
            metadata={"parser": _PARSER_NAME},
            warnings=[],
        )

    def _get_converter(self) -> Any:
        if self._converter is not None:
            return self._converter

        from markitdown import MarkItDown

        self._converter = MarkItDown()
        return self._converter