from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from chat.application.document_export import (
    FILE_EXTENSIONS,
    SUPPORTED_EXPORT_FORMATS,
    DocumentExportError,
    DocumentExportService,
    ExportOptions,
    GeneratedDocumentFile,
)
from chat.application.tools.common.errors.document_parse import (
    DocumentParseError as ParseServiceError,
)
from chat.application.tools.services.document_file import (
    DocumentTempFileResolver,
    InvalidDocumentRefError as FileInvalidDocumentRefError,
    ResolvedDocumentSource,
    UnreadableDocumentRefError as FileUnreadableDocumentRefError,
    document_processing_scope,
)
from common.logger import log_error, log_event

from .errors import (
    DocumentConvertError,
    DocumentDecodeError,
    DocumentExportError as ConvertDocumentExportError,
    DocumentInternalError,
    DocumentParseError,
    EmptyParsedMarkdownError,
    FileConvertError,
    InvalidDocumentRefError,
    UnreadableDocumentRefError,
    UnsupportedDocumentFormatError,
    UnsupportedDocumentRouteError,
)

if TYPE_CHECKING:
    from chat.application.tools.services.document_parse import DocumentParseService


_TEXT_READ_BYTES_LIMIT = 20 * 1024 * 1024
_TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk")
_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
_TEXT_SUFFIXES = frozenset({".txt"})
_HTML_SUFFIXES = frozenset({".html", ".htm"})
_PARSE_EXPORT_SUFFIXES = frozenset(
    {
        ".pdf",
        ".docx",
        ".docm",
        ".pptx",
        ".pptm",
        ".epub",
        ".xlsx",
        ".xls",
        ".xlsm",
        ".ods",
    }
)
_TEXT_EXPORT_TARGETS = frozenset({"markdown", "html", "pdf", "docx", "txt"})
_PARSE_EXPORT_TARGETS = frozenset({"markdown", "html", "pdf", "docx", "txt"})


@dataclass(frozen=True, slots=True)
class NormalizedDocumentConvertRequest:
    source_file_ref: str
    output_file_name: Optional[str]
    target_format: str
    user_id: str
    session_id: str
    title: Optional[str]
    reference_docx_file_ref: Optional[str]
    reference_docx_path: Optional[Path]


@dataclass(frozen=True, slots=True)
class _DocumentConvertRoute:
    route_kind: str
    requires_parse: bool
    export_source_format: str


