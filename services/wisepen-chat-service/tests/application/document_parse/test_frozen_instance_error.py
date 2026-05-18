import asyncio
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from chat.application.tools.services.document_convert.errors import FileConvertError
from chat.application.tools.services.document_convert.service import DocumentConvertService
from chat.application.tools.services.document_file import DocumentTempFileResolver
from chat.application.tools.services.document_parse.document_parse_service import (
    DocumentParseService,
)
from chat.application.tools.services.document_parse.pdf.parser import _PageDraft


def test_pdf_page_draft_is_mutable_parser_state() -> None:
    draft = _PageDraft(page_index=0, page_type="scanned", text="")

    draft.text = "ocr text"
    draft.ocr_used = True

    assert draft.text == "ocr text"
    assert draft.ocr_used is True


def test_parse_many_reports_frozen_state_update_as_internal_error(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        source = tmp_path / "source.pdf"
        source.write_bytes(b"%PDF-1.7")
        service = DocumentParseService(
            pdf_parser=FrozenParser(),
            office_parser=object(),
            epub_parser=object(),
            spreadsheet_parser=object(),
        )

        results = await service.parse_many([source], file_refs=["file-ref"])

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error is not None
        assert "解析服务内部状态更新异常: FrozenInstanceError" in results[0].error
        assert "not a PDF encryption, corruption, or scanned-page problem" in results[0].error

    asyncio.run(run())


def test_document_convert_reports_frozen_state_update_as_internal_error(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        source = tmp_path / "user" / "session" / "source.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"%PDF-1.7")
        service = DocumentConvertService(
            parse_service=FrozenParseService(),
            export_service=object(),
            temp_file_resolver=DocumentTempFileResolver(temp_root=tmp_path),
        )

        with pytest.raises(FileConvertError) as exc_info:
            await service.convert_document(
                user_id="user",
                session_id="session",
                file_ref=str(source),
                target_format="docx",
            )

        assert "解析服务内部状态更新异常: FrozenInstanceError" in str(exc_info.value)
        assert "not a source document format problem" in str(exc_info.value)

    asyncio.run(run())


class FrozenParser:
    async def parse(self, path: Path):
        raise FrozenInstanceError("cannot assign to field 'text'")


class FrozenParseService:
    async def parse_path(self, path: Path):
        raise FrozenInstanceError("cannot assign to field 'text'")
