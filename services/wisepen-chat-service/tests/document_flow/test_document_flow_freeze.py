import re
import importlib
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest


def _install_module_stub(name: str, **attrs) -> None:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    if "." in name:
        parent_name, child_name = name.rsplit(".", 1)
        parent = importlib.import_module(parent_name)
        setattr(parent, child_name, module)


class _MarkdownIt:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    def enable(self, *args, **kwargs):
        del args, kwargs
        return self

    def render(self, markdown: str) -> str:
        return markdown


class _Browser:
    pass


class _Playwright:
    pass


def _cache_and_format_stub(**kwargs) -> str:
    del kwargs
    return "[ToolContent Metadata]\ncontent_id: cnt_stub\n\n[Content]\nstub"


_install_module_stub("markdown_it", MarkdownIt=_MarkdownIt)
_install_module_stub("playwright")
_install_module_stub(
    "playwright.async_api",
    Browser=_Browser,
    Playwright=_Playwright,
    async_playwright=lambda: None,
)
_install_module_stub(
    "chat.application.tool_content_store",
    cache_and_format=_cache_and_format_stub,
)

from chat.application.attachment_read import (
    AttachmentReadRequest,
    AttachmentReadService,
    ResolvedAttachment,
)
from chat.application.content_detection import ContentDetection, ContentKind, DetectionConfidence, DetectionHints
from chat.application.document_export.models import GeneratedDocumentFile
from chat.application.document_parse.document_parse_service import DocumentParseResultItem
from chat.application.document_parse.file_resolver import LocalDocumentFileResolver
from chat.application.document_parse.models import DocumentParseResult, ParsedPage
from chat.application.file_handoff import TemporaryFileHandoffStore

_TOOLS_ROOT = Path(__file__).parents[2] / "src" / "chat" / "application" / "tools"


def _install_package_stub(name: str, path: Path) -> None:
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        module.__path__ = [str(path)]
        sys.modules[name] = module
    parent_name, child_name = name.rsplit(".", 1)
    parent = importlib.import_module(parent_name)
    setattr(parent, child_name, module)


_install_package_stub("chat.application.tools", _TOOLS_ROOT)
_install_package_stub("chat.application.tools.attachment", _TOOLS_ROOT / "attachment")
_install_package_stub("chat.application.tools.document", _TOOLS_ROOT / "document")
_install_package_stub("chat.application.tools.web", _TOOLS_ROOT / "web")

from chat.application.tools.attachment.attachment_read_tool import AttachmentReadTool
from chat.application.tools.document.document_convert_tool import DocumentConvertTool
from chat.application.tools.document.document_export_tool import DocumentExportTool
from chat.application.tools.document.document_parse_tool import DocumentParseTool
from chat.application.tools.web.web_fetch_tool import WebFetchTool
from chat.application.web_fetch.models import FetchedDocument

pytestmark = pytest.mark.asyncio


_DOWNLOAD_REF_NOTE = (
    "download_ref is for user download and preview only. Do not pass download_ref to "
    "web_fetch, document_parse, attachment_read, evidence_rank, or tool_content_read."
)


async def test_direct_text_export_returns_download_ref_without_paths(tmp_path: Path) -> None:
    tool = DocumentExportTool(
        export_service=FakeExportService(tmp_path),
        content_store=FakeContentStore({}),
    )

    output = await tool.execute(
        {"session_id": "session"},
        target_format="pdf",
        content="# Report",
        file_name="report.pdf",
    )

    assert "[Generated Document]" in output
    assert "- download_ref: session/report.pdf" in output
    assert _DOWNLOAD_REF_NOTE in output
    _assert_no_generated_path_leak(output)


async def test_tool_content_export_returns_download_ref(tmp_path: Path) -> None:
    export_service = FakeExportService(tmp_path)
    tool = DocumentExportTool(
        export_service=export_service,
        content_store=FakeContentStore({"cnt_report": "cached markdown"}),
    )

    output = await tool.execute(
        {"session_id": "session"},
        target_format="docx",
        content_ref="cnt_report",
        file_name="cached.docx",
    )

    assert export_service.last_content == "cached markdown"
    assert "- download_ref: session/cached.docx" in output
    assert _DOWNLOAD_REF_NOTE in output
    _assert_no_generated_path_leak(output)


