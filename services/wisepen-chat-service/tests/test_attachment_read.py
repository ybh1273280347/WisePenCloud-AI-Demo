import asyncio
from pathlib import Path
from typing import Optional, List, Any

import pytest

from chat.application.attachment_read import (
    AttachmentKind,
    AttachmentReadRequest,
    AttachmentReadService,
    AttachmentStatus,
    ResolvedAttachment,
    StubAttachmentResolver,
)
from chat.application.attachment_read.formatting import format_attachment_read_result
from chat.application.attachment_read.models import AttachmentReadItem, AttachmentReadResult
from chat.application.attachment_read.text_reader import ATTACHMENT_TEXT_MAX_BYTES, read_text_file
from chat.application.content_detection import ContentDetection, ContentKind, DetectionConfidence, DetectionHints
from chat.application.file_handoff import TemporaryFileHandoffStore

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def stub_cache_and_format(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("chat.application.attachment_read.service.cache_and_format", lambda **kwargs: "[cached block]")


async def test_service_calls_resolver_with_context_and_preserves_order(tmp_path: Path) -> None:
    files = _write_files(tmp_path, {"b.txt": b"beta", "a.txt": b"alpha"})
    resolver = RecordingResolver(
        [
            _resolved("att_b", "b.txt", files["b.txt"]),
            _resolved("att_a", "a.txt", files["a.txt"]),
        ]
    )
    service = _service(
        resolver=resolver,
        detector=MappingDetector(
            {
                "a.txt": _detection(ContentKind.TEXT, "text/plain", ".txt"),
                "b.txt": _detection(ContentKind.TEXT, "text/plain", ".txt"),
            }
        ),
        root=tmp_path / "handoff",
    )

    result = await service.read_attachments(
        AttachmentReadRequest(
            session_id="s1",
            user_id="u1",
            attachment_refs=["att_a", "att_b"],
        )
    )

    assert resolver.calls == [("s1", "u1", ["att_a", "att_b"])]
    assert [item.attachment_ref for item in result.items] == ["att_a", "att_b"]
    assert all(item.status == AttachmentStatus.READ.value for item in result.items)


@pytest.mark.parametrize("kind", [ContentKind.TEXT, ContentKind.HTML, ContentKind.JSON, ContentKind.XML])
async def test_direct_text_kinds_are_read_and_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: ContentKind) -> None:
    file_path = tmp_path / "notes.txt"
    file_path.write_text("hello attachment", encoding="utf-8")
    cache_calls = []

    def fake_cache_and_format(**kwargs):
        cache_calls.append(kwargs)
        return "[cached block]"

    monkeypatch.setattr("chat.application.attachment_read.service.cache_and_format", fake_cache_and_format)
    service = _service(
        resolver=RecordingResolver([_resolved("att_text", "notes.txt", file_path)]),
        detector=StaticDetector(_detection(kind, "text/plain", ".txt")),
        root=tmp_path / "handoff",
    )

    result = await service.read_attachments(_request(["att_text"]))
    item = result.items[0]

    assert item.kind == AttachmentKind.DIRECT_TEXT.value
    assert item.status == AttachmentStatus.READ.value
    assert item.content_block == "[cached block]"
    assert item.preview == "hello attachment"
    assert len(item.preview or "") <= 1000
    assert cache_calls[0]["tool_name"] == "attachment_read"


async def test_text_read_failure_becomes_item_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"
    service = _service(
        resolver=RecordingResolver([_resolved("att_missing", "missing.txt", missing)]),
        detector=StaticDetector(_detection(ContentKind.TEXT, "text/plain", ".txt")),
        root=tmp_path / "handoff",
    )

    result = await service.read_attachments(_request(["att_missing"]))

    assert result.items[0].status == AttachmentStatus.ERROR.value
    assert "Text read failed" in (result.items[0].error or "")
    assert str(missing) not in (result.items[0].error or "")


