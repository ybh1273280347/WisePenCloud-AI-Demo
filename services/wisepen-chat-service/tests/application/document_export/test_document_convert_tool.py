import asyncio
from pathlib import Path

from chat.application.document_export import GeneratedDocumentFile
from chat.application.tools.document.document_convert_tool import DocumentConvertTool
from chat.application.tools.services.document_convert.errors import DocumentConvertError


def test_document_convert_tool_schema_contains_export_options() -> None:
    schema = DocumentConvertTool(convert_service=CapturingConvertService()).parameters_schema

    assert "title" in schema["properties"]
    assert "reference_docx_file_ref" in schema["properties"]
    assert "reference_docx_path" not in schema["properties"]
    assert "reference_docx" not in schema["properties"]


def test_document_convert_tool_passes_title_to_convert_service() -> None:
    async def run() -> None:
        service = CapturingConvertService()
        tool = DocumentConvertTool(convert_service=service)

        result = await tool.execute(
            {"session_id": "session", "user_id": "user"},
            file_ref="/tmp/wisepen-chat-upload-files/user/session/source.md",
            target_format="pdf",
            title="Report",
        )

        assert not result.startswith("[Tool Error]")
        assert service.calls[0]["title"] == "Report"

    asyncio.run(run())


def test_document_convert_tool_passes_reference_docx_file_ref() -> None:
    async def run() -> None:
        service = CapturingConvertService()
        tool = DocumentConvertTool(convert_service=service)

        await tool.execute(
            {"session_id": "session", "user_id": "user"},
            file_ref="/tmp/wisepen-chat-upload-files/user/session/source.md",
            target_format="docx",
            reference_docx_file_ref="/tmp/wisepen-chat-upload-files/user/session/ref.docx",
        )

        assert (
            service.calls[0]["reference_docx_file_ref"]
            == "/tmp/wisepen-chat-upload-files/user/session/ref.docx"
        )

    asyncio.run(run())


def test_document_convert_tool_returns_convert_error_for_empty_title() -> None:
    async def run() -> None:
        tool = DocumentConvertTool(convert_service=RejectingConvertService())

        result = await tool.execute(
            {"session_id": "session", "user_id": "user"},
            file_ref="/tmp/wisepen-chat-upload-files/user/session/source.md",
            target_format="pdf",
            title="",
        )

        assert result == "[Tool Error] title must be a non-empty string"

    asyncio.run(run())


def test_document_convert_tool_does_not_import_document_export_error() -> None:
    source = Path(
        "src/chat/application/tools/document/document_convert_tool.py"
    ).read_text(encoding="utf-8")

    assert "DocumentExportError" not in source


class CapturingConvertService:
    def __init__(self) -> None:
        self.calls = []

    async def convert_document(self, **kwargs):
        self.calls.append(kwargs)
        return GeneratedDocumentFile(
            file_path=Path("generated.pdf"),
            file_name="generated.pdf",
            storage_file_name="0123456789abcdef0123456789abcdef-generated.pdf",
            user_id=kwargs["user_id"],
            session_id=kwargs["session_id"],
            content_type="application/pdf",
            target_format=kwargs["target_format"],
            size_bytes=12,
        )


class RejectingConvertService:
    async def convert_document(self, **kwargs):
        raise DocumentConvertError("title must be a non-empty string")