async def test_file_ref_batch_parses_to_content_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file_ref = _handoff_pdf(tmp_path)
    parse_tool = _document_parse_tool(monkeypatch)

    output = await parse_tool.execute({"session_id": "session"}, file_refs=[file_ref])

    assert "[Tool Result] Document parse batch results" in output
    assert "content_id: cnt_parse" in output
    assert parse_tool.parse_service.file_refs == [file_ref]


async def test_document_convert_returns_download_ref(tmp_path: Path) -> None:
    file_ref = _handoff_pdf(tmp_path)
    tool = DocumentConvertTool(convert_service=FakeConvertService(tmp_path))

    output = await tool.execute(
        {"session_id": "session"},
        file_ref=file_ref,
        target_format="html",
        file_name="converted.html",
    )

    assert "- download_ref: session/converted.html" in output
    assert _DOWNLOAD_REF_NOTE in output
    _assert_no_generated_path_leak(output)


async def test_attachment_text_read_returns_content_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text_file = tmp_path / "notes.txt"
    text_file.write_text("attachment text", encoding="utf-8")
    monkeypatch.setattr(
        "chat.application.attachment_read.service.cache_and_format",
        lambda **kwargs: "[ToolContent Metadata]\ncontent_id: cnt_attachment\n\n[Content]\nattachment text",
    )
    tool = _attachment_read_tool(
        tmp_path=tmp_path,
        resolved=[_resolved("att_text", "notes.txt", text_file, "text/plain")],
        detections={"notes.txt": _detection(ContentKind.TEXT, "text/plain", ".txt")},
    )

    output = await tool.execute({"session_id": "session", "user_id": "user"}, attachment_refs=["att_text"])

    assert "content_id: cnt_attachment" in output
    assert "local_path" not in output
    assert "file_path" not in output


async def test_attachment_binary_handoff_then_document_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-1.7")
    attachment_tool = _attachment_read_tool(
        tmp_path=tmp_path,
        resolved=[_resolved("att_doc", "report.pdf", source, "application/pdf")],
        detections={"report.pdf": _detection(ContentKind.DOCUMENT, "application/pdf", ".pdf")},
    )

    attachment_output = await attachment_tool.execute(
        {"session_id": "session", "user_id": "user"},
        attachment_refs=["att_doc"],
    )
    file_ref = _extract_field(attachment_output, "file_ref")
    parse_tool = _document_parse_tool(monkeypatch)
    parse_output = await parse_tool.execute({"session_id": "session"}, file_refs=[file_ref])

    assert "Document parse required:" in attachment_output
    assert Path(file_ref).name[16] == "-"
    assert Path(file_ref).suffix == ".pdf"
    assert "content_id: cnt_parse" in parse_output
    _assert_file_ref_only_in_allowed_attachment_lines(attachment_output, file_ref)


async def test_attachment_image_ocr_returns_content_id_and_image_ref(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"\x89PNG\r\n")
    monkeypatch.setattr(
        "chat.application.attachment_read.service.cache_and_format",
        lambda **kwargs: "[ToolContent Metadata]\ncontent_id: cnt_ocr\n\n[Content]\nocr text",
    )
    tool = _attachment_read_tool(
        tmp_path=tmp_path,
        resolved=[_resolved("att_img", "screen.png", image, "image/png")],
        detections={"screen.png": _detection(ContentKind.IMAGE, "image/png", ".png")},
        ocr_adapter=FakeOcrAdapter("ocr text"),
    )

    output = await tool.execute({"session_id": "session", "user_id": "user"}, attachment_refs=["att_img"])

    assert "status: ocr_completed" in output
    assert "content_id: cnt_ocr" in output
    assert "image_ref: att_img" in output
    assert "OCR text is only text extracted from the image. It does not replace visual analysis" in output
    assert "local_path" not in output
    assert "file_path" not in output


