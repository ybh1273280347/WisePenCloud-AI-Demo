from dataclasses import replace
from pathlib import Path
from typing import List, Optional

from chat.application.tools.services.document_parse.base import BaseDocumentParser
from chat.application.tools.common.errors.document_parse import (
    DocumentParseError,
    UnsupportedDocumentFormatError,
)
from chat.application.tools.services.document_parse.models import DocumentParseResult
from chat.application.tools.services.document_parse.office.fallback_parser import OfficeFallbackParser
from chat.application.tools.services.document_parse.office.primary_parser import OfficePrimaryParser
from common.logger import log_event

_PARSER_NAME = "OfficeParser"
_PRIMARY_PARSER = "docling"
_FALLBACK_PARSER = "markitdown"
_FALLBACK_CHAIN = [_PRIMARY_PARSER, _FALLBACK_PARSER]

_SUFFIX_TO_FILE_TYPE = {
    ".docx": "docx",
    ".docm": "docx",
    ".pptx": "pptx",
    ".pptm": "pptx",
}


class OfficeParser(BaseDocumentParser):
    supported_extensions = (".docx", ".docm", ".pptx", ".pptm")

    def __init__(
        self,
        *,
        primary_parser: OfficePrimaryParser,
        fallback_parser: OfficeFallbackParser,
    ):
        self.primary_parser = primary_parser
        self.fallback_parser = fallback_parser
        log_event(
            "OfficeParser 初始化",
            primary_parser_class=type(primary_parser).__name__,
            fallback_parser_class=type(fallback_parser).__name__,
            fallback_chain=" -> ".join(_FALLBACK_CHAIN),
        )

    async def parse(self, path: Path) -> DocumentParseResult:
        file_type = _SUFFIX_TO_FILE_TYPE.get(path.suffix.lower())
        if file_type is None:
            raise UnsupportedDocumentFormatError(path.suffix)
        return self._parse_sync(path, file_type=file_type)

    def _parse_sync(self, path: Path, *, file_type: str) -> DocumentParseResult:
        warnings: List[str] = []
        result: Optional[DocumentParseResult] = None
        selected_parser: Optional[str] = None

        try:
            log_event(
                "OfficeParser 尝试",
                path=str(path),
                file_type=file_type,
                parser=_PRIMARY_PARSER,
                handler_class=type(self.primary_parser).__name__,
            )
            result = self.primary_parser.parse(path, file_type=file_type)
            selected_parser = _PRIMARY_PARSER
            log_event(
                "OfficeParser 尝试完成",
                path=str(path),
                file_type=file_type,
                parser=_PRIMARY_PARSER,
                handler_class=type(self.primary_parser).__name__,
                length=len(result.text),
            )
        except Exception as e:
            warnings.append(f"docling_failed: {type(e).__name__}: {e}")
            log_event(
                "OfficeParser 尝试未完成",
                error=repr(e),
                path=str(path),
                file_type=file_type,
                parser=_PRIMARY_PARSER,
                handler_class=type(self.primary_parser).__name__,
            )

        if result is None:
            try:
                log_event(
                    "OfficeParser 尝试",
                    path=str(path),
                    file_type=file_type,
                    parser=_FALLBACK_PARSER,
                    handler_class=type(self.fallback_parser).__name__,
                )
                result = self.fallback_parser.parse(path, file_type=file_type)
                selected_parser = _FALLBACK_PARSER
                log_event(
                    "OfficeParser 尝试完成",
                    path=str(path),
                    file_type=file_type,
                    parser=_FALLBACK_PARSER,
                    handler_class=type(self.fallback_parser).__name__,
                    length=len(result.text),
                )
            except Exception as e:
                warnings.append(f"markitdown_failed: {type(e).__name__}: {e}")
                log_event(
                    "OfficeParser 尝试未完成",
                    error=repr(e),
                    path=str(path),
                    file_type=file_type,
                    parser=_FALLBACK_PARSER,
                    handler_class=type(self.fallback_parser).__name__,
                )

        if result is None:
            raise DocumentParseError(
                "Office parsing failed after primary and fallback parsers: "
                + " | ".join(warnings)
            )

        result_metadata = {
            **result.metadata,
            "parser": _PARSER_NAME,
            "selected_parser": selected_parser,
            "fallback_chain": _FALLBACK_CHAIN,
            "page_count": len(result.pages),
            "table_count": len(result.tables),
        }
        result = replace(
            result,
            metadata=result_metadata,
            warnings=warnings + result.warnings,
        )

        log_event(
            "OfficeParser 完成",
            path=str(path),
            file_type=file_type,
            selected_parser=selected_parser,
            selected_handler_class=(
                type(self.primary_parser).__name__
                if selected_parser == _PRIMARY_PARSER
                else type(self.fallback_parser).__name__
            ),
            page_count=len(result.pages),
            table_count=len(result.tables),
            warnings=len(result.warnings),
            length=len(result.text),
        )
        return result
