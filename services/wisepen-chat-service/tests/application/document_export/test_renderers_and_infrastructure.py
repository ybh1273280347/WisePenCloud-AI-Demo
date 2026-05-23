import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from chat.application.document_export.errors import ExportOutputError
from chat.application.document_export.internal.atomic_writer import AtomicExportWriter
from chat.application.document_export.internal.infrastructure.playwright_pool import (
    PlaywrightBrowserPool,
)
from chat.application.document_export.internal.path_safety import (
    is_path_within_root,
    sanitize_path_segment,
)
from chat.application.document_export.internal.renderers.docx_renderer import DocxRenderer
from chat.application.document_export.internal.renderers.html_renderer import HtmlRenderer
from chat.application.document_export.internal.renderers.pdf_renderer import PdfRenderer
from chat.application.document_export.internal.renderers.txt_renderer import TxtRenderer
from chat.application.document_export.models import ExportOptions, ExportRequest


def _request(
    *,
    markdown: str,
    output_path: Path,
    target_format: str,
    options: Optional[ExportOptions] = None,
) -> ExportRequest:
    return ExportRequest(
        user_id="user",
        session_id="session",
        markdown=markdown,
        target_format=target_format,
        output_path=output_path,
        file_name=output_path.name,
        options=options or ExportOptions(),
    )


def test_html_renderer_does_not_render_raw_html() -> None:
    html = HtmlRenderer().render_to_string("<script>alert(1)</script>")

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_html_renderer_tables_strikethrough_and_print_css() -> None:
    html = HtmlRenderer().render_to_string(
        "| A | B |\n| - | - |\n| one | two |\n\n~~gone~~"
    )

    assert "<table>" in html
    assert "<s>gone</s>" in html
    assert "@media print" in html
    assert "page-break-inside: avoid" in html


def test_pdf_renderer_reuses_html_renderer(tmp_path: Path) -> None:
    async def run() -> None:
        html_renderer = StubHtmlRenderer()
        browser_pool = StubBrowserPool()
        renderer = PdfRenderer(
            html_renderer=html_renderer,
            browser_pool=browser_pool,
        )

        await renderer.render(
            _request(
                markdown="# Title",
                output_path=tmp_path / "out.pdf",
                target_format="pdf",
                options=ExportOptions(title="Report"),
            )
        )

        assert html_renderer.calls == [("# Title", "Report")]
        assert browser_pool.html == "<html>rendered</html>"

    asyncio.run(run())


