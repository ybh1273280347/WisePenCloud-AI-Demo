import asyncio
from typing import List, Optional

from chat.application.tools.common.content_detection import ContentDetection, ContentDetector, ContentKind, DetectionHints
from chat.application.tools.common.file_handoff import (
    FileHandoffError,
    TemporaryFileHandoffStore,
    is_allowed_handoff_suffix,
)
from chat.application.tools.common.ocr import OcrImageAdapter
from chat.application.tools.config import TOOL_RESULT_MAX_CHARS

from .errors import AttachmentReadError, AttachmentTextReadError
from .models import (
    AttachmentKind,
    AttachmentReadItem,
    AttachmentReadRequest,
    AttachmentReadResult,
    AttachmentStatus,
    ResolvedAttachment,
)
from .resolver import AttachmentResolver
from .text_reader import read_text_file

_DIRECT_TEXT_KINDS = {
    ContentKind.TEXT,
    ContentKind.HTML,
    ContentKind.JSON,
    ContentKind.XML,
}

_DOCUMENT_SUFFIX_ERROR = "Binary document detected but no document_parse-compatible suffix was available."
_HANDOFF_ERROR = "Failed to prepare temporary file_ref for document_parse."
_IMAGE_OCR_FAILED_ERROR = (
    "Image OCR failed. OCR text is unavailable. "
    "The image_ref is still available for visual analysis by the model."
)


def cache_and_format(**kwargs) -> str:
    from chat.application.tools.common.tool_content_store import cache_and_format as _cache_and_format

    return _cache_and_format(**kwargs)


