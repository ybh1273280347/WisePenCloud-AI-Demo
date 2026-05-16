__all__ = [
    "BaseDocumentParser",
    "DocumentParseError",
    "DocumentParseResult",
    "DocumentParseResultItem",
    "DocumentParserDependencyError",
    "DocumentParserTimeoutError",
    "EmptyParsedContentError",
    "OcrProcessingError",
    "ParsedPage",
    "ParsedTable",
    "UnsupportedDocumentFormatError",
]


def __getattr__(name: str):
    if name == "DocumentParseService":
        from .document_parse_service import DocumentParseService

        globals()[name] = DocumentParseService
        return DocumentParseService

    if name == "DocumentParseResultItem":
        from .document_parse_service import DocumentParseResultItem

        globals()[name] = DocumentParseResultItem
        return DocumentParseResultItem

    if name == "BaseDocumentParser":
        from .base import BaseDocumentParser

        globals()[name] = BaseDocumentParser
        return BaseDocumentParser

    if name in {
        "DocumentParseError",
        "EmptyParsedContentError",
        "UnsupportedDocumentFormatError",
        "DocumentParserDependencyError",
        "DocumentParserTimeoutError",
        "OcrProcessingError",
    }:
        from . import errors

        exports = {
            "DocumentParseError": errors.DocumentParseError,
            "EmptyParsedContentError": errors.EmptyParsedContentError,
            "UnsupportedDocumentFormatError": errors.UnsupportedDocumentFormatError,
            "DocumentParserDependencyError": errors.DocumentParserDependencyError,
            "DocumentParserTimeoutError": errors.DocumentParserTimeoutError,
            "OcrProcessingError": errors.OcrProcessingError,
        }
        globals().update(exports)
        return exports[name]

    if name in {"DocumentParseResult", "ParsedPage", "ParsedTable"}:
        from .models import DocumentParseResult, ParsedPage, ParsedTable

        exports = {
            "DocumentParseResult": DocumentParseResult,
            "ParsedPage": ParsedPage,
            "ParsedTable": ParsedTable,
        }
        globals().update(exports)
        return exports[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
