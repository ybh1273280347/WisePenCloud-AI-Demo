import importlib.util
import asyncio
import sys
import types
from pathlib import Path
from typing import Optional

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = SERVICE_ROOT / "src"
TESTS_ROOT = SERVICE_ROOT / "tests"
OFFICE_ROOT = (
    SRC_ROOT
    / "chat"
    / "application"
    / "tools"
    / "services"
    / "document_parse"
    / "office"
)
_MISSING_MODULE = object()
_PACKAGE_STUB_NAMES = [
    "chat",
    "chat.application",
    "chat.application.tools",
    "chat.application.tools.common",
    "chat.application.tools.common.errors",
    "chat.application.tools.services",
    "chat.application.tools.services.document_parse",
    "chat.application.tools.services.document_parse.office",
    "common",
]
_MODULE_STUB_NAMES = [
    "common.logger",
    "chat.application.tools.services.document_parse.office.fallback_parser",
    "chat.application.tools.services.document_parse.office.primary_parser",
]
_LOADED_MODULE_NAMES = [
    "chat.application.tools.common.errors.document_parse",
    "chat.application.tools.services.document_parse.models",
    "chat.application.tools.services.document_parse.base",
    "chat.application.tools.services.document_parse.office.parser",
]
_TEMP_MODULE_NAMES = _PACKAGE_STUB_NAMES + _MODULE_STUB_NAMES + _LOADED_MODULE_NAMES


def _install_package_stub(name: str) -> None:
    module = types.ModuleType(name)
    module.__path__ = []
    sys.modules[name] = module


def _install_module_stub(name: str, **attrs) -> None:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {module_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_SAVED_MODULES = {
    name: sys.modules.get(name, _MISSING_MODULE) for name in _TEMP_MODULE_NAMES
}
try:
    for _package_name in _PACKAGE_STUB_NAMES:
        _install_package_stub(_package_name)

    _install_module_stub(
        "common.logger",
        log_event=lambda *args, **kwargs: None,
    )
    _install_module_stub(
        "chat.application.tools.services.document_parse.office.fallback_parser",
        OfficeFallbackParser=type("OfficeFallbackParser", (), {}),
    )
    _install_module_stub(
        "chat.application.tools.services.document_parse.office.primary_parser",
        OfficePrimaryParser=type("OfficePrimaryParser", (), {}),
    )

    _ERRORS_MODULE = _load_module(
        "chat.application.tools.common.errors.document_parse",
        SRC_ROOT
        / "chat"
        / "application"
        / "tools"
        / "common"
        / "errors"
        / "document_parse.py",
    )
    _MODELS_MODULE = _load_module(
        "chat.application.tools.services.document_parse.models",
        SRC_ROOT
        / "chat"
        / "application"
        / "tools"
        / "services"
        / "document_parse"
        / "models.py",
    )
    _load_module(
        "chat.application.tools.services.document_parse.base",
        SRC_ROOT
        / "chat"
        / "application"
        / "tools"
        / "services"
        / "document_parse"
        / "base.py",
    )
    _OFFICE_PARSER_MODULE = _load_module(
        "chat.application.tools.services.document_parse.office.parser",
        OFFICE_ROOT / "parser.py",
    )

    DocumentParseError = _ERRORS_MODULE.DocumentParseError
    UnsupportedDocumentFormatError = _ERRORS_MODULE.UnsupportedDocumentFormatError
    DocumentParseResult = _MODELS_MODULE.DocumentParseResult
    ParsedPage = _MODELS_MODULE.ParsedPage
    OfficeParser = _OFFICE_PARSER_MODULE.OfficeParser
finally:
    for _module_name, _module in _SAVED_MODULES.items():
        if _module is _MISSING_MODULE:
            sys.modules.pop(_module_name, None)
        else:
            sys.modules[_module_name] = _module


class _FakeParser:
    def __init__(self, *, text: Optional[str] = None, error: Optional[Exception] = None):
        self._text = text
        self._error = error
        self.calls = 0

    def parse(self, path: Path, *, file_type: str) -> DocumentParseResult:
        self.calls += 1
        if self._error is not None:
            raise self._error

        text = self._text
        if text is None:
            raise AssertionError("Fake parser needs text or error")

        page = ParsedPage(
            page_index=0,
            text=text,
            page_type="document",
            tables=[],
            metadata={"parser": "fake"},
        )
        return DocumentParseResult(
            text=text,
            source=str(path),
            file_type=file_type,
            pages=[page],
            tables=[],
            metadata={"parser": "fake"},
            warnings=[],
        )


def test_docling_success_does_not_call_markitdown() -> None:
    primary = _FakeParser(text="docling text")
    fallback = _FakeParser(text="markitdown text")
    parser = OfficeParser(primary_parser=primary, fallback_parser=fallback)

    result = asyncio.run(parser.parse(Path("report.docx")))

    assert primary.calls == 1
    assert fallback.calls == 0
    assert result.metadata["selected_parser"] == "docling"
    assert result.metadata["fallback_chain"] == ["docling", "markitdown"]
    assert result.metadata["table_count"] == 0
    assert result.warnings == []


def test_docling_failure_uses_markitdown_without_native_warning() -> None:
    primary = _FakeParser(error=RuntimeError("docling unavailable"))
    fallback = _FakeParser(text="markitdown text")
    parser = OfficeParser(primary_parser=primary, fallback_parser=fallback)

    result = asyncio.run(parser.parse(Path("deck.pptx")))

    assert primary.calls == 1
    assert fallback.calls == 1
    assert result.metadata["selected_parser"] == "markitdown"
    assert result.metadata["fallback_chain"] == ["docling", "markitdown"]
    assert any(warning.startswith("docling_failed:") for warning in result.warnings)
    removed_parser_name = "_".join(["python", "fallback"])
    assert not any(removed_parser_name in warning for warning in result.warnings)


def test_docling_and_markitdown_failure_raises_document_parse_error() -> None:
    primary = _FakeParser(error=RuntimeError("docling unavailable"))
    fallback = _FakeParser(error=ValueError("markitdown unavailable"))
    parser = OfficeParser(primary_parser=primary, fallback_parser=fallback)

    with pytest.raises(DocumentParseError) as exc_info:
        asyncio.run(parser.parse(Path("report.docm")))

    message = str(exc_info.value)
    assert "Office parsing failed after primary and fallback parsers:" in message
    assert "docling_failed: RuntimeError: docling unavailable" in message
    assert "markitdown_failed: ValueError: markitdown unavailable" in message
    removed_warning_name = "_".join(["python", "fallback", "failed"])
    assert removed_warning_name not in message
    assert primary.calls == 1
    assert fallback.calls == 1


def test_unsupported_suffix_raises() -> None:
    parser = OfficeParser(
        primary_parser=_FakeParser(text="docling text"),
        fallback_parser=_FakeParser(text="markitdown text"),
    )

    with pytest.raises(UnsupportedDocumentFormatError):
        asyncio.run(parser.parse(Path("notes.txt")))


def test_removed_native_office_parser_references_are_absent() -> None:
    removed_terms = [
        "".join(["Office", "Native", "Parser"]),
        "_".join(["python", "fallback"]),
        "_".join(["python", "docx"]),
        "_".join(["python", "pptx"]),
        ".".join(["office", "native_parser"]),
    ]
    paths = list(SRC_ROOT.rglob("*.py")) + list(TESTS_ROOT.rglob("*.py"))

    offenders = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for term in removed_terms:
            if term in text:
                offenders.append(f"{path.relative_to(SERVICE_ROOT)}: {term}")

    assert offenders == []