async def test_document_creates_document_parse_file_ref(tmp_path: Path) -> None:
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-1.7")
    service = _service(
        resolver=RecordingResolver([_resolved("att_doc", "report.exe.pdf", source, "application/pdf")]),
        detector=StaticDetector(_detection(ContentKind.DOCUMENT, "application/pdf", ".pdf")),
        root=tmp_path / "handoff",
    )

    result = await service.read_attachments(_request(["att_doc"]))
    item = result.items[0]

    assert item.kind == AttachmentKind.BINARY_DOCUMENT.value
    assert item.status == AttachmentStatus.DOCUMENT_PARSE_REQUIRED.value
    assert item.file_ref
    assert item.file_ref != item.attachment_ref
    assert not item.file_ref.startswith("cnt_")
    assert Path(item.file_ref).suffix == ".pdf"
    assert Path(item.file_ref).name[:16].isalnum()


async def test_mixed_text_and_document_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text_file = tmp_path / "notes.txt"
    doc_file = tmp_path / "report.pdf"
    text_file.write_text("notes", encoding="utf-8")
    doc_file.write_bytes(b"%PDF-1.7")
    monkeypatch.setattr("chat.application.attachment_read.service.cache_and_format", lambda **kwargs: "[cached]")
    service = _service(
        resolver=RecordingResolver(
            [
                _resolved("att_text", "notes.txt", text_file),
                _resolved("att_doc", "report.pdf", doc_file, "application/pdf"),
            ]
        ),
        detector=MappingDetector(
            {
                "notes.txt": _detection(ContentKind.TEXT, "text/plain", ".txt"),
                "report.pdf": _detection(ContentKind.DOCUMENT, "application/pdf", ".pdf"),
            }
        ),
        root=tmp_path / "handoff",
    )

    result = await service.read_attachments(_request(["att_text", "att_doc"]))

    assert result.items[0].status == AttachmentStatus.READ.value
    assert result.items[1].status == AttachmentStatus.DOCUMENT_PARSE_REQUIRED.value