@dataclass(frozen=True, slots=True)
class DocumentConvertService:
    parse_service: DocumentParseService
    export_service: DocumentExportService
    temp_file_resolver: DocumentTempFileResolver

    async def convert_document(
        self,
        *,
        session_id: str,
        user_id: str,
        file_ref: str,
        target_format: str,
        file_name: Optional[str] = None,
        title: Optional[str] = None,
        reference_docx_file_ref: Optional[str] = None,
    ) -> GeneratedDocumentFile:
        request = normalize_convert_request(
            file_ref=file_ref,
            file_name=file_name,
            target_format=target_format,
            user_id=user_id,
            session_id=session_id,
            title=title,
            reference_docx_file_ref=reference_docx_file_ref,
        )
        try:
            source = self.temp_file_resolver.resolve(
                file_ref=request.source_file_ref,
                user_id=request.user_id,
                session_id=request.session_id,
            )
        except FileInvalidDocumentRefError as exc:
            log_error(
                "document_convert file_ref invalid",
                exc,
                user_id=user_id,
                session_id=session_id,
                file_ref=file_ref,
            )
            raise InvalidDocumentRefError() from exc
        except FileUnreadableDocumentRefError as exc:
            log_error(
                "document_convert file_ref unreadable",
                exc,
                user_id=user_id,
                session_id=session_id,
                file_ref=file_ref,
            )
            raise UnreadableDocumentRefError() from exc

        reference_docx_path = self._resolve_reference_docx_path(request)
        request = replace(request, reference_docx_path=reference_docx_path)

        with document_processing_scope(
            self.temp_file_resolver.session_root(
                user_id=request.user_id,
                session_id=request.session_id,
            )
        ):
            return await self._convert_resolved_source(
                request=request,
                source=source,
            )

    async def _convert_resolved_source(
        self,
        *,
        request: NormalizedDocumentConvertRequest,
        source: ResolvedDocumentSource,
    ) -> GeneratedDocumentFile:
        source_format = detect_source_format_from_path(source.path)
        route = self._resolve_route(
            source_format=source_format,
            target_format=request.target_format,
        )
        log_event(
            "document_convert route resolved",
            user_id=request.user_id,
            session_id=request.session_id,
            source_path=str(source.path),
            source_format=source_format,
            target_format=request.target_format,
            output_file_name=request.output_file_name,
            size_bytes=source.size_bytes,
            route_kind=route.route_kind,
            requires_parse=route.requires_parse,
            export_source_format=route.export_source_format,
            title_provided=request.title is not None,
            reference_docx_used=request.reference_docx_path is not None,
        )

        if source_format in {"markdown", "plain_text", "html"}:
            if request.target_format not in _TEXT_EXPORT_TARGETS:
                raise UnsupportedDocumentRouteError(
                    f"unsupported route: {source_format}->{request.target_format}"
                )
            content = await self._read_text_source(source.path)
            if not content.strip():
                raise EmptyParsedMarkdownError()
            return await self._export_content(
                request=request,
                content=content,
                source_format=source_format,
            )

        if source_format in _PARSE_EXPORT_SUFFIXES:
            if request.target_format not in _PARSE_EXPORT_TARGETS:
                raise UnsupportedDocumentRouteError(
                    f"unsupported route: {source_format}->{request.target_format}"
                )
            markdown = await self._parse_source_to_markdown(
                source=source,
                target_format=request.target_format,
            )
            if not markdown.strip():
                raise EmptyParsedMarkdownError()
            return await self._export_markdown(
                request=request,
                markdown=markdown,
            )

        raise UnsupportedDocumentRouteError(
            f"unsupported route: {source_format}->{request.target_format}"
        )

    async def _export_content(
        self,
        *,
        request: NormalizedDocumentConvertRequest,
        content: str,
        source_format: str,
    ) -> GeneratedDocumentFile:
        export_source_format = "plain_text" if source_format == "plain_text" else "markdown"
        try:
            return await self.export_service.export_content(
                user_id=request.user_id,
                session_id=request.session_id,
                content=content,
                target_format=request.target_format,
                source_format=export_source_format,
                file_name=request.output_file_name,
                options=self._export_options(request),
            )
        except DocumentExportError as exc:
            log_error(
                "document_convert export content",
                exc,
                user_id=request.user_id,
                session_id=request.session_id,
                target_format=request.target_format,
            )
            raise ConvertDocumentExportError(str(exc)) from exc

    async def _export_markdown(
        self,
        *,
        request: NormalizedDocumentConvertRequest,
        markdown: str,
    ) -> GeneratedDocumentFile:
        try:
            return await self.export_service.export_markdown(
                user_id=request.user_id,
                session_id=request.session_id,
                markdown=markdown,
                target_format=request.target_format,
                file_name=request.output_file_name,
                options=self._export_options(request),
            )
        except DocumentExportError as exc:
            log_error(
                "document_convert export markdown",
                exc,
                user_id=request.user_id,
                session_id=request.session_id,
                target_format=request.target_format,
            )
            raise ConvertDocumentExportError(str(exc)) from exc

    async def _read_text_source(self, path: Path) -> str:
        try:
            return await asyncio.to_thread(_read_text_file, path)
        except DocumentDecodeError:
            raise
        except OSError as exc:
            log_error("document_convert read text source", exc, path=str(path))
            raise UnreadableDocumentRefError() from exc
        except Exception as exc:
            log_error("document_convert read text source unexpected", exc, path=str(path))
            raise DocumentInternalError() from exc

    async def _parse_source_to_markdown(
        self,
        *,
        source: ResolvedDocumentSource,
        target_format: str,
    ) -> str:
        try:
            parse_result = await self.parse_service.parse_path(source.path)
        except FrozenInstanceError as exc:
            log_error(
                "document_convert parse internal frozen state update error",
                exc,
                path=str(source.path),
                target_format=target_format,
            )
            raise FileConvertError(
                "解析服务内部状态更新异常: FrozenInstanceError. "
                "This is an internal parser state mutation bug, not a source document format problem."
            ) from exc
        except ParseServiceError as exc:
            log_error(
                "document_convert parse source document",
                exc,
                path=str(source.path),
                target_format=target_format,
            )
            raise DocumentParseError(str(exc)) from exc
        except Exception as exc:
            log_error(
                "document_convert parse source document unexpected",
                exc,
                path=str(source.path),
                target_format=target_format,
            )
            raise DocumentParseError("Failed to parse source document.") from exc

        return parse_result.text

    def _resolve_reference_docx_path(
        self, request: NormalizedDocumentConvertRequest
    ) -> Optional[Path]:
        if request.reference_docx_file_ref is None:
            return None

        try:
            source = self.temp_file_resolver.resolve(
                file_ref=request.reference_docx_file_ref,
                user_id=request.user_id,
                session_id=request.session_id,
            )
        except FileInvalidDocumentRefError as exc:
            log_error(
                "document_convert reference_docx_file_ref invalid",
                exc,
                user_id=request.user_id,
                session_id=request.session_id,
                file_ref=request.reference_docx_file_ref,
            )
            raise InvalidDocumentRefError() from exc
        except FileUnreadableDocumentRefError as exc:
            log_error(
                "document_convert reference_docx_file_ref unreadable",
                exc,
                user_id=request.user_id,
                session_id=request.session_id,
                file_ref=request.reference_docx_file_ref,
            )
            raise UnreadableDocumentRefError() from exc

        path = source.path
        if not path.exists():
            raise UnreadableDocumentRefError("reference_docx_file_ref does not exist")
        if not path.is_file():
            raise UnreadableDocumentRefError("reference_docx_file_ref is not a file")
        if path.suffix.lower() != ".docx":
            raise DocumentConvertError(
                "reference_docx_file_ref must point to a .docx file"
            )
        return path

    def _resolve_route(
        self, *, source_format: str, target_format: str
    ) -> _DocumentConvertRoute:
        if source_format in {"markdown", "plain_text", "html"}:
            if target_format not in _TEXT_EXPORT_TARGETS:
                raise UnsupportedDocumentRouteError(
                    f"unsupported route: {source_format}->{target_format}"
                )
            return _DocumentConvertRoute(
                route_kind="text_export",
                requires_parse=False,
                export_source_format=(
                    "plain_text" if source_format == "plain_text" else "markdown"
                ),
            )

        if source_format in _PARSE_EXPORT_SUFFIXES:
            if target_format not in _PARSE_EXPORT_TARGETS:
                raise UnsupportedDocumentRouteError(
                    f"unsupported route: {source_format}->{target_format}"
                )
            return _DocumentConvertRoute(
                route_kind="parse_export",
                requires_parse=True,
                export_source_format="markdown",
            )

        raise UnsupportedDocumentRouteError(
            f"unsupported route: {source_format}->{target_format}"
        )

    def _export_options(
        self, request: NormalizedDocumentConvertRequest
    ) -> ExportOptions:
        return ExportOptions(
            title=request.title,
            reference_docx=request.reference_docx_path,
        )


