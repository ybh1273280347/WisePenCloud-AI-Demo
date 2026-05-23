import asyncio
from pathlib import Path
from typing import List, Optional

import pytest

from chat.application.document_export import ExportOptions, GeneratedDocumentFile
from chat.application.tools.services.document_convert.errors import (
    DocumentConvertError,
    DocumentExportError,
    InvalidDocumentRefError,
    DocumentParseError,
    UnreadableDocumentRefError,
)
from chat.application.tools.services.document_convert.service import (
    DocumentConvertService,
    normalize_convert_request,
)
from chat.application.tools.services.document_file import DocumentTempFileResolver
from chat.application.tools.services.document_parse.models import DocumentParseResult


def test_md_file_ref_to_docx_uses_text_export(tmp_path: Path) -> None:
    async def run() -> None:
        source = _scoped_file(tmp_path, "user", "session", "input.md", "# RAG\n\ncontent")
        service = _service(tmp_path)

        generated = await service.convert_document(
            user_id="user",
            session_id="session",
            file_ref=str(source),
            target_format="docx",
            file_name="RAG___v1.docx",
        )

        assert generated.target_format == "docx"
        assert service.parse_service.calls == []
        assert service.export_service.export_content_calls == [
            ("# RAG\n\ncontent", "docx", "markdown", "RAG___v1.docx")
        ]
        assert service.export_service.export_content_options[0].title is None

    asyncio.run(run())


def test_file_name_does_not_determine_source_format(tmp_path: Path) -> None:
    async def run() -> None:
        source = _scoped_file(tmp_path, "user", "session", "input.md", "# source")
        service = _service(tmp_path)

        await service.convert_document(
            user_id="user",
            session_id="session",
            file_ref=str(source),
            target_format="docx",
            file_name="output.docx",
        )

        assert service.parse_service.calls == []
        assert service.export_service.export_content_calls[0][2] == "markdown"

    asyncio.run(run())


def test_output_file_name_suffix_conflict_fails(tmp_path: Path) -> None:
    async def run() -> None:
        source = _scoped_file(tmp_path, "user", "session", "input.md", "# source")
        service = _service(tmp_path)

        with pytest.raises(DocumentConvertError):
            await service.convert_document(
                user_id="user",
                session_id="session",
                file_ref=str(source),
                target_format="docx",
                file_name="output.pdf",
            )

    asyncio.run(run())


def test_text_file_ref_to_docx_uses_plain_text_export(tmp_path: Path) -> None:
    async def run() -> None:
        source = _scoped_file(tmp_path, "user", "session", "notes.txt", "plain notes")
        service = _service(tmp_path)

        await service.convert_document(
            user_id="user",
            session_id="session",
            file_ref=str(source),
            target_format="docx",
        )

        assert service.parse_service.calls == []
        assert service.export_service.export_content_calls == [
            ("plain notes", "docx", "plain_text", None)
        ]

    asyncio.run(run())


def test_pdf_file_ref_to_docx_uses_parse_export(tmp_path: Path) -> None:
    async def run() -> None:
        source = _scoped_binary(tmp_path, "user", "session", "paper.pdf", b"%PDF-1.7")
        service = _service(tmp_path)

        await service.convert_document(
            user_id="user",
            session_id="session",
            file_ref=str(source),
            target_format="docx",
        )

        assert service.parse_service.calls == [source.resolve(strict=False)]
        assert service.export_service.export_markdown_calls == [
            ("# parsed", "docx", None)
        ]

    asyncio.run(run())


def test_title_is_passed_to_export_options(tmp_path: Path) -> None:
    async def run() -> None:
        source = _scoped_file(tmp_path, "user", "session", "input.md", "# source")
        service = _service(tmp_path)

        await service.convert_document(
            user_id="user",
            session_id="session",
            file_ref=str(source),
            target_format="pdf",
            title="Quarterly Report",
        )

        assert service.export_service.export_content_options[0].title == "Quarterly Report"

    asyncio.run(run())


def test_empty_title_is_rejected() -> None:
    with pytest.raises(DocumentConvertError):
        normalize_convert_request(
            file_ref="input.md",
            file_name=None,
            target_format="pdf",
            user_id="user",
            session_id="session",
            title="",
        )