async def test_image_ocr_success_is_cached_and_returns_image_ref(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image = tmp_path / "screenshot.png"
    image.write_bytes(b"\x89PNG\r\n")
    cache_calls = []

    def fake_cache_and_format(**kwargs):
        cache_calls.append(kwargs)
        return "[ocr cached]"

    monkeypatch.setattr("chat.application.attachment_read.service.cache_and_format", fake_cache_and_format)
    ocr_adapter = FakeOcrAdapter(text="hello from image")
    service = _service(
        resolver=RecordingResolver([_resolved("att_img", "screenshot.png", image, "image/png")]),
        detector=StaticDetector(_detection(ContentKind.IMAGE, "image/png", ".png")),
        root=tmp_path / "handoff",
        ocr_adapter=ocr_adapter,
    )

    result = await service.read_attachments(_request(["att_img"]))
    item = result.items[0]

    assert ocr_adapter.calls == [image]
    assert item.kind == AttachmentKind.IMAGE.value
    assert item.status == AttachmentStatus.OCR_COMPLETED.value
    assert item.ocr_content_block == "[ocr cached]"
    assert item.ocr_preview == "hello from image"
    assert item.image_ref == "att_img"
    assert item.image_available_for_vision is True
    assert item.content_block is None
    assert str(image) not in format_attachment_read_result(result)
    assert cache_calls[0]["tool_name"] == "attachment_read"
    assert cache_calls[0]["source"] == "att_img"
    assert cache_calls[0]["metadata"]["kind"] == "image_ocr"


async def test_image_ocr_failure_returns_image_ref_without_batch_error(tmp_path: Path) -> None:
    image = tmp_path / "screenshot.png"
    image.write_bytes(b"\x89PNG\r\n")
    service = _service(
        resolver=RecordingResolver([_resolved("att_img", "screenshot.png", image, "image/png")]),
        detector=StaticDetector(_detection(ContentKind.IMAGE, "image/png", ".png")),
        root=tmp_path / "handoff",
        ocr_adapter=FakeOcrAdapter(error=RuntimeError(f"failed: {image}")),
    )

    result = await service.read_attachments(_request(["att_img"]))
    item = result.items[0]

    assert item.status == AttachmentStatus.OCR_FAILED.value
    assert item.image_ref == "att_img"
    assert item.image_available_for_vision is True
    assert item.error == (
        "Image OCR failed. OCR text is unavailable. "
        "The image_ref is still available for visual analysis by the model."
    )
    assert item.error is not None
    assert str(image) not in item.error


async def test_image_ocr_failure_does_not_affect_other_attachments(tmp_path: Path) -> None:
    text_file = tmp_path / "notes.txt"
    image = tmp_path / "screenshot.png"
    doc_file = tmp_path / "report.pdf"
    text_file.write_text("notes", encoding="utf-8")
    image.write_bytes(b"\x89PNG\r\n")
    doc_file.write_bytes(b"%PDF-1.7")
    service = _service(
        resolver=RecordingResolver(
            [
                _resolved("att_text", "notes.txt", text_file),
                _resolved("att_img", "screenshot.png", image, "image/png"),
                _resolved("att_doc", "report.pdf", doc_file, "application/pdf"),
            ]
        ),
        detector=MappingDetector(
            {
                "notes.txt": _detection(ContentKind.TEXT, "text/plain", ".txt"),
                "screenshot.png": _detection(ContentKind.IMAGE, "image/png", ".png"),
                "report.pdf": _detection(ContentKind.DOCUMENT, "application/pdf", ".pdf"),
            }
        ),
        root=tmp_path / "handoff",
        ocr_adapter=FakeOcrAdapter(error=RuntimeError("ocr failed")),
    )

    result = await service.read_attachments(_request(["att_text", "att_img", "att_doc"]))

    assert result.items[0].status == AttachmentStatus.READ.value
    assert result.items[1].status == AttachmentStatus.OCR_FAILED.value
    assert result.items[1].image_ref == "att_img"
    assert result.items[2].status == AttachmentStatus.DOCUMENT_PARSE_REQUIRED.value


async def test_document_without_supported_suffix_is_error(tmp_path: Path) -> None:
    source = tmp_path / "report.bin"
    source.write_bytes(b"doc")
    service = _service(
        resolver=RecordingResolver([_resolved("att_doc", "report.bin", source)]),
        detector=StaticDetector(_detection(ContentKind.DOCUMENT, "application/pdf", None)),
        root=tmp_path / "handoff",
    )

    result = await service.read_attachments(_request(["att_doc"]))

    assert result.items[0].status == AttachmentStatus.ERROR.value
    assert result.items[0].error == "Binary document detected but no document_parse-compatible suffix was available."


async def test_file_handoff_failure_is_error(tmp_path: Path) -> None:
    source = tmp_path / "missing.pdf"
    service = _service(
        resolver=RecordingResolver([_resolved("att_doc", "missing.pdf", source)]),
        detector=StaticDetector(_detection(ContentKind.DOCUMENT, "application/pdf", ".pdf")),
        root=tmp_path / "handoff",
    )

    result = await service.read_attachments(_request(["att_doc"]))

    assert result.items[0].status == AttachmentStatus.ERROR.value
    assert result.items[0].error == "Failed to prepare temporary file_ref for document_parse."


@pytest.mark.parametrize(
    ("kind", "expected_status", "expected_kind"),
    [
        (ContentKind.UNSUPPORTED_ARCHIVE, AttachmentStatus.UNSUPPORTED.value, AttachmentKind.UNSUPPORTED_BINARY.value),
        (ContentKind.UNSUPPORTED_MEDIA, AttachmentStatus.UNSUPPORTED.value, AttachmentKind.UNSUPPORTED_BINARY.value),
        (ContentKind.UNSUPPORTED_BINARY, AttachmentStatus.UNSUPPORTED.value, AttachmentKind.UNSUPPORTED_BINARY.value),
    ],
)
async def test_deferred_and_unsupported_kinds(
    tmp_path: Path,
    kind: ContentKind,
    expected_status: str,
    expected_kind: str,
) -> None:
    source = tmp_path / "file.bin"
    source.write_bytes(b"bin")
    service = _service(
        resolver=RecordingResolver([_resolved("att", "file.bin", source)]),
        detector=StaticDetector(_detection(kind, "application/octet-stream", None)),
        root=tmp_path / "handoff",
    )

    result = await service.read_attachments(_request(["att"]))

    assert result.items[0].status == expected_status
    assert result.items[0].kind == expected_kind


async def test_single_attachment_failure_does_not_abort_batch(tmp_path: Path) -> None:
    ok_file = tmp_path / "ok.txt"
    ok_file.write_text("ok", encoding="utf-8")
    service = _service(
        resolver=RecordingResolver([_resolved("ok", "ok.txt", ok_file)]),
        detector=MappingDetector({"ok": _detection(ContentKind.TEXT, "text/plain", ".txt")}),
        root=tmp_path / "handoff",
    )

    result = await service.read_attachments(_request(["missing", "ok"]))

    assert result.items[0].status == AttachmentStatus.ERROR.value
    assert result.items[1].status == AttachmentStatus.READ.value


async def test_max_concurrency_is_enforced(tmp_path: Path) -> None:
    files = _write_files(tmp_path, {f"{index}.txt": b"text" for index in range(4)})
    detector = DelayedDetector(_detection(ContentKind.TEXT, "text/plain", ".txt"))
    service = _service(
        resolver=RecordingResolver(
            [_resolved(f"att_{index}", f"{index}.txt", files[f"{index}.txt"]) for index in range(4)]
        ),
        detector=detector,
        root=tmp_path / "handoff",
        max_concurrency=2,
    )

    await service.read_attachments(_request([f"att_{index}" for index in range(4)]))

    assert detector.max_seen <= 2


async def test_formatting_collects_document_refs_and_instructions() -> None:
    result = AttachmentReadResult(
        items=[
            AttachmentReadItem(
                attachment_ref="att_doc",
                file_name="report.pdf",
                mime_type="application/pdf",
                size_bytes=3,
                kind=AttachmentKind.BINARY_DOCUMENT.value,
                status=AttachmentStatus.DOCUMENT_PARSE_REQUIRED.value,
                file_ref="/tmp/wisepen-file-handoff/session/0123456789abcdef-report.pdf",
            ),
            AttachmentReadItem(
                attachment_ref="att_img",
                file_name="image.png",
                mime_type="image/png",
                size_bytes=2,
                kind=AttachmentKind.IMAGE.value,
                status=AttachmentStatus.OCR_COMPLETED.value,
                ocr_content_block="[ocr cached]",
                ocr_preview="text from image",
                image_ref="att_img",
                image_available_for_vision=True,
            ),
            AttachmentReadItem(
                attachment_ref="att_img_fail",
                file_name="failed.png",
                mime_type="image/png",
                size_bytes=2,
                kind=AttachmentKind.IMAGE.value,
                status=AttachmentStatus.OCR_FAILED.value,
                image_ref="att_img_fail",
                image_available_for_vision=True,
                error=(
                    "Image OCR failed. OCR text is unavailable. "
                    "The image_ref is still available for visual analysis by the model."
                ),
            ),
        ]
    )

    output = format_attachment_read_result(result)

    assert "Document parse required:" in output
    assert "- /tmp/wisepen-file-handoff/session/0123456789abcdef-report.pdf" in output
    assert "Call document_parse once with all file_refs" in output
    assert "ocr_content:" in output
    assert "[ocr cached]" in output
    assert "ocr_preview:" in output
    assert "text from image" in output
    assert "image_ref: att_img" in output
    assert "OCR text is only text extracted from the image. It does not replace visual analysis of the image." in output
    assert "image_ref: att_img_fail" in output
    assert "Image OCR failed. OCR text is unavailable." in output
    assert "OCR failure does not mean the image is unreadable." in output
    assert "For images, OCR is always attempted before returning image_ref." in output
    assert "OCR text is only extracted text from the image. It does not replace visual analysis." in output
    assert "If OCR failed, do not treat the image as unreadable. Use image_ref for visual analysis." in output
    assert "local_path" not in output
    assert "file_path" not in output


async def test_text_reader_encodings_and_truncation(tmp_path: Path) -> None:
    utf8 = tmp_path / "utf8.txt"
    utf8_sig = tmp_path / "utf8_sig.txt"
    gbk = tmp_path / "gbk.txt"
    latin1 = tmp_path / "latin1.txt"
    huge = tmp_path / "huge.txt"
    utf8.write_bytes("hello".encode("utf-8"))
    utf8_sig.write_bytes("hello".encode("utf-8-sig"))
    gbk.write_bytes("你好".encode("gbk"))
    latin1.write_bytes("café".encode("latin-1"))
    huge.write_bytes(b"a" * (ATTACHMENT_TEXT_MAX_BYTES + 2))

    assert await read_text_file(path=utf8, attachment_ref="a") == "hello"
    assert await read_text_file(path=utf8_sig, attachment_ref="a") == "hello"
    assert await read_text_file(path=gbk, attachment_ref="a") == "你好"
    assert await read_text_file(path=latin1, attachment_ref="a") == "café"
    assert "Content truncated" in await read_text_file(path=huge, attachment_ref="a")


async def test_stub_resolver_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        await StubAttachmentResolver().resolve_many(
            session_id="s",
            user_id="u",
            attachment_refs=["att"],
        )


class RecordingResolver:
    def __init__(self, resolved: List[ResolvedAttachment]) -> None:
        self.resolved = resolved
        self.calls: List[Tuple[str, str, List[str]]] = []

    async def resolve_many(
        self,
        *,
        session_id: str,
        user_id: str,
        attachment_refs: List[str],
    ) -> List[ResolvedAttachment]:
        self.calls.append((session_id, user_id, list(attachment_refs)))
        return self.resolved


class StaticDetector:
    def __init__(self, detection: ContentDetection) -> None:
        self.detection = detection

    async def detect_path(
        self,
        path: Path,
        hints: Optional[DetectionHints] = None,
    ) -> ContentDetection:
        return self.detection


class MappingDetector:
    def __init__(self, detections: Dict[str, ContentDetection]) -> None:
        self.detections = detections

    async def detect_path(
        self,
        path: Path,
        hints: Optional[DetectionHints] = None,
    ) -> ContentDetection:
        if hints is not None and hints.filename in self.detections:
            return self.detections[hints.filename]
        if path.name in self.detections:
            return self.detections[path.name]
        if path.stem in self.detections:
            return self.detections[path.stem]
        return next(iter(self.detections.values()))


class DelayedDetector(StaticDetector):
    def __init__(self, detection: ContentDetection) -> None:
        super().__init__(detection)
        self.current = 0
        self.max_seen = 0

    async def detect_path(
        self,
        path: Path,
        hints: Optional[DetectionHints] = None,
    ) -> ContentDetection:
        self.current += 1
        self.max_seen = max(self.max_seen, self.current)
        await asyncio.sleep(0.01)
        self.current -= 1
        return self.detection


class FakeOcrAdapter:
    def __init__(self, *, text: str = "ocr text", error: Optional[Exception] = None) -> None:
        self.text = text
        self.error = error
        self.calls: List[Path] = []

    async def extract_text(self, image_path: Path) -> str:
        self.calls.append(image_path)
        if self.error is not None:
            raise self.error
        return self.text


def _service(
    *,
    resolver,
    detector,
    root: Path,
    ocr_adapter=None,
    max_concurrency: int = 4,
) -> AttachmentReadService:
    return AttachmentReadService(
        resolver=resolver,
        content_detector=detector,
        file_handoff_store=TemporaryFileHandoffStore(root_dir=root, ttl_seconds=3600),
        ocr_image_adapter=ocr_adapter or FakeOcrAdapter(),
        max_concurrency=max_concurrency,
    )


def _request(attachment_refs: List[str]) -> AttachmentReadRequest:
    return AttachmentReadRequest(session_id="session", user_id="user", attachment_refs=attachment_refs)


def _resolved(
    attachment_ref: str,
    file_name: str,
    path: Path,
    mime_type: str = "text/plain",
) -> ResolvedAttachment:
    return ResolvedAttachment(
        attachment_ref=attachment_ref,
        file_name=file_name,
        mime_type=mime_type,
        size_bytes=path.stat().st_size if path.exists() else 0,
        local_path=path,
    )


def _detection(kind: ContentKind, mime_type: str, extension: Optional[str]) -> ContentDetection:
    return ContentDetection(
        kind=kind,
        mime_type=mime_type,
        extension=extension,
        confidence=DetectionConfidence.MAGIC,
        reason="test",
        detector="fake",
    )


def _write_files(tmp_path: Path, files: dict[str, bytes]) -> dict[str, Path]:
    paths = {}
    for name, content in files.items():
        path = tmp_path / name
        path.write_bytes(content)
        paths[name] = path
    return paths
