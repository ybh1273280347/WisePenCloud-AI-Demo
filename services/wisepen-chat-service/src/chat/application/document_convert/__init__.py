from .errors import (
    DocumentConvertError,
    EmptyParsedMarkdownError,
    FileConvertError,
    SourceFileNotFoundError,
)


def __getattr__(name: str):
    if name == "DocumentConvertService":
        from .service import DocumentConvertService

        return DocumentConvertService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DocumentConvertService",
    "DocumentConvertError",
    "EmptyParsedMarkdownError",
    "FileConvertError",
    "SourceFileNotFoundError",
]
