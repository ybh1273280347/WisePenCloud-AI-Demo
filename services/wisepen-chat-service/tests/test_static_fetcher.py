import io
import zipfile

import httpx
import pytest

from chat.application.content_detection import (
    ContentDetection,
    ContentKind,
    DetectionConfidence,
    DetectionHints,
)
from chat.application.web_fetch.errors import UnsupportedMediaError
from chat.application.web_fetch.fetcher import static_fetcher as static_fetcher_module
from chat.application.web_fetch.fetcher.static_fetcher import StaticFetcher
from chat.application.web_fetch.models import FetchedDocument

pytestmark = pytest.mark.asyncio


async def test_static_fetcher_pdf_response_returns_document() -> None:
    fetcher = _fetcher_with_transport(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=b"%PDF-1.7",
                request=request,
            )
        )
    )

    try:
        result = await fetcher.fetch("https://example.com/report.pdf")
    finally:
        await fetcher.close()

    assert isinstance(result, FetchedDocument)
    assert result.media_type == "application/pdf"
    assert result.filename == "report.pdf"


async def test_static_fetcher_html_response_returns_text() -> None:
    fetcher = _fetcher_with_transport(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                content=b"<html>ok</html>",
                request=request,
            )
        )
    )

    try:
        result = await fetcher.fetch("https://example.com/page")
    finally:
        await fetcher.close()

    assert result == "<html>ok</html>"


async def test_static_fetcher_image_response_does_not_read_body() -> None:
    stream = TrackingStream(b"image bytes")
    fetcher = _fetcher_with_transport(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "image/png"},
                stream=stream,
                request=request,
            )
        )
    )

    try:
        with pytest.raises(UnsupportedMediaError):
            await fetcher.fetch("https://example.com/image.png")
    finally:
        await fetcher.close()

    assert stream.was_read is False


async def test_static_fetcher_ordinary_zip_returns_none() -> None:
    fetcher = _fetcher_with_transport(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "application/octet-stream"},
                content=_zip_bytes({"file.txt": b"hello"}),
                request=request,
            )
        )
    )

    try:
        result = await fetcher.fetch("https://example.com/not-really.pdf")
    finally:
        await fetcher.close()

    assert result is None


async def test_static_fetcher_unknown_binary_returns_none() -> None:
    fetcher = _fetcher_with_transport(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "application/octet-stream"},
                content=b"\x00\x01\x02\x03" * 32,
                request=request,
            )
        ),
        detector=FakeDetector(_detection(ContentKind.UNSUPPORTED_BINARY, "application/octet-stream", None)),
    )

    try:
        result = await fetcher.fetch("https://example.com/file.txt")
    finally:
        await fetcher.close()

    assert result is None


async def test_static_fetcher_body_size_limit_still_applies() -> None:
    stream = TrackingStream(b"x" * 32)
    fetcher = _fetcher_with_transport(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/plain", "content-length": "32"},
                stream=stream,
                request=request,
            )
        ),
        max_response_bytes=8,
    )

    try:
        result = await fetcher.fetch("https://example.com/file.txt")
    finally:
        await fetcher.close()

    assert result is None
    assert stream.was_read is False


async def test_static_fetcher_redirect_url_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    validated_urls: List[str] = []

    def fake_validate(url: str) -> str:
        validated_urls.append(url)
        return url

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.com/start":
            return httpx.Response(
                302,
                headers={"location": "https://example.org/final"},
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"redirected",
            request=request,
        )

    monkeypatch.setattr(static_fetcher_module, "validate_public_http_url", fake_validate)
    fetcher = _fetcher_with_transport(
        httpx.MockTransport(handler),
        detector=FakeDetector(_detection(ContentKind.TEXT, "text/plain", ".txt")),
    )

    try:
        result = await fetcher.fetch("https://example.com/start")
    finally:
        await fetcher.close()

    assert result == "redirected"
    assert validated_urls == ["https://example.org/final"]


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.was_read = False

    async def __aiter__(self):
        self.was_read = True
        yield self.content


class FakeDetector:
    def __init__(self, detection: ContentDetection) -> None:
        self.detection = detection

    async def detect_bytes(
        self,
        content: bytes,
        hints: DetectionHints | None = None,
    ) -> ContentDetection:
        return self.detection


def _fetcher_with_transport(
    transport: httpx.AsyncBaseTransport,
    *,
    detector=None,
    max_response_bytes: int = 50 * 1024 * 1024,
) -> StaticFetcher:
    fetcher = StaticFetcher(
        max_response_bytes=max_response_bytes,
        content_detector=detector,
    )
    fetcher._client = httpx.AsyncClient(transport=transport, follow_redirects=False)
    return fetcher


def _detection(
    kind: ContentKind,
    mime_type: str,
    extension: str | None,
) -> ContentDetection:
    return ContentDetection(
        kind=kind,
        mime_type=mime_type,
        extension=extension,
        confidence=DetectionConfidence.FALLBACK,
        reason="test",
        detector="fake",
    )


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return buffer.getvalue()