async def test_url_document_handoff_then_document_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    web_tool = WebFetchTool(
        fetcher=FakeWebFetcher(
            [
                SimpleNamespace(
                    url="https://example.com/report.pdf",
                    success=True,
                    content=None,
                    document=FetchedDocument(
                        url="https://example.com/report.pdf",
                        media_type="application/pdf",
                        filename="report.pdf",
                        content=b"%PDF-1.7",
                    ),
                    error=None,
                )
            ]
        ),
        file_handoff_store=TemporaryFileHandoffStore(root_dir=tmp_path / "handoff", ttl_seconds=3600),
    )

    web_output = await web_tool.execute({"session_id": "session"}, urls=["https://example.com/report.pdf"])
    file_ref = _extract_field(web_output, "file_ref")
    parse_output = await _document_parse_tool(monkeypatch).execute({"session_id": "session"}, file_refs=[file_ref])

    assert "Downloaded a document file. Web Fetch does not parse document content." in web_output
    assert Path(file_ref).name[16] == "-"
    assert Path(file_ref).suffix == ".pdf"
    assert "content_id: cnt_parse" in parse_output


async def test_url_text_fetch_returns_tool_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    del tmp_path
    monkeypatch.setattr(
        "chat.application.tools.web.web_fetch_tool.cache_and_format",
        lambda **kwargs: "[ToolContent Metadata]\ncontent_id: cnt_web\n\n[Content]\nweb text",
    )
    web_tool = WebFetchTool(
        fetcher=FakeWebFetcher(
            [
                SimpleNamespace(
                    url="https://example.com/page",
                    success=True,
                    content="web text",
                    document=None,
                    error=None,
                )
            ]
        ),
        file_handoff_store=TemporaryFileHandoffStore(root_dir=Path.cwd() / ".tmp-test-handoff", ttl_seconds=1),
    )

    output = await web_tool.execute({"session_id": "session"}, urls=["https://example.com/page"])

    assert "content_id: cnt_web" in output


async def test_generated_download_ref_frontend_mock_preview_and_download(tmp_path: Path) -> None:
    tool = DocumentConvertTool(convert_service=FakeConvertService(tmp_path))
    output = await tool.execute(
        {"session_id": "session"},
        file_ref="unused.pdf",
        target_format="pdf",
        file_name="converted.pdf",
    )
    download_ref = _extract_field(output, "download_ref")
    frontend = FakeFrontend({"session/converted.pdf": b"%PDF-1.7"})

    assert frontend.download(download_ref) == b"%PDF-1.7"
    assert frontend.preview(download_ref) == "preview:session/converted.pdf"
    assert not Path(download_ref).is_absolute()
    assert ":" not in download_ref
    assert "\\" not in download_ref


async def test_reference_misuse_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parse_tool = _document_parse_tool(monkeypatch)
    attachment_tool = AttachmentReadTool(service=FailIfCalledAttachmentService())
    web_tool = WebFetchTool(
        fetcher=FakeWebFetcher([]),
        file_handoff_store=TemporaryFileHandoffStore(root_dir=tmp_path / "handoff", ttl_seconds=3600),
    )
    file_ref = _handoff_pdf(tmp_path)

    assert "Document file not found" in await parse_tool.execute({"session_id": "session"}, file_refs=["att_upload"])
    assert "Invalid file_refs parameter" in await parse_tool.execute({"session_id": "session"}, file_refs=["cnt_123"])
    assert "Document file not found" in await parse_tool.execute({"session_id": "session"}, file_refs=["session/report.pdf"])
    assert "Document file not found" in await parse_tool.execute({"session_id": "session"}, file_refs=["att_img"])
    assert "Invalid attachment_refs parameter" in await attachment_tool.execute(
        {"session_id": "session", "user_id": "user"},
        attachment_refs=[file_ref],
    )
    assert "Invalid attachment_refs parameter" in await attachment_tool.execute(
        {"session_id": "session", "user_id": "user"},
        attachment_refs=["session/report.pdf"],
    )
    assert "Invalid urls parameter" in await web_tool.execute(
        {"session_id": "session"},
        urls=["session/report.pdf"],
    )


