from .errors import (
    DocumentDecodeError,
    DocumentConvertError,
    DocumentInternalError,
    DocumentParseError,
    DocumentExportError,
    DocumentDownloadRefError,
    DocumentUserActionRequiredError,
    EmptyParsedMarkdownError,
    FileConvertError,
    InvalidDocumentRefError,
    UnreadableDocumentRefError,
    UnsupportedDocumentFormatError,
    UnsupportedDocumentRouteError,
)


def __getattr__(name: str):
    if name == "DocumentConvertService":
        from .service import DocumentConvertService

        return DocumentConvertService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DocumentConvertService",
    "DocumentDecodeError",
    "DocumentConvertError",
    "DocumentInternalError",
    "DocumentParseError",
    "DocumentExportError",
    "DocumentDownloadRefError",
    "DocumentUserActionRequiredError",
    "EmptyParsedMarkdownError",
    "FileConvertError",
    "InvalidDocumentRefError",
    "UnreadableDocumentRefError",
    "UnsupportedDocumentFormatError",
    "UnsupportedDocumentRouteError",
]
