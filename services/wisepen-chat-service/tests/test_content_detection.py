import io
import sys
import zipfile
from pathlib import Path

import pytest

from chat.application.tools.common.content_detection import (
    ContentDetection,
    ContentDetector,
    ContentKind,
    DetectionConfidence,
    DetectionHints,
    drop_dangerous_inner_suffix,
    sanitize_download_filename,
)
from chat.application.tools.common.content_detection.magika_detector import MagikaDetector
from chat.application.tools.common.content_detection.puremagic_detector import PureMagicDetector


pytestmark = pytest.mark.asyncio


async def test_pdf_magic_document() -> None:
    detection = await ContentDetector().detect_bytes(b"%PDF-1.7\ntext", DetectionHints(filename="x.txt"))
    assert detection.kind == ContentKind.DOCUMENT
    assert detection.extension == ".pdf"


@pytest.mark.parametrize(
    ("content", "extension"),
    [
        (b"\x89PNG\r\n\x1a\npayload", ".png"),
        (b"\xff\xd8\xff\xe0payload", ".jpg"),
        (b"RIFF\x00\x00\x00\x00WEBPpayload", ".webp"),
        (b"GIF89apayload", ".gif"),
        (b"BMpayload", ".bmp"),
        (b"II*\x00payload", ".tiff"),
    ],
)
async def test_image_magic(content: bytes, extension: str) -> None:
    detection = await ContentDetector().detect_bytes(content, DetectionHints(filename="wrong.txt"))
    assert detection.kind == ContentKind.IMAGE
    assert detection.extension == extension


async def test_html_payload() -> None:
    detection = await ContentDetector().detect_bytes(b" <!doctype html><html></html>")
    assert detection.kind == ContentKind.HTML


async def test_json_payload() -> None:
    detection = await ContentDetector().detect_bytes(b'{"ok": true}')
    assert detection.kind == ContentKind.JSON


async def test_xml_payload() -> None:
    detection = await ContentDetector().detect_bytes(b'<?xml version="1.0"?><root/>')
    assert detection.kind == ContentKind.XML


@pytest.mark.parametrize(
    "content",
    [
        b"plain text with no extension\nsecond line",
        b"def main():\n    return 'hello'\n",
    ],
)
async def test_text_without_extension(content: bytes) -> None:
    detection = await ContentDetector().detect_bytes(content)
    assert detection.kind == ContentKind.TEXT


async def test_wrong_extension_pdf_wins() -> None:
    detection = await ContentDetector().detect_bytes(b"%PDF-1.7", DetectionHints(filename="note.txt"))
    assert detection.kind == ContentKind.DOCUMENT


@pytest.mark.parametrize("filename", ["photo.jpg", "paper.pdf"])
async def test_wrong_extension_text_wins(filename: str) -> None:
    detection = await ContentDetector().detect_bytes(
        b"this is really plain text with enough bytes\n",
        DetectionHints(filename=filename),
    )
    assert detection.kind == ContentKind.TEXT


async def test_plain_zip_unsupported_archive() -> None:
    detection = await ContentDetector().detect_bytes(_zip_bytes({"file.txt": b"hello"}))
    assert detection.kind == ContentKind.UNSUPPORTED_ARCHIVE


async def test_zip_result_is_not_overridden_by_ai_detector() -> None:
    class AlwaysImageDetector:
        async def detect_bytes(self, content: bytes) -> ContentDetection:
            return ContentDetection(
                ContentKind.IMAGE,
                "image/png",
                ".png",
                DetectionConfidence.AI,
                "fake_ai_image",
                "fake_ai",
            )

    detection = await ContentDetector(magika_detector=AlwaysImageDetector()).detect_bytes(_zip_bytes({"file.txt": b"hello"}))
    assert detection.kind == ContentKind.UNSUPPORTED_ARCHIVE
    assert detection.detector == "zip_classifier"