async def test_reference_contract_docs_are_frozen() -> None:
    docs_root = Path(__file__).parents[2] / "docs"

    reference_contract = (docs_root / "file_reference_contract.md").read_text(encoding="utf-8")
    routing = (docs_root / "document_tool_routing.md").read_text(encoding="utf-8")
    download = (docs_root / "generated_file_download_contract.md").read_text(encoding="utf-8")

    for reference in ["attachment_ref", "file_ref", "content_id", "download_ref", "image_ref"]:
        assert reference in reference_contract
    assert "Multiple `file_ref` values must be parsed in one call" in routing
    assert "Do not call `document_parse` once per `file_ref`." in routing
    assert "download_ref is for user download and preview only" in download
    assert "The model must not pass `download_ref` to `web_fetch`." in download


async def test_forbidden_imports_and_private_handoff_are_removed() -> None:
    root = Path(__file__).parents[2]
    python_files = list((root / "src").rglob("*.py")) + list((root / "tests").rglob("*.py"))
    combined = "\n".join(path.read_text(encoding="utf-8") for path in python_files if "__pycache__" not in path.parts)

    forbidden_private_import = (
        "chat.application.tools.document.document_export_tool"
        " import "
        "_format_generated_result"
    )
    old_ocr_path = ".".join(["chat", "application", "document_parse", "ocr"])
    private_export_formatter = "def " + "_format_generated_result"
    private_web_handoff = "def " + "_write_handoff_file"

    assert forbidden_private_import not in combined
    assert old_ocr_path not in combined
    assert private_export_formatter not in combined
    assert private_web_handoff not in combined


@dataclass
class FakeExportService:
    root: Path
    last_content: Optional[str] = None

    async def export_content(
        self,
        *,
        session_id: str,
        content: str,
        target_format: str,
        source_format: str = "markdown",
        file_name: Optional[str] = None,
    ) -> GeneratedDocumentFile:
        del session_id, source_format
        self.last_content = content
        name = file_name or f"document.{target_format}"
        return GeneratedDocumentFile(
            file_path=self.root / "internal" / name,
            file_name=name,
            content_type=f"application/{target_format}",
            target_format=target_format,
            size_bytes=123,
        )


@dataclass
class FakeConvertService:
    root: Path

    async def convert_file(
        self,
        *,
        session_id: str,
        file_ref: str,
        target_format: str,
        file_name: Optional[str] = None,
    ) -> GeneratedDocumentFile:
        del session_id, file_ref
        name = file_name or f"converted.{target_format}"
        return GeneratedDocumentFile(
            file_path=self.root / "internal" / name,
            file_name=name,
            content_type=f"application/{target_format}",
            target_format=target_format,
            size_bytes=456,
        )


from typing import Dict, Optional

class FakeContentStore:
    def __init__(self, items: Dict[str, str]) -> None:
        self.items = items

    def get(self, *, session_id: str, content_id: str):
        del session_id
        text = self.items.get(content_id)
        if text is None:
            return None
        return SimpleNamespace(text=text)


from typing import List
class FakeParseService:
    def __init__(self) -> None:
        self.file_refs: List[str] = []

    async def parse_many(self, paths: List[Path], *, file_refs: Optional[List[str]] = None):
        self.file_refs = list(file_refs or [])
        return [
            DocumentParseResultItem(
                file_ref=self.file_refs[index],
                success=True,
                result=DocumentParseResult(
                    text=f"parsed markdown {index}",
                    source=str(path),
                    file_type="pdf",
                    pages=[ParsedPage(page_index=0, text="parsed markdown", page_type="text")],
                    metadata={"parser": "fake"},
                ),
            )
            for index, path in enumerate(paths)
        ]


from typing import List
class FakeWebFetcher:
    def __init__(self, results: List[SimpleNamespace]) -> None:
        self.results = results

    async def fetch_many(self, urls: List[str]):
        del urls
        return self.results


