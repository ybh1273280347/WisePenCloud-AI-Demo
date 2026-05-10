"""
web_fetch decoupling and security unit tests.

Usage:
    uv run python test/test_web_fetch_security_unit.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import uuid
from pathlib import Path
from typing import List, Optional
from unittest.mock import patch

from chat.application.document_parse.file_resolver import LocalDocumentFileResolver
from chat.application.web_fetch.content_processor import ContentProcessor
from chat.application.web_fetch.content_processor import _extract_markdown_from_html
from chat.application.web_fetch.fetch_coordinator import FetchCoordinator
from chat.application.web_fetch.fetcher.static_fetcher import _route_response
from chat.application.web_fetch.fetcher.static_fetcher import FetchedDocument
from chat.application.web_fetch.utils.url import (
    UrlSecurityError,
    is_public_http_url,
    validate_public_http_url,
)


def _load_web_fetch_tool_module():
    module_path = Path(__file__).resolve().parents[1] / "src" / "chat" / "application" / "tools" / "web_fetch_tool.py"
    spec = importlib.util.spec_from_file_location("web_fetch_tool_under_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load web_fetch_tool module from {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WEB_FETCH_TOOL_MODULE = _load_web_fetch_tool_module()
WebFetchTool = WEB_FETCH_TOOL_MODULE.WebFetchTool


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class TrackingFetcher:
    def __init__(
        self,
        result: Optional[str | FetchedDocument] = None,
        error: Optional[Exception] = None,
    ):
        self.result = result
        self.error = error
        self.calls: List[str] = []

    async def fetch(self, url: str) -> Optional[str | FetchedDocument]:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return self.result


class FakeProcessor:
    def __init__(self):
        self.calls = []

    async def process_async(self, content) -> Optional[str]:
        self.calls.append(content)
        if isinstance(content, bytes):
            return None
        return str(content).strip() or None


def _make_coordinator(
    static: TrackingFetcher,
    steel: TrackingFetcher,
    local: TrackingFetcher,
    processor: Optional[FakeProcessor] = None,
) -> FetchCoordinator:
    processor = processor or FakeProcessor()
    return FetchCoordinator(
        static_fetcher=static,
        steel_fetcher=steel,
        local_script_fetcher=local,
        processor=processor,
        min_content_length=10,
        last_resort_min_length=5,
        cache_ttl_seconds=60,
        cache_max_items=16,
    )


def test_web_fetch_schema_is_url_only() -> None:
    tool = WebFetchTool(fetcher=TrackingFetcher(result="hello world"))
    schema = tool.parameters_schema
    props = set(schema["properties"].keys())
    assert_true(props == {"url"}, f"web_fetch schema should only expose url, got {props}")


def test_content_processor_does_not_parse_bytes() -> None:
    processor = ContentProcessor(min_content_length=1)
    assert_true(processor.process(b"%PDF-1.7") is None, "bytes should not enter document parsing")


def test_static_fetcher_routes_documents_to_handoff() -> None:
    cases = [
        ("application/pdf", "/files/sample.pdf", b"%PDF-1.7", ".pdf"),
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "/files/sample.docx",
            b"PK\x03\x04docx",
            ".docx",
        ),
        (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "/files/sample.xlsx",
            b"PK\x03\x04xlsx",
            ".xlsx",
        ),
        (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "/files/sample.pptx",
            b"PK\x03\x04pptx",
            ".pptx",
        ),
    ]

    for media_type, path, content, suffix in cases:
        result = _route_response(
            media_type=media_type,
            content_type_header=media_type,
            content_disposition="",
            path=path,
            url=f"https://example.com{path}",
            content=content,
        )
        assert_true(isinstance(result, FetchedDocument), f"{suffix} should return FetchedDocument")
        assert_true(result.content == content, f"{suffix} handoff should preserve bytes")
        assert_true(result.filename.endswith(suffix), f"{suffix} handoff should preserve extension")


def test_static_fetcher_uses_content_disposition_filename() -> None:
    cases = [
        (
            "attachment; filename*=UTF-8''%E6%96%87%E4%BB%B6.pdf",
            "/download",
            "application/pdf",
            "文件.pdf",
        ),
        (
            'attachment; filename="report.pdf"',
            "/download",
            "application/pdf",
            "report.pdf",
        ),
        (
            "attachment; filename='report.pdf'",
            "/download",
            "application/pdf",
            "report.pdf",
        ),
        (
            "attachment; filename=report.pdf",
            "/download",
            "application/pdf",
            "report.pdf",
        ),
        (
            'attachment; filename="report.exe"',
            "/download",
            "application/pdf",
            "report.pdf",
        ),
        (
            "",
            "/files/archive.bin",
            "application/epub+zip",
            "archive.epub",
        ),
        (
            "",
            "",
            "application/pdf",
            "download.pdf",
        ),
    ]

    for content_disposition, path, media_type, expected_filename in cases:
        result = _route_response(
            media_type=media_type,
            content_type_header=media_type,
            content_disposition=content_disposition,
            path=path,
            url=f"https://example.com{path}",
            content=b"document bytes",
        )
        assert_true(isinstance(result, FetchedDocument), "document response should return FetchedDocument")
        assert_true(
            result.filename == expected_filename,
            f"expected {expected_filename}, got {result.filename}",
        )


def test_static_fetcher_decodes_charset_from_header() -> None:
    result = _route_response(
        media_type="text/plain",
        content_type_header="text/plain; charset=gbk",
        content_disposition="",
        path="/article.txt",
        url="https://example.com/article.txt",
        content="中文正文".encode("gbk"),
    )

    assert_true(result == "中文正文", f"GBK header charset should decode text, got: {result}")


def test_static_fetcher_falls_back_to_meta_charset_after_bad_header() -> None:
    html = '<html><head><meta charset="gbk"></head><body>中文正文</body></html>'
    result = _route_response(
        media_type="text/html",
        content_type_header="text/html; charset=gb23122",
        content_disposition="",
        path="/article.html",
        url="https://example.com/article.html",
        content=html.encode("gbk"),
    )

    assert_true(isinstance(result, str), "HTML response should return text")
    assert_true("中文正文" in result, f"bad header charset should continue to meta charset, got: {result}")


def test_content_processor_falls_back_after_short_extract() -> None:
    baseline_text = "baseline 正文内容 " * 8

    with (
        patch("chat.application.web_fetch.content_processor.trafilatura.extract", return_value="short"),
        patch("chat.application.web_fetch.content_processor.trafilatura.baseline", return_value=(None, baseline_text, None)),
        patch("chat.application.web_fetch.content_processor.trafilatura.html2txt", return_value="html2txt fallback"),
    ):
        result = _extract_markdown_from_html(
            "<html><body>fallback</body></html>",
            min_content_length=20,
        )

    assert_true(result is not None, "fallback extraction should return text")
    assert_true("baseline 正文内容" in result, f"baseline should be used after short extract, got: {result}")


def test_private_url_rejected() -> None:
    try:
        validate_public_http_url("http://10.0.0.1/")
        assert_true(False, "private URL should be rejected")
    except UrlSecurityError:
        pass

    assert_true(not is_public_http_url("http://localhost/"), "localhost is not public")


async def test_entry_security_rejection_calls_no_fetchers() -> None:
    static = TrackingFetcher(result="should not reach")
    steel = TrackingFetcher(result="should not reach")
    local = TrackingFetcher(result="should not reach")
    coordinator = _make_coordinator(static, steel, local)

    try:
        await coordinator.fetch("http://10.0.0.1/")
        assert_true(False, "entry-level security rejection should raise UrlSecurityError")
    except UrlSecurityError:
        pass

    assert_true(len(static.calls) == 0, "static fetcher should not be called")
    assert_true(len(steel.calls) == 0, "steel fetcher should not be called")
    assert_true(len(local.calls) == 0, "local fetcher should not be called")


async def test_fetcher_security_error_terminates_chain() -> None:
    static = TrackingFetcher(error=UrlSecurityError("重定向到私有 IP"))
    steel = TrackingFetcher(result="# Steel Result\n\nShould not reach")
    local = TrackingFetcher(result="# Local Result\n\nShould not reach")
    coordinator = _make_coordinator(static, steel, local)

    with patch("chat.application.web_fetch.fetch_coordinator.validate_public_http_url", return_value="http://8.8.8.8/"):
        try:
            await coordinator.fetch("http://8.8.8.8/")
            assert_true(False, "FetchCoordinator should have raised UrlSecurityError")
        except UrlSecurityError:
            pass

    assert_true(len(static.calls) == 1, "static fetcher should be called once")
    assert_true(len(steel.calls) == 0, "fallback should stop after security error")
    assert_true(len(local.calls) == 0, "fallback should stop after security error")


async def test_normal_failure_allows_fallback() -> None:
    static = TrackingFetcher(result=None)
    steel = TrackingFetcher(result="# Steel Result\n\nFetched by Steel")
    local = TrackingFetcher(result="# Local Result\n\nFetched by Local")
    coordinator = _make_coordinator(static, steel, local)

    with patch("chat.application.web_fetch.fetch_coordinator.validate_public_http_url", return_value="http://8.8.8.8/"):
        result = await coordinator.fetch("http://8.8.8.8/")

    assert_true(result is not None, "should get result from fallback")
    assert_true("Steel Result" in result, "should get Steel result")
    assert_true(len(static.calls) == 1, "static fetcher should be called")
    assert_true(len(steel.calls) == 1, "steel fetcher should be called as fallback")


async def test_document_handoff_stops_fallback_and_processor() -> None:
    document = FetchedDocument(
        url="https://example.com/sample.pdf",
        media_type="application/pdf",
        filename="sample.pdf",
        content=b"%PDF-1.7 document bytes",
    )
    static = TrackingFetcher(result=document)
    steel = TrackingFetcher(result="# Steel Result\n\nShould not reach")
    local = TrackingFetcher(result="# Local Result\n\nShould not reach")
    processor = FakeProcessor()
    coordinator = _make_coordinator(static, steel, local, processor=processor)

    with patch("chat.application.web_fetch.fetch_coordinator.validate_public_http_url", return_value="https://example.com/sample.pdf"):
        result = await coordinator.fetch("https://example.com/sample.pdf")

    assert_true(result is document, "document handoff should be returned directly")
    assert_true(len(static.calls) == 1, "static fetcher should be called once")
    assert_true(len(processor.calls) == 0, "document handoff should not enter ContentProcessor")
    assert_true(len(steel.calls) == 0, "document handoff should not fallback to Steel")
    assert_true(len(local.calls) == 0, "document handoff should not fallback to LocalScript")


async def test_tool_rejects_private_ip_url() -> None:
    fetcher = TrackingFetcher(result="should not be used")
    tool = WebFetchTool(fetcher=fetcher)

    result = await tool.execute(
        {"session_id": uuid.uuid4().hex[:16]},
        url="http://10.0.0.1/",
    )

    assert_true("[Tool Error]" in result, f"should return Tool Error, got: {result[:100]}")
    assert_true("rejected by security policy" in result, "should mention security policy")
    assert_true(len(fetcher.calls) == 0, "fetcher should not be called for rejected URL")


async def test_tool_catches_security_error_from_fetcher() -> None:
    class SecurityRaisingFetcher:
        async def fetch(self, url: str) -> Optional[str]:
            raise UrlSecurityError("redirect to blocked IP")

    tool = WebFetchTool(fetcher=SecurityRaisingFetcher())

    with patch.object(WEB_FETCH_TOOL_MODULE, "validate_public_http_url", return_value="http://8.8.8.8/"):
        result = await tool.execute(
            {"session_id": uuid.uuid4().hex[:16]},
            url="http://8.8.8.8/",
        )

    assert_true("[Tool Error]" in result, "should return Tool Error")
    assert_true("rejected by security policy" in result, "should mention security policy")


async def test_tool_returns_file_ref_for_document_handoff() -> None:
    document = FetchedDocument(
        url="https://example.com/report.pdf",
        media_type="application/pdf",
        filename="report.pdf",
        content=b"%PDF-1.7 report",
    )

    class DocumentFetcher:
        async def fetch(self, url: str):
            return document

    tool = WebFetchTool(fetcher=DocumentFetcher())

    with patch.object(WEB_FETCH_TOOL_MODULE, "validate_public_http_url", return_value=document.url):
        result = await tool.execute(
            {"session_id": uuid.uuid4().hex[:16]},
            url=document.url,
        )

    assert_true("file_ref:" in result, f"document handoff should include file_ref, got: {result}")
    assert_true("document_parse" in result, "document handoff should tell caller to use document_parse")
    assert_true("Web Fetch does not parse document content" in result, "web_fetch should not claim parsing")

    file_ref_line = next(line for line in result.splitlines() if line.startswith("file_ref:"))
    file_ref = file_ref_line.split(":", 1)[1].strip()
    resolved = LocalDocumentFileResolver().resolve(file_ref)
    resolved_path = Path(resolved.local_path)
    assert_true(resolved_path.read_bytes() == document.content, "file_ref should point to cached document bytes")
    resolved_path.unlink(missing_ok=True)
    resolved_path.parent.rmdir()


async def main() -> int:
    sync_tests = [
        test_web_fetch_schema_is_url_only,
        test_content_processor_does_not_parse_bytes,
        test_static_fetcher_routes_documents_to_handoff,
        test_static_fetcher_uses_content_disposition_filename,
        test_static_fetcher_decodes_charset_from_header,
        test_static_fetcher_falls_back_to_meta_charset_after_bad_header,
        test_content_processor_falls_back_after_short_extract,
        test_private_url_rejected,
    ]

    async_tests = [
        test_entry_security_rejection_calls_no_fetchers,
        test_fetcher_security_error_terminates_chain,
        test_normal_failure_allows_fallback,
        test_document_handoff_stops_fallback_and_processor,
        test_tool_rejects_private_ip_url,
        test_tool_catches_security_error_from_fetcher,
        test_tool_returns_file_ref_for_document_handoff,
    ]

    passed = 0
    failed = 0

    for test in sync_tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {test.__name__}: {e}")
            failed += 1

    for test in async_tests:
        try:
            await test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {test.__name__}: {e}")
            failed += 1

    print(f"\n{passed}/{passed + failed} tests passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
