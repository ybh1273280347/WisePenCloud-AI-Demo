import importlib.util
from pathlib import Path

import pytest

from chat.application.tools.services.attachment_read import (
    AttachmentKind,
    AttachmentReadItem,
    AttachmentReadResult,
    AttachmentStatus,
)
from chat.application.tools.services.attachment_read.resolver import StubAttachmentResolver
_TOOL_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "chat"
    / "application"
    / "tools"
    / "attachment"
    / "attachment_read_tool.py"
)
_SPEC = importlib.util.spec_from_file_location("attachment_read_tool_module", _TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
AttachmentReadTool = _MODULE.AttachmentReadTool

pytestmark = pytest.mark.asyncio


async def test_tool_missing_session_id() -> None:
    tool = AttachmentReadTool(service=FakeService(AttachmentReadResult(items=[])))

    result = await tool.execute({"user_id": "u"}, attachment_refs=["att"])

    assert result == "[Tool Error] Missing session_id in execution context."


async def test_tool_missing_user_id() -> None:
    tool = AttachmentReadTool(service=FakeService(AttachmentReadResult(items=[])))

    result = await tool.execute({"session_id": "s"}, attachment_refs=["att"])

    assert result == "[Tool Error] Missing user_id in execution context."


@pytest.mark.parametrize("kwargs", [{}, {"attachment_refs": []}])
async def test_tool_missing_attachment_refs(kwargs) -> None:
    tool = AttachmentReadTool(service=FakeService(AttachmentReadResult(items=[])))

    result = await tool.execute({"session_id": "s", "user_id": "u"}, **kwargs)

    assert result == "[Tool Error] Missing required attachment_refs parameter."


async def test_tool_stub_resolver_not_configured() -> None:
    tool = AttachmentReadTool(service=RaisingService(NotImplementedError()))

    result = await tool.execute({"session_id": "s", "user_id": "u"}, attachment_refs=["att"])

    assert result == "[Tool Error] Attachment resolver is not configured."


async def test_tool_formats_service_result() -> None:
    tool = AttachmentReadTool(
        service=FakeService(
            AttachmentReadResult(
                items=[
                    AttachmentReadItem(
                        attachment_ref="att",
                        file_name="notes.txt",
                        mime_type="text/plain",
                        size_bytes=5,
                        kind=AttachmentKind.DIRECT_TEXT.value,
                        status=AttachmentStatus.READ.value,
                        content_block="[cached]",
                    )
                ]
            )
        )
    )

    result = await tool.execute({"session_id": "s", "user_id": "u"}, attachment_refs=["att"])

    assert "[Tool Result] attachment_read" in result
    assert "[cached]" in result


async def test_tool_description_and_schema_rules() -> None:
    tool = AttachmentReadTool(service=FakeService(AttachmentReadResult(items=[])))
    schema = tool.parameters_schema

    assert "Images are always OCR-processed first." in tool.description
    assert "OCR text is only extracted text from the image. It does not replace visual analysis by the model." in tool.description
    assert "OCR failure does not block the tool result." in tool.description
    assert set(schema["properties"]) == {"attachment_refs", "purpose"}
    assert "mode" not in schema["properties"]
    assert "ocr" not in schema["properties"]
    assert "vision" not in schema["properties"]
    assert "parse_document" not in schema["properties"]


async def test_tool_does_not_call_document_parse_or_ocr() -> None:
    assert "document_parse" not in AttachmentReadTool.__init__.__code__.co_names
    assert "Ocr" not in " ".join(AttachmentReadTool.__init__.__code__.co_names)


class FakeService:
    def __init__(self, result: AttachmentReadResult) -> None:
        self.result = result

    async def read_attachments(self, request):
        return self.result


class RaisingService:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def read_attachments(self, request):
        raise self.exc