class RecordingResolver:
    def __init__(self, resolved: List[ResolvedAttachment]) -> None:
        self.resolved = resolved

    async def resolve_many(
        self,
        *,
        session_id: str,
        user_id: str,
        attachment_refs: List[str],
    ) -> List[ResolvedAttachment]:
        del session_id, user_id, attachment_refs
from typing import Dict, Optional


class MappingDetector:
    def __init__(self, detections: Dict[str, ContentDetection]) -> None:
class MappingDetector:
    def __init__(self, detections: dict[str, ContentDetection]) -> None:
        self.detections = detections

    async def detect_path(self, path: Path, hints: Optional[DetectionHints] = None) -> ContentDetection:
        if hints is not None and hints.filename in self.detections:
            return self.detections[hints.filename]
        return self.detections[path.name]


class FakeOcrAdapter:
    def __init__(self, text: str) -> None:
        self.text = text

    async def extract_text(self, image_path: Path) -> str:
        del image_path
        return self.text


class FailIfCalledAttachmentService:
    async def read_attachments(self, request: AttachmentReadRequest):
        del request
        raise AssertionError("attachment_read service should not be called for invalid references")


class FakeFrontend:
    def __init__(self, files: Dict[str, bytes]) -> None:
        self.files = files

    def download(self, download_ref: str) -> bytes:
        return self.files[download_ref]

    def preview(self, download_ref: str) -> str:
        if download_ref not in self.files:
            raise FileNotFoundError(download_ref)
        return f"preview:{download_ref}"


def _document_parse_tool(monkeypatch: pytest.MonkeyPatch) -> DocumentParseTool:
    monkeypatch.setattr(
        "chat.application.tools.document.document_parse_tool.cache_and_format",
        lambda **kwargs: "[ToolContent Metadata]\ncontent_id: cnt_parse\n\n[Content]\nparsed markdown",
    )
    return DocumentParseTool(
        parse_service=FakeParseService(),
        file_resolver=LocalDocumentFileResolver(),
    )


def _attachment_read_tool(
    *,
    tmp_path: Path,
    resolved: list[ResolvedAttachment],
    detections: dict[str, ContentDetection],
    ocr_adapter: Optional[FakeOcrAdapter] = None,
) -> AttachmentReadTool:
    service = AttachmentReadService(
        resolver=RecordingResolver(resolved),
        content_detector=MappingDetector(detections),
        file_handoff_store=TemporaryFileHandoffStore(root_dir=tmp_path / "handoff", ttl_seconds=3600),
        ocr_image_adapter=ocr_adapter or FakeOcrAdapter(""),
    )
    return AttachmentReadTool(service=service)


def _handoff_pdf(tmp_path: Path) -> str:
    store = TemporaryFileHandoffStore(root_dir=tmp_path / "handoff", ttl_seconds=3600)
    return store.write_bytes(
        session_id="session",
        filename="report.pdf",
        content=b"%PDF-1.7",
        canonical_suffix=".pdf",
    ).file_ref


def _resolved(
    attachment_ref: str,
    file_name: str,
    path: Path,
    mime_type: str,
) -> ResolvedAttachment:
    return ResolvedAttachment(
        attachment_ref=attachment_ref,
        file_name=file_name,
        mime_type=mime_type,
        size_bytes=path.stat().st_size,
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


def _extract_field(output: str, field: str) -> str:
    match = re.search(rf"^\s*-?\s*{re.escape(field)}:\s*(.+)$", output, re.MULTILINE)
    assert match is not None, output
    return match.group(1).strip()


def _assert_no_generated_path_leak(output: str) -> None:
    forbidden = ["generated.file_path", "output_path", "local_path", "file_path"]
    for value in forbidden:
        assert value not in output


def _assert_file_ref_only_in_allowed_attachment_lines(output: str, file_ref: str) -> None:
    allowed_prefixes = ("file_ref:", "- ")
    for line in output.splitlines():
        if file_ref not in line:
            continue
        assert line.strip().startswith(allowed_prefixes), line
