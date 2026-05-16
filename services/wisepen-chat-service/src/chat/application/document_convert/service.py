from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from chat.application.document_export import (
    DocumentExportService,
    GeneratedDocumentFile,
)

from .errors import EmptyParsedMarkdownError, FileConvertError, SourceFileNotFoundError

if TYPE_CHECKING:
    from chat.application.document_parse import DocumentParseService
    from chat.application.document_parse.file_resolver import LocalDocumentFileResolver


@dataclass(frozen=True, slots=True)
class DocumentConvertService:
    parse_service: DocumentParseService
    export_service: DocumentExportService
    file_resolver: LocalDocumentFileResolver

    async def convert_file(
        self,
        *,
        session_id: str,
        file_ref: str,
        target_format: str,
        file_name: Optional[str] = None,
    ) -> GeneratedDocumentFile:
        try:
            resolved = self.file_resolver.resolve(file_ref)
        except FileNotFoundError as e:
            raise SourceFileNotFoundError(file_ref) from e

        try:
            parse_result = await self.parse_service.parse_path(resolved.local_path)
        except Exception as e:
            raise FileConvertError("Failed to parse source document.") from e

        markdown = parse_result.text
        if not markdown or not markdown.strip():
            raise EmptyParsedMarkdownError(
                "Document parser returned empty Markdown content."
            )

        return await self.export_service.export_markdown(
            session_id=session_id,
            markdown=markdown,
            target_format=target_format,
            file_name=file_name,
        )
