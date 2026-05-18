import importlib
import inspect
from pathlib import Path

import pytest

from chat.application.tools.services.document_parse.factory import build_document_parse_service
from chat.application.tools.services.document_parse.pdf.parser import PdfParser
from chat.application.tools.common.ocr import OcrImageAdapter, OcrProcessor, OcrResult


PROJECT_ROOT = Path(__file__).parents[1]
SRC_ROOT = PROJECT_ROOT / "src"


def test_public_ocr_imports_are_available() -> None:
    assert OcrImageAdapter.__name__ == "OcrImageAdapter"
    assert OcrProcessor.__name__ == "OcrProcessor"
    assert OcrResult.__name__ == "OcrResult"


def test_old_document_parse_ocr_import_path_is_removed() -> None:
    old_module = ".".join(["chat", "application", "document_parse", "ocr"])

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(old_module)


def test_build_document_parse_service_still_injects_ocr_adapter() -> None:
    processor = OcrProcessor(enabled=False)

    service = build_document_parse_service(local_ocr_processor=processor)

    assert isinstance(service.pdf_parser.ocr_adapter, OcrImageAdapter)
    assert service.pdf_parser.ocr_adapter.local_ocr_processor is processor


def test_pdf_parser_accepts_public_ocr_adapter() -> None:
    processor = OcrProcessor(enabled=False)
    adapter = OcrImageAdapter(local_ocr_processor=processor)

    parser = PdfParser(
        classifier=object(),
        text_extractor=object(),
        page_renderer=object(),
        table_extractor=object(),
        ocr_adapter=adapter,
        scanned_table_extractor=object(),
    )

    assert parser.ocr_adapter is adapter


def test_ocr_processor_worker_module_path_uses_public_module() -> None:
    source = inspect.getsource(OcrProcessor._start_worker)

    assert "chat.application.tools.common.ocr.worker" in source
    assert ".".join(["chat", "application", "document_parse", "ocr", "worker"]) not in source


def test_old_ocr_directory_is_removed() -> None:
    assert not (SRC_ROOT / "chat" / "application" / "document_parse" / "ocr").exists()


def test_business_code_has_no_old_ocr_import_path() -> None:
    forbidden = [
        ".".join(["chat", "application", "document_parse", "ocr"]),
        ".".join(["document_parse", "ocr"]),
    ]

    for path in _iter_python_files(PROJECT_ROOT):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, str(path)


def test_no_forbidden_ocr_backends_or_abstractions_are_introduced() -> None:
    forbidden = [
        "py" + "tesseract",
        "Tess" + "eract",
        "Ocr" + "Engine",
    ]

    for path in _iter_python_files(PROJECT_ROOT):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, str(path)


def test_container_wires_attachment_read_to_shared_ocr_adapter() -> None:
    document_parse_source = (
        SRC_ROOT / "chat" / "container_providers" / "document_parse.py"
    ).read_text(encoding="utf-8")
    attachment_read_source = (
        SRC_ROOT / "chat" / "container_providers" / "attachment_read.py"
    ).read_text(encoding="utf-8")

    assert "from chat.application.tools.common.ocr import OcrImageAdapter, OcrProcessor" in document_parse_source
    assert "container_cls.ocr_processor = providers.Singleton(" in document_parse_source
    assert "container_cls.ocr_image_adapter = providers.Singleton(" in document_parse_source
    assert "local_ocr_processor=container_cls.ocr_processor" in document_parse_source
    assert "ocr_image_adapter=container_cls.ocr_image_adapter" in attachment_read_source
    assert document_parse_source.count("providers.Singleton(\n        OcrProcessor") == 1
    assert ".".join(["chat", "application", "document_parse", "ocr"]) not in document_parse_source
    assert ".".join(["chat", "application", "document_parse", "ocr"]) not in attachment_read_source


def _iter_python_files(root: Path):
    for path in [root / "src", root / "tests"]:
        for candidate in path.rglob("*.py"):
            if "__pycache__" in candidate.parts:
                continue
            yield candidate
