from pathlib import Path


DOCUMENT_TYPE_PDF = "pdf"
DOCUMENT_TYPE_DOCX = "docx"
DOCUMENT_TYPE_PPTX = "pptx"
DOCUMENT_TYPE_EPUB = "epub"
DOCUMENT_TYPE_SPREADSHEET = "spreadsheet"

PDF_SUFFIXES = {".pdf"}
OFFICE_SUFFIXES = {".docx", ".pptx"}
EPUB_SUFFIXES = {".epub"}
SPREADSHEET_SUFFIXES = {".xlsx", ".xls", ".ods"}

_DOCUMENT_TYPE_BY_SUFFIX = {
    ".pdf": DOCUMENT_TYPE_PDF,
    ".docx": DOCUMENT_TYPE_DOCX,
    ".pptx": DOCUMENT_TYPE_PPTX,
    ".epub": DOCUMENT_TYPE_EPUB,
    ".xlsx": DOCUMENT_TYPE_SPREADSHEET,
    ".xls": DOCUMENT_TYPE_SPREADSHEET,
    ".ods": DOCUMENT_TYPE_SPREADSHEET,
}

_UNSUPPORTED_GUIDANCE = {
    ".html": ".html pages should be handled by web_fetch.",
    ".htm": ".htm pages should be handled by web_fetch.",
    ".txt": ".txt should be read directly by the caller, not document_parse.",
    ".md": ".md should be read directly by the caller, not document_parse.",
    ".markdown": ".markdown should be read directly by the caller, not document_parse.",
    ".csv": ".csv should be read directly by the caller, not document_parse.",
    ".json": ".json should be read directly by the caller, not document_parse.",
    ".xml": ".xml should be read directly by the caller, not document_parse.",
    ".png": ".png images should be handled by the multimodal model, not document_parse.",
    ".jpg": ".jpg images should be handled by the multimodal model, not document_parse.",
    ".jpeg": ".jpeg images should be handled by the multimodal model, not document_parse.",
    ".webp": ".webp images should be handled by the multimodal model, not document_parse.",
    ".tiff": ".tiff images should be handled by the multimodal model, not document_parse.",
    ".tif": ".tif images should be handled by the multimodal model, not document_parse.",
    ".bmp": ".bmp images should be handled by the multimodal model, not document_parse.",
    ".gif": ".gif images should be handled by the multimodal model, not document_parse.",
    ".mp3": ".mp3 audio should be handled by the multimodal model, not document_parse.",
    ".wav": ".wav audio should be handled by the multimodal model, not document_parse.",
    ".mp4": ".mp4 video should be handled by the multimodal model, not document_parse.",
    ".mov": ".mov video should be handled by the multimodal model, not document_parse.",
}

__all__ = [
    "DOCUMENT_TYPE_PDF",
    "DOCUMENT_TYPE_DOCX",
    "DOCUMENT_TYPE_PPTX",
    "DOCUMENT_TYPE_EPUB",
    "DOCUMENT_TYPE_SPREADSHEET",
    "PDF_SUFFIXES",
    "OFFICE_SUFFIXES",
    "EPUB_SUFFIXES",
    "SPREADSHEET_SUFFIXES",
    "detect_document_type_by_suffix",
]


def detect_document_type_by_suffix(path: Path) -> str:
    suffix = path.suffix.lower()

    if not suffix:
        raise ValueError("Unsupported document type: missing file suffix.")

    document_type = _DOCUMENT_TYPE_BY_SUFFIX.get(suffix)
    if document_type:
        return document_type

    guidance = _UNSUPPORTED_GUIDANCE.get(suffix, "")
    raise ValueError(f"不支持的文件类型: {suffix}。{guidance}".rstrip())