def test_reference_docx_file_ref_is_resolved_and_passed_to_export(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        source = _scoped_file(tmp_path, "user", "session", "input.md", "# source")
        reference = _scoped_binary(
            tmp_path,
            "user",
            "session",
            "reference.docx",
            b"docx",
        )
        service = _service(tmp_path)

        await service.convert_document(
            user_id="user",
            session_id="session",
            file_ref=str(source),
            target_format="docx",
            reference_docx_file_ref=str(reference),
        )

        assert service.export_service.export_content_options[0].reference_docx == reference.resolve()

    asyncio.run(run())


@pytest.mark.parametrize("target_format", ["pdf", "html", "markdown", "txt"])
def test_reference_docx_file_ref_is_rejected_for_non_docx_targets(
    target_format: str,
) -> None:
    with pytest.raises(DocumentConvertError):
        normalize_convert_request(
            file_ref="input.md",
            file_name=None,
            target_format=target_format,
            user_id="user",
            session_id="session",
            reference_docx_file_ref="reference.docx",
        )


def test_reference_docx_invalid_ref_maps_to_invalid_document_ref(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        source = _scoped_file(tmp_path, "user", "session", "input.md", "# source")
        outside = tmp_path / "outside.docx"
        outside.write_bytes(b"docx")
        service = _service(tmp_path)

        with pytest.raises(InvalidDocumentRefError):
            await service.convert_document(
                user_id="user",
                session_id="session",
                file_ref=str(source),
                target_format="docx",
                reference_docx_file_ref=str(outside),
            )

    asyncio.run(run())


def test_reference_docx_unreadable_ref_maps_to_unreadable_document_ref(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        source = _scoped_file(tmp_path, "user", "session", "input.md", "# source")
        missing = tmp_path / "user" / "session" / "missing.docx"
        service = _service(tmp_path)

        with pytest.raises(UnreadableDocumentRefError):
            await service.convert_document(
                user_id="user",
                session_id="session",
                file_ref=str(source),
                target_format="docx",
                reference_docx_file_ref=str(missing),
            )

    asyncio.run(run())


def test_reference_docx_file_ref_must_be_docx(tmp_path: Path) -> None:
    async def run() -> None:
        source = _scoped_file(tmp_path, "user", "session", "input.md", "# source")
        reference = _scoped_file(tmp_path, "user", "session", "reference.txt", "text")
        service = _service(tmp_path)

        with pytest.raises(DocumentConvertError):
            await service.convert_document(
                user_id="user",
                session_id="session",
                file_ref=str(source),
                target_format="docx",
                reference_docx_file_ref=str(reference),
            )

    asyncio.run(run())


def test_reference_docx_file_ref_directory_is_rejected(tmp_path: Path) -> None:
    async def run() -> None:
        source = _scoped_file(tmp_path, "user", "session", "input.md", "# source")
        directory = tmp_path / "user" / "session" / "reference.docx"
        directory.mkdir(parents=True)
        service = _service(tmp_path)

        with pytest.raises(UnreadableDocumentRefError):
            await service.convert_document(
                user_id="user",
                session_id="session",
                file_ref=str(source),
                target_format="docx",
                reference_docx_file_ref=str(directory),
            )

    asyncio.run(run())


def test_route_log_includes_export_intent_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        source = _scoped_file(tmp_path, "user", "session", "input.md", "# source")
        reference = _scoped_binary(
            tmp_path,
            "user",
            "session",
            "reference.docx",
            b"docx",
        )
        service = _service(tmp_path)
        events: List[dict] = []

        def capture_log_event(event_name: str, **kwargs) -> None:
            if event_name == "document_convert route resolved":
                events.append(kwargs)

        monkeypatch.setattr(
            "chat.application.tools.services.document_convert.service.log_event",
            capture_log_event,
        )

        await service.convert_document(
            user_id="user",
            session_id="session",
            file_ref=str(source),
            target_format="docx",
            title="Report",
            reference_docx_file_ref=str(reference),
        )

        assert events
        assert events[0]["route_kind"] == "text_export"
        assert events[0]["requires_parse"] is False
        assert events[0]["export_source_format"] == "markdown"
        assert events[0]["title_provided"] is True
        assert events[0]["reference_docx_used"] is True

    asyncio.run(run())


def test_invalid_file_ref_raises_unreadable_ref(tmp_path: Path) -> None:
    async def run() -> None:
        (tmp_path / "user" / "session").mkdir(parents=True)
        service = _service(tmp_path)

        with pytest.raises(UnreadableDocumentRefError):
            await service.convert_document(
                user_id="user",
                session_id="session",
                file_ref=str(tmp_path / "user" / "session" / "missing.md"),
                target_format="docx",
            )

    asyncio.run(run())


def test_parse_failure_is_document_parse_error(tmp_path: Path) -> None:
    async def run() -> None:
        source = _scoped_binary(tmp_path, "user", "session", "paper.pdf", b"%PDF-1.7")
        service = _service(tmp_path, parse_service=FailingParseService())

        with pytest.raises(DocumentParseError):
            await service.convert_document(
                user_id="user",
                session_id="session",
                file_ref=str(source),
                target_format="docx",
            )

    asyncio.run(run())


def test_export_failure_is_document_export_error(tmp_path: Path) -> None:
    async def run() -> None:
        source = _scoped_file(tmp_path, "user", "session", "input.md", "# source")
        service = _service(tmp_path, export_service=FailingExportService())

        with pytest.raises(DocumentExportError):
            await service.convert_document(
                user_id="user",
                session_id="session",
                file_ref=str(source),
                target_format="docx",
            )

    asyncio.run(run())


def test_pdf_route_does_not_import_docling(tmp_path: Path) -> None:
    source = Path("src/chat/application/tools/services/document_parse/pdf/parser.py")
    text = source.read_text(encoding="utf-8")

    assert "docling" not in text.lower()
    assert "DocumentConverter" not in text


def _service(
    temp_root: Path,
    *,
    parse_service=None,
    export_service=None,
) -> DocumentConvertService:
    return DocumentConvertService(
        parse_service=parse_service or FakeParseService(),
        export_service=export_service or FakeExportService(),
        temp_file_resolver=DocumentTempFileResolver(temp_root=temp_root),
    )


class FakeParseService:
    def __init__(self) -> None:
        self.calls = []

    async def parse_path(self, path: Path) -> DocumentParseResult:
        resolved = path.resolve(strict=False)
        self.calls.append(resolved)
        return DocumentParseResult(
            text="# parsed",
            source=str(resolved),
            file_type="pdf",
        )


class FailingParseService:
    async def parse_path(self, path: Path) -> DocumentParseResult:
        raise RuntimeError("parse failed")


class FakeExportService:
    def __init__(self) -> None:
        self.export_content_calls = []
        self.export_markdown_calls = []
        self.export_content_options = []
        self.export_markdown_options = []

    async def export_content(
        self,
        *,
        user_id: str,
        session_id: str,
        content: str,
        target_format: str,
        source_format: str,
        file_name=None,
        options: Optional[ExportOptions] = None,
    ) -> GeneratedDocumentFile:
        self.export_content_calls.append(
            (content, target_format, source_format, file_name)
        )
        self.export_content_options.append(options)
        return _generated(
            user_id=user_id,
            session_id=session_id,
            target_format=target_format,
        )

    async def export_markdown(
        self,
        *,
        user_id: str,
        session_id: str,
        markdown: str,
        target_format: str,
        file_name=None,
        options: Optional[ExportOptions] = None,
    ) -> GeneratedDocumentFile:
        self.export_markdown_calls.append((markdown, target_format, file_name))
        self.export_markdown_options.append(options)
        return _generated(
            user_id=user_id,
            session_id=session_id,
            target_format=target_format,
        )


class FailingExportService(FakeExportService):
    async def export_content(self, **kwargs):
        from chat.application.document_export import ExportOutputError

        raise ExportOutputError("export failed")


def _generated(
    *,
    user_id: str,
    session_id: str,
    target_format: str,
) -> GeneratedDocumentFile:
    return GeneratedDocumentFile(
        file_path=Path("generated.docx"),
        file_name="generated.docx",
        storage_file_name="0123456789abcdef0123456789abcdef-generated.docx",
        user_id=user_id,
        session_id=session_id,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        target_format=target_format,
        size_bytes=12,
    )


def _scoped_file(
    root: Path,
    user_id: str,
    session_id: str,
    name: str,
    text: str,
) -> Path:
    path = root / user_id / session_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _scoped_binary(
    root: Path,
    user_id: str,
    session_id: str,
    name: str,
    content: bytes,
) -> Path:
    path = root / user_id / session_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path