def normalize_convert_request(
    *,
    file_ref: str,
    file_name: Optional[str],
    target_format: str,
    user_id: str,
    session_id: str,
    title: Optional[str] = None,
    reference_docx_file_ref: Optional[str] = None,
) -> NormalizedDocumentConvertRequest:
    if not file_ref or not str(file_ref).strip():
        raise InvalidDocumentRefError("file_ref is required")
    if not isinstance(target_format, str) or not target_format:
        raise DocumentConvertError("target_format is required")
    if target_format not in SUPPORTED_EXPORT_FORMATS:
        raise UnsupportedDocumentFormatError(f"unsupported target_format: {target_format}")
    if not user_id or not str(user_id).strip():
        raise DocumentConvertError("user_id is required")
    if not session_id or not str(session_id).strip():
        raise DocumentConvertError("session_id is required")
    if title is not None:
        if not isinstance(title, str) or not title.strip():
            raise DocumentConvertError("title must be a non-empty string")
    if reference_docx_file_ref is not None:
        if not isinstance(reference_docx_file_ref, str) or not reference_docx_file_ref.strip():
            raise InvalidDocumentRefError("reference_docx_file_ref must be a non-empty string")
        if target_format != "docx":
            raise DocumentConvertError(
                "reference_docx_file_ref is only supported for docx export"
            )

    if file_name is not None:
        if not isinstance(file_name, str) or not file_name.strip():
            raise DocumentConvertError("file_name must be a non-empty string")
        output_suffix = Path(file_name).suffix.lower()
        expected_suffix = FILE_EXTENSIONS[target_format]
        if output_suffix and output_suffix != expected_suffix:
            raise DocumentConvertError(
                "output file_name suffix conflicts with target_format"
            )

    return NormalizedDocumentConvertRequest(
        source_file_ref=file_ref,
        output_file_name=file_name,
        target_format=target_format,
        user_id=user_id,
        session_id=session_id,
        title=title,
        reference_docx_file_ref=reference_docx_file_ref,
        reference_docx_path=None,
    )


def detect_source_format_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _MARKDOWN_SUFFIXES:
        return "markdown"
    if suffix in _TEXT_SUFFIXES:
        return "plain_text"
    if suffix in _HTML_SUFFIXES:
        return "html"
    if suffix in _PARSE_EXPORT_SUFFIXES:
        return suffix
    raise UnsupportedDocumentFormatError("unsupported source document format")


def _read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    if len(raw) > _TEXT_READ_BYTES_LIMIT:
        raise DocumentDecodeError("文档内容过大，无法作为文本源读取。")

    for encoding in _TEXT_ENCODINGS:
        try:
            return raw.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue

    raise DocumentDecodeError("文档内容解码失败。")