@pytest.mark.parametrize(
    ("members", "extension"),
    [
        ({"word/document.xml": b"<w:document/>"}, ".docx"),
        ({"xl/workbook.xml": b"<workbook/>"}, ".xlsx"),
        ({"ppt/presentation.xml": b"<presentation/>"}, ".pptx"),
        ({"mimetype": b"application/epub+zip", "OPS/content.opf": b""}, ".epub"),
        ({"mimetype": b"application/vnd.oasis.opendocument.spreadsheet", "content.xml": b""}, ".ods"),
    ],
)
async def test_zip_document_types(members: dict[str, bytes], extension: str) -> None:
    detection = await ContentDetector().detect_bytes(_zip_bytes(members))
    assert detection.kind == ContentKind.DOCUMENT
    assert detection.extension == extension


async def test_odt_is_unsupported_archive() -> None:
    detection = await ContentDetector().detect_bytes(
        _zip_bytes({"mimetype": b"application/vnd.oasis.opendocument.text", "content.xml": b""})
    )
    assert detection.kind == ContentKind.UNSUPPORTED_ARCHIVE
    assert detection.reason == "unsupported_odt"


async def test_zip_path_traversal_is_unsupported_archive() -> None:
    detection = await ContentDetector().detect_bytes(_zip_bytes({"../escape.txt": b"no"}))
    assert detection.kind == ContentKind.UNSUPPORTED_ARCHIVE
    assert detection.reason == "zip_unsafe_path"


async def test_zip_bomb_metadata_is_unsupported_archive() -> None:
    detection = await ContentDetector().detect_bytes(_zip_bytes({"huge.txt": b"0" * (2 * 1024 * 1024)}))
    assert detection.kind == ContentKind.UNSUPPORTED_ARCHIVE
    assert detection.reason == "zip_compression_ratio_too_high"


async def test_nested_archive_is_unsupported_archive() -> None:
    detection = await ContentDetector().detect_bytes(_zip_bytes({"inner.zip": b"PK\x05\x06"}))
    assert detection.kind == ContentKind.UNSUPPORTED_ARCHIVE
    assert detection.reason == "zip_nested_archive"


async def test_detect_path_reads_file(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_bytes(b"hello from disk\n")
    detection = await ContentDetector().detect_path(path)
    assert detection.kind == ContentKind.TEXT


async def test_magika_import_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = __import__

    def fake_import(name: str, *args, **kwargs):
        if name == "magika":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    detection = await ContentDetector().detect_bytes(b"%PDF-1.7")
    assert detection.kind == ContentKind.DOCUMENT


async def test_magika_low_confidence_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    class LowConfidenceMagika:
        def identify_bytes(self, content: bytes):
            return {"output": {"label": "jpg", "mime_type": "image/jpeg", "extension": "jpg", "score": 0.1}}

    detector = MagikaDetector()
    monkeypatch.setattr(detector, "_magika", LowConfidenceMagika())
    detection = await ContentDetector(magika_detector=detector).detect_bytes(b"plain text still wins\n")
    assert detection.kind == ContentKind.TEXT


async def test_puremagic_import_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = __import__

    def fake_import(name: str, *args, **kwargs):
        if name == "puremagic":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    detection = await PureMagicDetector().detect_bytes(b"plain text fallback")
    assert detection is None


async def test_puremagic_import_failure_full_chain_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = __import__

    def fake_import(name: str, *args, **kwargs):
        if name == "puremagic":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    detection = await ContentDetector().detect_bytes(b"plain text fallback\n")
    assert detection.kind == ContentKind.TEXT


async def test_filename_sanitize_matches_web_fetch_behavior() -> None:
    assert sanitize_download_filename("../report.pdf") == "report.pdf"
    assert sanitize_download_filename("\x00..") == "download"
    assert sanitize_download_filename("~") == "download"


async def test_dangerous_inner_suffix_is_dropped() -> None:
    assert drop_dangerous_inner_suffix("invoice.exe.pdf") == "invoice.pdf"
    assert drop_dangerous_inner_suffix("invoice.safe.pdf") == "invoice.safe.pdf"


async def test_python_magic_not_imported_or_declared() -> None:
    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert "python-magic" not in text
    assert "filemagic" not in text
    assert "magicfile" not in text
    assert "magic" not in sys.modules


def _zip_bytes(members: dict[str, bytes], infos: dict[str, zipfile.ZipInfo] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            info = infos.get(name) if infos else None
            if info is None:
                zf.writestr(name, content)
            else:
                zf.writestr(info, content)
    return buffer.getvalue()
