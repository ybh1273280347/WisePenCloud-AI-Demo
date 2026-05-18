import asyncio
from pathlib import Path

import pytest

from chat.application.document_export import GeneratedDocumentFile
from chat.application.tools.services.document_convert.errors import (
    DocumentConvertError,
    DocumentExportError,
    DocumentParseError,
    UnreadableDocumentRefError,
)
from chat.application.tools.services.document_convert.service import DocumentConvertService
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

    async def export_content(
        self,
        *,
        user_id: str,
        session_id: str,
        content: str,
        target_format: str,
        source_format: str,
        file_name=None,
    ) -> GeneratedDocumentFile:
        self.export_content_calls.append(
            (content, target_format, source_format, file_name)
        )
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
    ) -> GeneratedDocumentFile:
        self.export_markdown_calls.append((markdown, target_format, file_name))
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
