from pathlib import Path
from typing import List, Optional

from chat.application.document_parse import DocumentParseResult
from chat.application.document_parse.office.fallback_parser import OfficeFallbackParser
from chat.application.document_parse.office.native_parser import OfficeNativeParser
from chat.application.document_parse.office.primary_parser import OfficePrimaryParser


_PARSER_NAME = "OfficeParser"
_PRIMARY_PARSER = "docling"
_FALLBACK_PARSER = "markitdown"
_NATIVE_PARSER = "python_fallback"
_FALLBACK_CHAIN = [_PRIMARY_PARSER, _FALLBACK_PARSER, _NATIVE_PARSER]


class OfficeParser:
    def __init__(
        self,
        *,
        primary_parser: OfficePrimaryParser,
        fallback_parser: OfficeFallbackParser,
        native_parser: OfficeNativeParser,
    ):
        self.primary_parser = primary_parser
        self.fallback_parser = fallback_parser
        self.native_parser = native_parser

    def parse(self, path: Path, *, file_type: str) -> DocumentParseResult:
        warnings: List[str] = []
        result: Optional[DocumentParseResult] = None
        selected_parser: Optional[str] = None

        try:
            result = self.primary_parser.parse(path, file_type=file_type)
            selected_parser = _PRIMARY_PARSER
        except Exception as exc:
            warnings.append(f"docling_failed: {type(exc).__name__}: {exc}")

        if result is None:
            try:
                result = self.fallback_parser.parse(path, file_type=file_type)
                selected_parser = _FALLBACK_PARSER
            except Exception as exc:
                warnings.append(f"markitdown_failed: {type(exc).__name__}: {exc}")

        if result is None:
            try:
                result = self.native_parser.parse(path, file_type=file_type)
                selected_parser = _NATIVE_PARSER
            except Exception as exc:
                warnings.append(f"python_fallback_failed: {type(exc).__name__}: {exc}")
                raise RuntimeError(
                    "Office parsing failed after all fallback parsers: "
                    + " | ".join(warnings)
                ) from exc

        result.metadata["parser"] = _PARSER_NAME
        result.metadata["selected_parser"] = selected_parser
        result.metadata["fallback_chain"] = _FALLBACK_CHAIN
        result.metadata["page_count"] = len(result.pages)
        result.metadata["table_count"] = len(result.tables)
        result.warnings = warnings + result.warnings

        return result