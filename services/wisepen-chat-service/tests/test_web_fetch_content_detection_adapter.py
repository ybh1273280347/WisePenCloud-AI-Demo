import pytest

from chat.application.tools.common.content_detection import (
    ContentDetection,
    ContentKind,
    DetectionConfidence,
    DetectionHints,
)
from chat.application.tools.services.web_fetch.errors import UnsupportedMediaError
from chat.application.tools.services.web_fetch.fetcher.content_detection_adapter import (
    build_document_filename,
    build_web_fetch_detection_hints,
    build_web_fetch_result,
    decode_text_response,
    is_declared_unsupported_media,
    should_read_web_fetch_body,
)
from chat.application.tools.services.web_fetch.models import FetchedDocument

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "media_type",
    [
        "image/png",
        "video/mp4",
        "audio/mpeg",
        "font/woff2",
    ],
)
async def test_body_precheck_rejects_declared_media(media_type: str) -> None:
    hints = build_web_fetch_detection_hints(
        url="https://example.com/file",
        content_type_header=media_type,
        content_disposition="",
    )

    assert should_read_web_fetch_body(hints=hints) is False
    assert is_declared_unsupported_media(hints=hints) is True


@pytest.mark.parametrize(
    "media_type",
    [
        "text/html",
        "application/json",
        "application/xml",
        "application/pdf",
    ],
)
async def test_body_precheck_allows_text_and_documents(media_type: str) -> None:
    hints = build_web_fetch_detection_hints(
        url="https://example.com/file",
        content_type_header=media_type,
        content_disposition="",
    )

    assert should_read_web_fetch_body(hints=hints) is True


@pytest.mark.parametrize(
    ("url", "should_read"),
    [
        ("https://example.com/file.pdf", True),
        ("https://example.com/file.txt", True),
        ("https://example.com/download", False),
    ],
)
async def test_octet_stream_uses_filename_hint(url: str, should_read: bool) -> None:
    hints = build_web_fetch_detection_hints(
        url=url,
        content_type_header="application/octet-stream",
        content_disposition="",
    )

    assert should_read_web_fetch_body(hints=hints) is should_read


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (ContentKind.HTML, "<html></html>"),
        (ContentKind.JSON, '{"ok": true}'),
        (ContentKind.XML, "<root/>"),
        (ContentKind.TEXT, "plain text"),
    ],
)
async def test_result_builder_returns_text_for_text_like_kinds(kind: ContentKind, expected: str) -> None:
    result = await build_web_fetch_result(
        url="https://example.com/page",
        content=expected.encode("utf-8"),
        hints=DetectionHints(content_type_header="text/plain", source_uri="https://example.com/page"),
        detector=FakeDetector(_detection(kind, "text/plain", ".txt")),
    )

    assert result == expected


async def test_result_builder_returns_fetched_document() -> None:
    result = await build_web_fetch_result(
        url="https://example.com/source",
        content=b"%PDF-1.7",
        hints=build_web_fetch_detection_hints(
            url="https://example.com/source",
            content_type_header="application/pdf",
            content_disposition='attachment; filename="report.exe.pdf"',
        ),
        detector=FakeDetector(_detection(ContentKind.DOCUMENT, "application/pdf", ".pdf")),
    )

    assert isinstance(result, FetchedDocument)
    assert result.media_type == "application/pdf"
    assert result.filename == "report.pdf"
    assert result.content == b"%PDF-1.7"


@pytest.mark.parametrize("kind", [ContentKind.IMAGE, ContentKind.UNSUPPORTED_MEDIA])
async def test_result_builder_raises_for_media(kind: ContentKind) -> None:
    with pytest.raises(UnsupportedMediaError):
        await build_web_fetch_result(
            url="https://example.com/image",
            content=b"image",
            hints=DetectionHints(content_type_header="image/png", source_uri="https://example.com/image"),
            detector=FakeDetector(_detection(kind, "image/png", ".png")),
        )


