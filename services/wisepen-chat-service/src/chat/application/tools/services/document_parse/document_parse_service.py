import asyncio
import tempfile
from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from typing import List, Optional

from chat.application.tools.common.errors.document_parse import (
    DocumentParseError,
    EmptyParsedContentError,
    UnsupportedDocumentFormatError,
)
from chat.application.tools.services.document_parse.models import DocumentParseResult
from chat.application.tools.services.document_parse.epub.parser import EpubParser
from chat.application.tools.services.document_parse.office.parser import OfficeParser
from chat.application.tools.services.document_parse.pdf.parser import PdfParser
from chat.application.tools.services.document_parse.spreadsheet.parser import (
    SpreadsheetParser,
)
from chat.application.tools.services.document_parse.suffixes import (
    DOCUMENT_TYPE_DOCX,
    DOCUMENT_TYPE_EPUB,
    DOCUMENT_TYPE_PDF,
    DOCUMENT_TYPE_PPTX,
    DOCUMENT_TYPE_SPREADSHEET,
    detect_document_type_by_suffix,
)
from common.logger import log_error, log_event

_PARSE_CONCURRENCY = 4


@dataclass(slots=True)
class DocumentParseResultItem:
    file_ref: str
    success: bool
    result: Optional[DocumentParseResult] = None
    error: Optional[str] = None


class DocumentParseService:
    """文档解析服务。"""

    def __init__(
        self,
        *,
        pdf_parser: PdfParser,
        office_parser: OfficeParser,
        epub_parser: EpubParser,
        spreadsheet_parser: SpreadsheetParser,
    ):
        self.pdf_parser = pdf_parser
        self.office_parser = office_parser
        self.epub_parser = epub_parser
        self.spreadsheet_parser = spreadsheet_parser
        log_event(
            "document_parse service 初始化",
            pdf_parser=type(pdf_parser).__name__,
            office_parser=type(office_parser).__name__,
            epub_parser=type(epub_parser).__name__,
            spreadsheet_parser=type(spreadsheet_parser).__name__,
        )

    async def parse_path(self, path: Path) -> DocumentParseResult:
        if not path.is_file():
            raise FileNotFoundError(f"Document file not found: {path}")

        document_type = detect_document_type_by_suffix(path)
        handler = self._handler_for_document_type(document_type)
        log_event(
            "document_parse 路由开始",
            path=str(path),
            suffix=path.suffix.lower(),
            document_type=document_type,
            handler_class=type(handler).__name__,
        )

        if document_type == DOCUMENT_TYPE_PDF:
            result = await handler.parse(path)
        elif document_type in {DOCUMENT_TYPE_DOCX, DOCUMENT_TYPE_PPTX}:
            result = await handler.parse(path)
        elif document_type == DOCUMENT_TYPE_EPUB:
            result = await handler.parse(path)
        elif document_type == DOCUMENT_TYPE_SPREADSHEET:
            result = await handler.parse(path)
        else:
            raise UnsupportedDocumentFormatError(document_type)

        if not result.text.strip():
            raise EmptyParsedContentError(str(path))

        log_event(
            "document_parse 路由完成",
            path=str(path),
            document_type=document_type,
            handler_class=type(handler).__name__,
            parser=result.metadata.get("parser"),
            selected_parser=result.metadata.get("selected_parser"),
            page_count=len(result.pages),
            table_count=len(result.tables),
            length=len(result.text),
        )
        return result

    def _handler_for_document_type(self, document_type: str):
        if document_type == DOCUMENT_TYPE_PDF:
            return self.pdf_parser
        if document_type in {DOCUMENT_TYPE_DOCX, DOCUMENT_TYPE_PPTX}:
            return self.office_parser
        if document_type == DOCUMENT_TYPE_EPUB:
            return self.epub_parser
        if document_type == DOCUMENT_TYPE_SPREADSHEET:
            return self.spreadsheet_parser
        raise UnsupportedDocumentFormatError(document_type)

    async def parse_bytes(self, data: bytes, *, filename: str) -> DocumentParseResult:
        suffix = Path(filename).suffix.lower()
        if not suffix:
            raise UnsupportedDocumentFormatError("missing file suffix")

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)

        try:
            return await self.parse_path(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    async def parse_many(
        self, paths: List[Path], *, file_refs: Optional[List[str]] = None
    ) -> List[DocumentParseResultItem]:
        """并发解析多个文件，返回与输入顺序一致的结果列表。单个文件失败不影响其他。"""
        ref_list = [
            file_refs[i] if file_refs and i < len(file_refs) else str(p)
            for i, p in enumerate(paths)
        ]
        log_event("document_parse 批量开始", 总数=len(paths), file_refs=ref_list)
        t0 = asyncio.get_event_loop().time()

        semaphore = asyncio.Semaphore(_PARSE_CONCURRENCY)

        async def _parse_one(index: int, path: Path) -> DocumentParseResultItem:
            ref = (
                file_refs[index] if file_refs and index < len(file_refs) else str(path)
            )
            async with semaphore:
                try:
                    result = await self.parse_path(path)
                    return DocumentParseResultItem(
                        file_ref=ref, success=True, result=result
                    )
                except FileNotFoundError as e:
                    return DocumentParseResultItem(
                        file_ref=ref, success=False, error=str(e)
                    )
                except DocumentParseError as e:
                    return DocumentParseResultItem(
                        file_ref=ref, success=False, error=str(e)
                    )
                except FrozenInstanceError as e:
                    log_error(
                        "document_parse internal frozen state update error",
                        e,
                        file_ref=ref,
                        path=str(path),
                    )
                    return DocumentParseResultItem(
                        file_ref=ref,
                        success=False,
                        error=(
                            "解析服务内部状态更新异常: FrozenInstanceError. "
                            "This is an internal parser state mutation bug, not a PDF encryption, corruption, or scanned-page problem."
                        ),
                    )
                except Exception as e:
                    log_error(
                        "document_parse unexpected error",
                        e,
                        file_ref=ref,
                        path=str(path),
                    )
                    return DocumentParseResultItem(
                        file_ref=ref,
                        success=False,
                        error=f"未预期异常: {e.__class__.__name__}",
                    )

        tasks = [_parse_one(i, p) for i, p in enumerate(paths)]
        results = list(await asyncio.gather(*tasks))

        elapsed_ms = int((asyncio.get_event_loop().time() - t0) * 1000)
        success_count = sum(1 for r in results if r.success)
        fail_count = len(results) - success_count
        failed_refs = [r.file_ref for r in results if not r.success]
        log_event(
            "document_parse 批量结束",
            总数=len(paths),
            已完成=success_count,
            未完成=fail_count,
            未完成_file_refs=failed_refs,
            耗时_ms=elapsed_ms,
        )

        return results
