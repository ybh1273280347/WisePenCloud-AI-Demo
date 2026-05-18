from pathlib import Path, PurePosixPath
from typing import Optional

from chat.application.tools.common.content_detection.builtin_magic import (
    detect_builtin_magic,
    hint_extension,
    is_html_content,
    is_json_content,
    is_text_mime_hint,
    is_xml_content,
    is_zip_content,
    normalized_hint_mime,
)
from chat.application.tools.common.content_detection.magika_detector import MagikaDetector
from chat.application.tools.common.content_detection.models import (
    ContentDetection,
    ContentKind,
    DetectionConfidence,
    DetectionHints,
)
from chat.application.tools.common.content_detection.puremagic_detector import PureMagicDetector
from chat.application.tools.common.content_detection.text_probe import TextProbe
from chat.application.tools.common.content_detection.zip_classifier import classify_zip_document

_HEADER_BYTES = 64 * 1024

_HINT_DOCUMENT_MIME_TO_EXTENSION = {
    "application/pdf": ".pdf",
    "application/vnd.ms-excel": ".xls",
    "application/msword": ".doc",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-word.document.macroenabled.12": ".docm",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel.sheet.macroenabled.12": ".xlsm",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.ms-powerpoint.presentation.macroenabled.12": ".pptm",
    "application/epub+zip": ".epub",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
}

_HINT_DOCUMENT_EXTENSIONS = frozenset(_HINT_DOCUMENT_MIME_TO_EXTENSION.values())


class ContentDetector:
    def __init__(
        self,
        magika_detector: Optional[MagikaDetector] = None,
        puremagic_detector: Optional[PureMagicDetector] = None,
        text_probe: Optional[TextProbe] = None,
    ) -> None:
        self._magika = magika_detector or MagikaDetector()
        self._puremagic = puremagic_detector or PureMagicDetector()
        self._text_probe = text_probe or TextProbe()

    async def detect_path(
        self,
        path: Path,
        hints: Optional[DetectionHints] = None,
    ) -> ContentDetection:
        detection_hints = self._merge_path_hints(path, hints)
        with path.open("rb") as file:
            content = file.read()
        return await self._detect_content(
            content=content,
            header=content[:_HEADER_BYTES],
            hints=detection_hints,
            path=path,
        )

    async def detect_bytes(
        self,
        content: bytes,
        hints: Optional[DetectionHints] = None,
    ) -> ContentDetection:
        detection_hints = hints or DetectionHints()
        return await self._detect_content(
            content=content,
            header=content[:_HEADER_BYTES],
            hints=detection_hints,
            path=None,
        )

    async def _detect_content(
        self,
        content: bytes,
        header: bytes,
        hints: DetectionHints,
        path: Optional[Path],
    ) -> ContentDetection:
        builtin = detect_builtin_magic(header, content, hints)
        if builtin:
            return builtin

        if is_zip_content(header):
            return classify_zip_document(content)

        magika = await self._detect_with_magika(content, path)
        if magika:
            return magika

        puremagic = await self._detect_with_puremagic(content, path)
        if puremagic:
            return puremagic

        fallback = self._legacy_fallback(content, header, hints)
        if fallback:
            return fallback

        text = self._text_probe.detect_bytes(content)
        if text:
            return text

        hint = self._hint_fallback(hints)
        if hint:
            return hint

        return ContentDetection(
            kind=ContentKind.UNSUPPORTED_BINARY,
            mime_type="application/octet-stream",
            extension=None,
            confidence=DetectionConfidence.FALLBACK,
            reason="unknown_binary",
            detector="content_detector",
        )

    async def _detect_with_magika(
        self,
        content: bytes,
        path: Optional[Path],
    ) -> Optional[ContentDetection]:
        if path:
            return await self._magika.detect_path(path)
        return await self._magika.detect_bytes(content)

    async def _detect_with_puremagic(
        self,
        content: bytes,
        path: Optional[Path],
    ) -> Optional[ContentDetection]:
        if path:
            return await self._puremagic.detect_path(path)
        return await self._puremagic.detect_bytes(content)

    def _legacy_fallback(
        self,
        content: bytes,
        header: bytes,
        hints: DetectionHints,
    ) -> Optional[ContentDetection]:
        if is_html_content(header):
            return ContentDetection(
                ContentKind.HTML,
                "text/html",
                ".html",
                DetectionConfidence.FALLBACK,
                "legacy_html_payload",
                "legacy_fallback",
            )

        if is_json_content(header, hints):
            return ContentDetection(
                ContentKind.JSON,
                "application/json",
                ".json",
                DetectionConfidence.FALLBACK,
                "legacy_json_payload",
                "legacy_fallback",
            )

        if is_xml_content(header, hints):
            return ContentDetection(
                ContentKind.XML,
                "application/xml",
                ".xml",
                DetectionConfidence.FALLBACK,
                "legacy_xml_payload",
                "legacy_fallback",
            )

        if is_text_mime_hint(hints):
            text = self._text_probe.detect_bytes(content)
            if text:
                return ContentDetection(
                    text.kind,
                    normalized_hint_mime(hints) or text.mime_type,
                    text.extension,
                    DetectionConfidence.FALLBACK,
                    "legacy_text_mime",
                    "legacy_fallback",
                )

        return None

    def _hint_fallback(self, hints: DetectionHints) -> Optional[ContentDetection]:
        mime_type = normalized_hint_mime(hints)
        if mime_type in _HINT_DOCUMENT_MIME_TO_EXTENSION:
            return ContentDetection(
                ContentKind.DOCUMENT,
                mime_type,
                _HINT_DOCUMENT_MIME_TO_EXTENSION[mime_type],
                DetectionConfidence.HINT,
                "document_mime_hint",
                "hints",
            )

        extension = hint_extension(hints)
        if extension in _HINT_DOCUMENT_EXTENSIONS:
            return ContentDetection(
                ContentKind.DOCUMENT,
                mime_type or "application/octet-stream",
                extension,
                DetectionConfidence.HINT,
                "document_extension_hint",
                "hints",
            )

        if mime_type.startswith("image/"):
            return ContentDetection(
                ContentKind.IMAGE,
                mime_type,
                extension or None,
                DetectionConfidence.HINT,
                "image_mime_hint",
                "hints",
            )

        if mime_type.startswith("audio/") or mime_type.startswith("video/"):
            return ContentDetection(
                ContentKind.UNSUPPORTED_MEDIA,
                mime_type,
                extension or None,
                DetectionConfidence.HINT,
                "media_mime_hint",
                "hints",
            )

        return None

    def _merge_path_hints(
        self, path: Path, hints: Optional[DetectionHints]
    ) -> DetectionHints:
        if hints is None:
            return DetectionHints(filename=path.name, source_uri=str(path))

        filename = hints.filename or path.name
        source_uri = hints.source_uri or str(path)
        if not filename and source_uri:
            filename = PurePosixPath(source_uri.replace("\\", "/")).name

        return DetectionHints(
            filename=filename,
            declared_mime_type=hints.declared_mime_type,
            content_type_header=hints.content_type_header,
            content_disposition=hints.content_disposition,
            source_uri=source_uri,
        )
