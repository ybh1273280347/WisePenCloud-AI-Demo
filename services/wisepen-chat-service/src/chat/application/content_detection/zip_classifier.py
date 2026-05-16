import io
import zipfile
from typing import List, Optional, Set

from chat.application.content_detection.models import (
    ContentDetection,
    ContentKind,
    DetectionConfidence,
)

_MAX_ZIP_ENTRIES = 4096
_MAX_ZIP_TOTAL_UNCOMPRESSED = 256 * 1024 * 1024
_MAX_ZIP_ENTRY_UNCOMPRESSED = 32 * 1024 * 1024
_MAX_ZIP_METADATA_FILE_SIZE = 1 * 1024 * 1024
_MAX_ZIP_COMPRESSION_RATIO = 100


def classify_zip_document(content: bytes) -> ContentDetection:
    try:
        with zipfile.ZipFile(io.BytesIO(content), mode="r") as zf:
            infos = zf.infolist()
            safety_error = validate_zip_document_metadata(infos)
            if safety_error:
                return unsupported_zip(safety_error)

            names = {info.filename for info in infos}

            epub = classify_epub(zf, names)
            if epub:
                return epub

            ods = classify_ods(zf, names)
            if ods:
                return ods

            ooxml = classify_ooxml(zf, names)
            if ooxml:
                return ooxml

            if is_odt(zf, names):
                return unsupported_zip("unsupported_odt")

    except zipfile.BadZipFile:
        return unsupported_zip("bad_zip")

    return unsupported_zip("zip_not_supported_document")


def validate_zip_document_metadata(infos: List[zipfile.ZipInfo]) -> Optional[str]:
    if len(infos) > _MAX_ZIP_ENTRIES:
        return "zip_too_many_entries"

    total_uncompressed = 0

    for info in infos:
        name = info.filename.replace("\\", "/")

        if not name:
            return "zip_empty_member_name"

        if name.startswith("/") or name.startswith("../") or "/../" in name:
            return "zip_unsafe_path"

        if is_nested_archive(name):
            return "zip_nested_archive"

        if info.file_size > _MAX_ZIP_ENTRY_UNCOMPRESSED:
            return "zip_entry_too_large"

        total_uncompressed += info.file_size
        if total_uncompressed > _MAX_ZIP_TOTAL_UNCOMPRESSED:
            return "zip_total_uncompressed_too_large"

        if info.compress_size > 0:
            ratio = info.file_size / info.compress_size
            if ratio > _MAX_ZIP_COMPRESSION_RATIO:
                return "zip_compression_ratio_too_high"

    return None


def read_zip_metadata_file(
    zf: zipfile.ZipFile,
    name: str,
) -> Optional[bytes]:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None

    if info.file_size > _MAX_ZIP_METADATA_FILE_SIZE:
        return None

    if info.compress_size > 0:
        ratio = info.file_size / info.compress_size
        if ratio > _MAX_ZIP_COMPRESSION_RATIO:
            return None

    return zf.read(name)


def classify_epub(
    zf: zipfile.ZipFile,
    names: Set[str],
) -> Optional[ContentDetection]:
    if "mimetype" not in names:
        return None

    content = read_zip_metadata_file(zf, "mimetype")
    if content is None:
        return None

    if content.strip() == b"application/epub+zip":
        return document_zip(".epub", "application/epub+zip", "epub_mimetype")

    return None


def classify_ods(
    zf: zipfile.ZipFile,
    names: Set[str],
) -> Optional[ContentDetection]:
    if "mimetype" not in names:
        return None

    content = read_zip_metadata_file(zf, "mimetype")
    if content is None:
        return None

    if content.strip() == b"application/vnd.oasis.opendocument.spreadsheet":
        return document_zip(
            ".ods", "application/vnd.oasis.opendocument.spreadsheet", "ods_mimetype"
        )

    return None


def is_odt(
    zf: zipfile.ZipFile,
    names: Set[str],
) -> bool:
    if "mimetype" not in names:
        return False

    content = read_zip_metadata_file(zf, "mimetype")
    if content is None:
        return False

    return content.strip() == b"application/vnd.oasis.opendocument.text"


def classify_ooxml(
    zf: zipfile.ZipFile,
    names: Set[str],
) -> Optional[ContentDetection]:
    lowered_names = {name.lower() for name in names}

    xml_bytes = read_zip_metadata_file(zf, "[Content_Types].xml")
    if xml_bytes:
        xml = xml_bytes[:_MAX_ZIP_METADATA_FILE_SIZE].decode("utf-8", errors="replace")

        if "application/vnd.ms-word.document.macroEnabled" in xml:
            return document_zip(
                ".docm",
                "application/vnd.ms-word.document.macroenabled.12",
                "ooxml_docm_content_types",
            )

        if "application/vnd.ms-powerpoint.presentation.macroEnabled" in xml:
            return document_zip(
                ".pptm",
                "application/vnd.ms-powerpoint.presentation.macroenabled.12",
                "ooxml_pptm_content_types",
            )

        if "application/vnd.ms-excel.sheet.macroEnabled" in xml:
            return document_zip(
                ".xlsm",
                "application/vnd.ms-excel.sheet.macroenabled.12",
                "ooxml_xlsm_content_types",
            )

        if "application/vnd.openxmlformats-officedocument.wordprocessingml" in xml:
            return document_zip(
                ".docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "ooxml_docx_content_types",
            )

        if "application/vnd.openxmlformats-officedocument.presentationml" in xml:
            return document_zip(
                ".pptx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "ooxml_pptx_content_types",
            )

        if "application/vnd.openxmlformats-officedocument.spreadsheetml" in xml:
            return document_zip(
                ".xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "ooxml_xlsx_content_types",
            )

    if "word/document.xml" in lowered_names:
        return document_zip(
            ".docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "ooxml_docx_path",
        )

    if "ppt/presentation.xml" in lowered_names:
        return document_zip(
            ".pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "ooxml_pptx_path",
        )

    if "xl/workbook.xml" in lowered_names:
        return document_zip(
            ".xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "ooxml_xlsx_path",
        )

    return None


def is_nested_archive(name: str) -> bool:
    return name.lower().endswith((".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz"))


def document_zip(extension: str, mime_type: str, reason: str) -> ContentDetection:
    return ContentDetection(
        ContentKind.DOCUMENT,
        mime_type,
        extension,
        DetectionConfidence.CONTAINER,
        reason,
        "zip_classifier",
    )


def unsupported_zip(reason: str) -> ContentDetection:
    return ContentDetection(
        kind=ContentKind.UNSUPPORTED_ARCHIVE,
        mime_type="application/zip",
        extension=".zip",
        confidence=DetectionConfidence.CONTAINER,
        reason=reason,
        detector="zip_classifier",
    )
