from .constants import (
    CONTENT_TYPES,
    FILE_EXTENSIONS,
    SOURCE_FORMATS,
    SUPPORTED_EXPORT_FORMATS,
)
from .config import document_export_output_path
from .download_reference import build_download_ref, build_download_url
from .download_resolver import DocumentDownloadResolver, ResolvedDownloadFile
from .errors import (
    DocumentExportError,
    DuplicateRendererFormatError,
    EmptyExportContentError,
    ExportDependencyMissingError,
    ExportOutputError,
    ExportRenderError,
    ExportTimeoutError,
    InvalidSourceFormatError,
    UnsupportedExportFormatError,
)
from .mime import guess_export_content_type
from .models import ExportOptions, ExportRequest, GeneratedDocumentFile

__all__ = [
    "ExportOptions",
    "ExportRequest",
    "GeneratedDocumentFile",
    "DocumentExportService",
    "DocumentExportError",
    "DuplicateRendererFormatError",
    "EmptyExportContentError",
    "ExportDependencyMissingError",
    "ExportOutputError",
    "ExportRenderError",
    "ExportTimeoutError",
    "InvalidSourceFormatError",
    "UnsupportedExportFormatError",
    "DocumentDownloadResolver",
    "ResolvedDownloadFile",
    "SUPPORTED_EXPORT_FORMATS",
    "SOURCE_FORMATS",
    "CONTENT_TYPES",
    "FILE_EXTENSIONS",
    "build_download_ref",
    "build_download_url",
    "document_export_output_path",
    "guess_export_content_type",
]


def __getattr__(name: str):
    if name == "DocumentExportService":
        from .service import DocumentExportService

        globals()[name] = DocumentExportService
        return DocumentExportService

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