@pytest.mark.parametrize("kind", [ContentKind.UNSUPPORTED_ARCHIVE, ContentKind.UNSUPPORTED_BINARY])
async def test_result_builder_returns_none_for_unsupported_non_media(kind: ContentKind) -> None:
    result = await build_web_fetch_result(
        url="https://example.com/file",
        content=b"binary",
        hints=DetectionHints(content_type_header="application/octet-stream", source_uri="https://example.com/file"),
        detector=FakeDetector(_detection(kind, "application/octet-stream", None)),
    )

    assert result is None


async def test_result_builder_returns_none_for_empty_decoded_text() -> None:
    result = await build_web_fetch_result(
        url="https://example.com/empty",
        content=b"   \n\t",
        hints=DetectionHints(content_type_header="text/plain", source_uri="https://example.com/empty"),
        detector=FakeDetector(_detection(ContentKind.TEXT, "text/plain", ".txt")),
    )

    assert result is None


async def test_document_filename_content_disposition_takes_priority() -> None:
    filename = build_document_filename(
        url="https://example.com/url-name.pdf",
        hints=build_web_fetch_detection_hints(
            url="https://example.com/url-name.pdf",
            content_type_header="application/pdf",
            content_disposition='attachment; filename="header-name.pdf"',
        ),
        detection=_detection(ContentKind.DOCUMENT, "application/pdf", ".pdf"),
    )

    assert filename == "header-name.pdf"


async def test_document_filename_uses_url_basename_fallback() -> None:
    filename = build_document_filename(
        url="https://example.com/path/url-name.pdf",
        hints=build_web_fetch_detection_hints(
            url="https://example.com/path/url-name.pdf",
            content_type_header="application/pdf",
            content_disposition="",
        ),
        detection=_detection(ContentKind.DOCUMENT, "application/pdf", ".pdf"),
    )

    assert filename == "url-name.pdf"


async def test_document_filename_falls_back_to_download() -> None:
    filename = build_document_filename(
        url="https://example.com/",
        hints=build_web_fetch_detection_hints(
            url="https://example.com/",
            content_type_header="application/pdf",
            content_disposition="",
        ),
        detection=_detection(ContentKind.DOCUMENT, "application/pdf", ".pdf"),
    )

    assert filename == "download.pdf"


async def test_document_filename_drops_dangerous_inner_suffix() -> None:
    filename = build_document_filename(
        url="https://example.com/report.pdf",
        hints=build_web_fetch_detection_hints(
            url="https://example.com/report.pdf",
            content_type_header="application/pdf",
            content_disposition='attachment; filename="invoice.exe.pdf"',
        ),
        detection=_detection(ContentKind.DOCUMENT, "application/pdf", ".pdf"),
    )

    assert filename == "invoice.pdf"


async def test_document_filename_detection_extension_overrides_spoofed_suffix() -> None:
    filename = build_document_filename(
        url="https://example.com/report.pdf",
        hints=build_web_fetch_detection_hints(
            url="https://example.com/report.pdf",
            content_type_header="application/pdf",
            content_disposition='attachment; filename="report.pdf"',
        ),
        detection=_detection(
            ContentKind.DOCUMENT,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".docx",
        ),
    )

    assert filename == "report.docx"


async def test_decode_text_uses_content_type_charset() -> None:
    assert decode_text_response("你好".encode("gbk"), content_type_header="text/plain; charset=gbk") == "你好"


async def test_decode_text_uses_html_meta_after_invalid_content_type_charset() -> None:
    content = '<meta charset="gbk">你好'.encode("gbk")
    assert decode_text_response(content, content_type_header="text/html; charset=not-a-codec") == '<meta charset="gbk">你好'


async def test_decode_text_uses_utf8_replace_without_charset() -> None:
    assert decode_text_response(b"ok\xff", content_type_header="text/plain") == "ok\ufffd"


class FakeDetector:
    def __init__(self, detection: ContentDetection) -> None:
        self.detection = detection

    async def detect_bytes(
        self,
        content: bytes,
        hints: DetectionHints | None = None,
    ) -> ContentDetection:
        return self.detection


def _detection(
    kind: ContentKind,
    mime_type: str,
    extension: str | None,
) -> ContentDetection:
    return ContentDetection(
        kind=kind,
        mime_type=mime_type,
        extension=extension,
        confidence=DetectionConfidence.MAGIC,
        reason="test",
        detector="fake",
    )
