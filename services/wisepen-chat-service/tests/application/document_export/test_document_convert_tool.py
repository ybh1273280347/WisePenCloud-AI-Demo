import asyncio

from chat.application.document_export.errors import ExportRenderError
from chat.application.tools.document.document_convert_tool import DocumentConvertTool


def test_document_convert_tool_preserves_export_diagnostic() -> None:
    async def run() -> None:
        tool = DocumentConvertTool(convert_service=FailingConvertService())

        result = await tool.execute(
            {"session_id": "session", "user_id": "user"},
            file_ref=(
                "/tmp/wisepen-chat-upload-files/user/session/"
                "0123456789abcdef0123456789abcdef-source.docx"
            ),
            target_format="pdf",
        )

        assert result.startswith("[Tool Error] Export failed:")
        assert "Playwright Chromium is not installed" in result

    asyncio.run(run())


class FailingConvertService:
    async def convert_document(self, **kwargs):
        raise ExportRenderError(
            "Failed to render PDF: PDF browser unavailable: "
            "Playwright Chromium is not installed."
        )