class AttachmentReadService:
    def __init__(
        self,
        *,
        resolver: AttachmentResolver,
        content_detector: ContentDetector,
        file_handoff_store: TemporaryFileHandoffStore,
        ocr_image_adapter: OcrImageAdapter,
        max_concurrency: int = 4,
    ):
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        self._resolver = resolver
        self._content_detector = content_detector
        self._file_handoff_store = file_handoff_store
        self._ocr_image_adapter = ocr_image_adapter
        self._max_concurrency = max_concurrency

    async def read_attachments(
        self,
        request: AttachmentReadRequest,
    ) -> AttachmentReadResult:
        resolved = await self._resolver.resolve_many(
            session_id=request.session_id,
            user_id=request.user_id,
            attachment_refs=request.attachment_refs,
        )
        resolved_by_ref = {item.attachment_ref: item for item in resolved}
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def process_ref(attachment_ref: str) -> AttachmentReadItem:
            async with semaphore:
                resolved_item = resolved_by_ref.get(attachment_ref)
                if resolved_item is None:
                    return _error_item(
                        attachment_ref=attachment_ref,
                        file_name=attachment_ref,
                        mime_type="application/octet-stream",
                        size_bytes=0,
                        kind=AttachmentKind.UNSUPPORTED_BINARY,
                        error=f"Cannot resolve attachment '{attachment_ref}'.",
                    )
                return await self._process_attachment(
                    request=request,
                    resolved=resolved_item,
                )

        results = await asyncio.gather(
            *(process_ref(attachment_ref) for attachment_ref in request.attachment_refs),
            return_exceptions=True,
        )

        items: List[AttachmentReadItem] = []
        for index, result in enumerate(results):
            if isinstance(result, AttachmentReadItem):
                items.append(result)
                continue

            attachment_ref = request.attachment_refs[index]
            items.append(
                _error_item(
                    attachment_ref=attachment_ref,
                    file_name=attachment_ref,
                    mime_type="application/octet-stream",
                    size_bytes=0,
                    kind=AttachmentKind.UNSUPPORTED_BINARY,
                    error="Unexpected error while reading attachment.",
                )
            )

        return AttachmentReadResult(items=items)

    async def _process_attachment(
        self,
        *,
        request: AttachmentReadRequest,
        resolved: ResolvedAttachment,
    ) -> AttachmentReadItem:
        try:
            detection = await self._content_detector.detect_path(
                resolved.local_path,
                hints=DetectionHints(
                    filename=resolved.file_name,
                    declared_mime_type=resolved.mime_type,
                    content_type_header=None,
                    content_disposition=None,
                    source_uri=None,
                ),
            )

            if detection.kind in _DIRECT_TEXT_KINDS:
                return await self._read_direct_text(
                    request=request,
                    resolved=resolved,
                    mime_type=detection.mime_type or resolved.mime_type or "text/plain",
                )

            if detection.kind == ContentKind.IMAGE:
                return await self._handle_image(
                    request=request,
                    resolved=resolved,
                    detection=detection,
                )

            if detection.kind == ContentKind.DOCUMENT:
                return self._handoff_document(
                    request=request,
                    resolved=resolved,
                    mime_type=detection.mime_type or resolved.mime_type or "application/octet-stream",
                    extension=detection.extension,
                )

            return AttachmentReadItem(
                attachment_ref=resolved.attachment_ref,
                file_name=resolved.file_name,
                mime_type=detection.mime_type or resolved.mime_type or "application/octet-stream",
                size_bytes=resolved.size_bytes,
                kind=AttachmentKind.UNSUPPORTED_BINARY.value,
                status=AttachmentStatus.UNSUPPORTED.value,
                error=detection.reason or "Unsupported attachment type.",
            )

        except AttachmentReadError as exc:
            return _error_item_from_resolved(resolved, str(exc))
        except Exception:
            return _error_item_from_resolved(resolved, "Unexpected error while reading attachment.")

    async def _read_direct_text(
        self,
        *,
        request: AttachmentReadRequest,
        resolved: ResolvedAttachment,
        mime_type: str,
    ) -> AttachmentReadItem:
        try:
            text = await read_text_file(
                path=resolved.local_path,
                attachment_ref=resolved.attachment_ref,
            )
        except AttachmentTextReadError as exc:
            return _error_item_from_resolved(resolved, str(exc), kind=AttachmentKind.DIRECT_TEXT)

        content_block = cache_and_format(
            session_id=request.session_id,
            tool_name="attachment_read",
            source=resolved.attachment_ref,
            text=text,
            content_type=mime_type or "text/plain",
            metadata={
                "attachment_ref": resolved.attachment_ref,
                "file_name": resolved.file_name,
                "kind": AttachmentKind.DIRECT_TEXT.value,
            },
            limit=TOOL_RESULT_MAX_CHARS,
        )

        return AttachmentReadItem(
            attachment_ref=resolved.attachment_ref,
            file_name=resolved.file_name,
            mime_type=mime_type or "text/plain",
            size_bytes=resolved.size_bytes,
            kind=AttachmentKind.DIRECT_TEXT.value,
            status=AttachmentStatus.READ.value,
            content_block=content_block,
            preview=text[:1000],
        )

    def _handoff_document(
        self,
        *,
        request: AttachmentReadRequest,
        resolved: ResolvedAttachment,
        mime_type: str,
        extension: Optional[str],
    ) -> AttachmentReadItem:
        if not is_allowed_handoff_suffix(extension):
            return _error_item_from_resolved(
                resolved,
                _DOCUMENT_SUFFIX_ERROR,
                kind=AttachmentKind.BINARY_DOCUMENT,
                mime_type=mime_type,
            )

        try:
            handoff = self._file_handoff_store.copy_file(
                user_id=request.user_id,
                session_id=request.session_id,
                source_path=resolved.local_path,
                filename=resolved.file_name,
                canonical_suffix=extension or "",
                content_type=mime_type,
            )
        except FileHandoffError:
            return _error_item_from_resolved(
                resolved,
                _HANDOFF_ERROR,
                kind=AttachmentKind.BINARY_DOCUMENT,
                mime_type=mime_type,
            )

        return AttachmentReadItem(
            attachment_ref=resolved.attachment_ref,
            file_name=resolved.file_name,
            mime_type=mime_type,
            size_bytes=resolved.size_bytes,
            kind=AttachmentKind.BINARY_DOCUMENT.value,
            status=AttachmentStatus.DOCUMENT_PARSE_REQUIRED.value,
            file_ref=handoff.file_ref,
        )

    async def _handle_image(
        self,
        *,
        request: AttachmentReadRequest,
        resolved: ResolvedAttachment,
        detection: ContentDetection,
    ) -> AttachmentReadItem:
        try:
            ocr_text = await self._ocr_image_adapter.extract_text(resolved.local_path)
        except Exception:
            return AttachmentReadItem(
                attachment_ref=resolved.attachment_ref,
                file_name=resolved.file_name,
                mime_type=detection.mime_type or resolved.mime_type or "application/octet-stream",
                size_bytes=resolved.size_bytes,
                kind=AttachmentKind.IMAGE.value,
                status=AttachmentStatus.OCR_FAILED.value,
                image_ref=resolved.attachment_ref,
                image_available_for_vision=True,
                error=_IMAGE_OCR_FAILED_ERROR,
            )

        ocr_content_block = cache_and_format(
            session_id=request.session_id,
            tool_name="attachment_read",
            source=resolved.attachment_ref,
            text=ocr_text,
            content_type="text/plain",
            metadata={
                "attachment_ref": resolved.attachment_ref,
                "file_name": resolved.file_name,
                "kind": "image_ocr",
            },
            limit=TOOL_RESULT_MAX_CHARS,
        )

        return AttachmentReadItem(
            attachment_ref=resolved.attachment_ref,
            file_name=resolved.file_name,
            mime_type=detection.mime_type or resolved.mime_type or "application/octet-stream",
            size_bytes=resolved.size_bytes,
            kind=AttachmentKind.IMAGE.value,
            status=AttachmentStatus.OCR_COMPLETED.value,
            ocr_content_block=ocr_content_block,
            ocr_preview=ocr_text[:1000],
            image_ref=resolved.attachment_ref,
            image_available_for_vision=True,
        )


def _error_item_from_resolved(
    resolved: ResolvedAttachment,
    error: str,
    *,
    kind: AttachmentKind = AttachmentKind.UNSUPPORTED_BINARY,
    mime_type: Optional[str] = None,
) -> AttachmentReadItem:
    return _error_item(
        attachment_ref=resolved.attachment_ref,
        file_name=resolved.file_name,
        mime_type=mime_type or resolved.mime_type or "application/octet-stream",
        size_bytes=resolved.size_bytes,
        kind=kind,
        error=error,
    )


def _error_item(
    *,
    attachment_ref: str,
    file_name: str,
    mime_type: str,
    size_bytes: int,
    kind: AttachmentKind,
    error: str,
) -> AttachmentReadItem:
    return AttachmentReadItem(
        attachment_ref=attachment_ref,
        file_name=file_name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        kind=kind.value,
        status=AttachmentStatus.ERROR.value,
        error=error,
    )