def test_playwright_pool_disables_javascript_and_blocks_external_resources(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        pool = PlaywrightBrowserPool()
        browser = FakeBrowser()

        await pool._render_with_context(
            browser=browser,
            html='<img src="https://example.com/a.png">',
            output_path=tmp_path / "out.pdf",
            timeout_seconds=1.0,
        )

        assert browser.context.context_options["java_script_enabled"] is False
        assert browser.context.route_pattern == "**/*"
        assert browser.context.page.pdf_options["print_background"] is True
        assert browser.context.page.pdf_options["prefer_css_page_size"] is True

        external_route = FakeRoute("https://example.com/a.png")
        await browser.context.route_handler(external_route)
        assert external_route.action == "abort"

        data_route = FakeRoute("data:image/png;base64,abc")
        await browser.context.route_handler(data_route)
        assert data_route.action == "continue"

    asyncio.run(run())


def test_txt_renderer_preserves_code_blocks() -> None:
    text = TxtRenderer()._markdown_to_text("Before\n\n```python\nprint('x')\n```\n")

    assert "Before" in text
    assert "print('x')" in text


def test_txt_renderer_extracts_link_image_and_table_text() -> None:
    text = TxtRenderer()._markdown_to_text(
        "See [docs](https://example.com).\n\n"
        "![diagram alt](diagram.png)\n\n"
        "| Name | Value |\n| - | - |\n| alpha | beta |\n"
    )

    assert "See docs." in text
    assert "https://example.com" not in text
    assert "diagram alt" in text
    assert "Name | Value" in text
    assert "alpha | beta" in text


def test_docx_renderer_deduplicates_resource_paths(tmp_path: Path) -> None:
    input_path = tmp_path / "work" / "input.md"
    input_path.parent.mkdir()
    input_path.write_text("# Title", encoding="utf-8")

    request = _request(
        markdown="# Title",
        output_path=tmp_path / "out.docx",
        target_format="docx",
        options=ExportOptions(assets_dir=input_path.parent),
    )

    args = DocxRenderer()._build_pandoc_args(input_path=input_path, request=request)
    resource_path = args[args.index("--resource-path") + 1]

    assert resource_path.split(os.pathsep) == [str(input_path.parent)]


def test_docx_renderer_keeps_reference_docx_argument(tmp_path: Path) -> None:
    input_path = tmp_path / "work" / "input.md"
    input_path.parent.mkdir()
    input_path.write_text("# Title", encoding="utf-8")
    reference_docx = tmp_path / "reference.docx"
    reference_docx.write_bytes(b"docx-template")

    request = _request(
        markdown="# Title",
        output_path=tmp_path / "out.docx",
        target_format="docx",
        options=ExportOptions(reference_docx=reference_docx),
    )

    renderer = DocxRenderer()
    renderer._validate_reference_docx(request)
    args = renderer._build_pandoc_args(input_path=input_path, request=request)

    assert args[args.index("--reference-doc") + 1] == str(reference_docx)


def test_atomic_export_writer_rejects_empty_output(tmp_path: Path) -> None:
    async def run() -> None:
        async def render(path: Path) -> None:
            path.write_text("", encoding="utf-8")

        with pytest.raises(ExportOutputError):
            await AtomicExportWriter().write_with_renderer(
                output_path=tmp_path / "out.txt",
                render=render,
            )

    asyncio.run(run())


def test_sanitize_path_segment_windows_reserved_names_and_invalid_chars() -> None:
    assert sanitize_path_segment("CON") == "CON_"

    cleaned = sanitize_path_segment("bad:name/with?chars")
    assert ":" not in cleaned
    assert "/" not in cleaned
    assert "\\" not in cleaned
    assert "?" not in cleaned


def test_is_path_within_root_rejects_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()

    assert is_path_within_root(root / "inside.txt", root)
    assert not is_path_within_root(tmp_path / "outside.txt", root)


class StubHtmlRenderer:
    def __init__(self) -> None:
        self.calls: List[Any] = []

    def render_to_string(self, markdown: str, *, title: Optional[str] = None) -> str:
        self.calls.append((markdown, title))
        return "<html>rendered</html>"


class StubBrowserPool:
    def __init__(self) -> None:
        self.html = ""

    async def render_pdf_from_html(self, **kwargs: Any) -> None:
        self.html = kwargs["html"]


class FakeBrowser:
    def __init__(self) -> None:
        self.context = FakeContext({})

    async def new_context(self, **kwargs: Any) -> "FakeContext":
        self.context = FakeContext(kwargs)
        return self.context


class FakeContext:
    def __init__(self, context_options: Dict[str, Any]) -> None:
        self.context_options = context_options
        self.route_pattern = ""
        self.route_handler = None
        self.page = FakePage()

    async def route(self, pattern: str, handler: Any) -> None:
        self.route_pattern = pattern
        self.route_handler = handler

    async def new_page(self) -> "FakePage":
        return self.page

    async def close(self) -> None:
        pass


class FakePage:
    def __init__(self) -> None:
        self.pdf_options: Dict[str, Any] = {}

    async def set_content(
        self, html: str, *, wait_until: str, timeout: float
    ) -> None:
        pass

    async def pdf(self, **kwargs: Any) -> None:
        self.pdf_options = kwargs
        Path(kwargs["path"]).write_bytes(b"%PDF")

    async def close(self) -> None:
        pass


class FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = FakeRequest(url)
        self.action = ""

    async def continue_(self) -> None:
        self.action = "continue"

    async def abort(self) -> None:
        self.action = "abort"
