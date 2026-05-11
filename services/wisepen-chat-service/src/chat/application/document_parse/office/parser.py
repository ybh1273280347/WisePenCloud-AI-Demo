from pathlib import Path
from typing import List, Optional

from chat.application.document_parse.models import DocumentParseResult
from chat.application.document_parse.office.fallback_parser import OfficeFallbackParser
from chat.application.document_parse.office.native_parser import OfficeNativeParser
from chat.application.document_parse.office.primary_parser import OfficePrimaryParser
from common.logger import log_event, log_fail, log_ok


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
        log_ok(
            "OfficeParser init",
            primary_parser_class=type(primary_parser).__name__,
            fallback_parser_class=type(fallback_parser).__name__,
            native_parser_class=type(native_parser).__name__,
            fallback_chain=" -> ".join(_FALLBACK_CHAIN),
        )

    def parse(self, path: Path, *, file_type: str) -> DocumentParseResult:
        warnings: List[str] = []
        result: Optional[DocumentParseResult] = None
        selected_parser: Optional[str] = None

        try:
            log_event(
                "OfficeParser attempt",
                path=str(path),
                file_type=file_type,
                parser=_PRIMARY_PARSER,
                handler_class=type(self.primary_parser).__name__,
            )
            result = self.primary_parser.parse(path, file_type=file_type)
            selected_parser = _PRIMARY_PARSER
            log_ok(
                "OfficeParser attempt",
                path=str(path),
                file_type=file_type,
                parser=_PRIMARY_PARSER,
                handler_class=type(self.primary_parser).__name__,
                length=len(result.text),
            )
        except Exception as exc:
            warnings.append(f"docling_failed: {type(exc).__name__}: {exc}")
            log_fail(
                "OfficeParser attempt",
                exc,
                path=str(path),
                file_type=file_type,
                parser=_PRIMARY_PARSER,
                handler_class=type(self.primary_parser).__name__,
            )

        if result is None:
            try:
                log_event(
                    "OfficeParser attempt",
                    path=str(path),
                    file_type=file_type,
                    parser=_FALLBACK_PARSER,
                    handler_class=type(self.fallback_parser).__name__,
                )
                result = self.fallback_parser.parse(path, file_type=file_type)
                selected_parser = _FALLBACK_PARSER
                log_ok(
                    "OfficeParser attempt",
                    path=str(path),
                    file_type=file_type,
                    parser=_FALLBACK_PARSER,
                    handler_class=type(self.fallback_parser).__name__,
                    length=len(result.text),
                )
            except Exception as exc:
                warnings.append(f"markitdown_failed: {type(exc).__name__}: {exc}")
                log_fail(
                    "OfficeParser attempt",
                    exc,
                    path=str(path),
                    file_type=file_type,
                    parser=_FALLBACK_PARSER,
                    handler_class=type(self.fallback_parser).__name__,
                )

        if result is None:
            try:
                log_event(
                    "OfficeParser attempt",
                    path=str(path),
                    file_type=file_type,
                    parser=_NATIVE_PARSER,
                    handler_class=type(self.native_parser).__name__,
                )
                result = self.native_parser.parse(path, file_type=file_type)
                selected_parser = _NATIVE_PARSER
                log_ok(
                    "OfficeParser attempt",
                    path=str(path),
                    file_type=file_type,
                    parser=_NATIVE_PARSER,
                    handler_class=type(self.native_parser).__name__,
                    length=len(result.text),
                )
            except Exception as exc:
                warnings.append(f"python_fallback_failed: {type(exc).__name__}: {exc}")
                log_fail(
                    "OfficeParser attempt",
                    exc,
                    path=str(path),
                    file_type=file_type,
                    parser=_NATIVE_PARSER,
                    handler_class=type(self.native_parser).__name__,
                )
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

        log_ok(
            "OfficeParser parse",
            path=str(path),
            file_type=file_type,
            selected_parser=selected_parser,
            selected_handler_class=(
                type(self.primary_parser).__name__
                if selected_parser == _PRIMARY_PARSER
                else type(self.fallback_parser).__name__
                if selected_parser == _FALLBACK_PARSER
                else type(self.native_parser).__name__
            ),
            page_count=len(result.pages),
            table_count=len(result.tables),
            warnings=len(result.warnings),
            length=len(result.text),
        )
        return result
