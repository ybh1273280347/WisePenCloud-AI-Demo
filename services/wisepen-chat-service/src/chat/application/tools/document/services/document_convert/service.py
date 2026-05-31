from __future__ import annotations

from pathlib import Path
from typing import Optional

from chat.application.infra.document_temp_files.processing_scope import document_processing_scope
from chat.application.infra.document_temp_files.resolver import DocumentTempFileResolver
from chat.application.tools.document.services.document_export.enums import ExportFormat, ExportSourceFormat
from chat.application.tools.document.services.document_export.errors import DocumentExportError
from chat.application.tools.document.services.document_export.models import ExportOptions, GeneratedDocumentFile
from chat.application.tools.document.services.document_export.service import DocumentExportService
from common.logger import log_event
from .converter import MarkdownConverter
from .errors import (
    DocumentConvertError,
    DocumentExportFailedError,
    EmptyParsedMarkdownError,
    UnreadableDocumentRefError,
)


class DocumentConvertService:
    """
    文档转换服务。

    - 解析 file_ref 到真实临时文件。
    - 使用 MarkdownConverter 将源文件统一转换为 Markdown。
    - 使用 DocumentExportService 将 Markdown 导出为目标格式。
    """

    def __init__(
        self,
        *,
        markdown_converter: MarkdownConverter,
        export_service: DocumentExportService,
        temp_file_resolver: DocumentTempFileResolver,
    ) -> None:
        """初始化对象依赖。"""
        self.markdown_converter = markdown_converter
        self.export_service = export_service
        self.temp_file_resolver = temp_file_resolver

    async def convert_document(
        self,
        *,
        session_id: str,
        user_id: str,
        file_ref: str,
        target_format: ExportFormat,
        file_name: Optional[str] = None,
        title: Optional[str] = None,
        reference_docx_file_ref: Optional[str] = None,
    ) -> GeneratedDocumentFile:
        """转换当前流程。"""
        source = self.temp_file_resolver.resolve(
            file_ref=file_ref,
            user_id=user_id,
            session_id=session_id,
        )

        reference_docx_path = self._resolve_reference_docx_path(
            reference_docx_file_ref=reference_docx_file_ref,
            user_id=user_id,
            session_id=session_id,
        )

        # 防止正在转换的文件被定时清理脚本误删。
        with document_processing_scope(
            self.temp_file_resolver.session_root(
                user_id=user_id,
                session_id=session_id,
            )
        ):
            markdown = await self.markdown_converter.convert(path=source.path)
            if not markdown.strip():
                raise EmptyParsedMarkdownError()

            try:
                generated = await self.export_service.export_document(
                    user_id=user_id,
                    session_id=session_id,
                    content=markdown,
                    source_format=ExportSourceFormat.MARKDOWN,
                    target_format=target_format,
                    file_name=file_name,
                    options=ExportOptions(
                        title=title,
                        reference_docx=reference_docx_path,
                    ),
                )
            except DocumentExportError as e:
                raise DocumentExportFailedError(str(e)) from e

            # 只保留最终成功审计。
            log_event(
                "document_convert completed",
                user_id=user_id,
                session_id=session_id,
                source_file_ref=file_ref,
                source_path=str(source.path),
                target_format=target_format.value,
                output_file_name=file_name,
                storage_file_name=generated.storage_file_name,
                size_bytes=generated.size_bytes,
                reference_docx_used=reference_docx_path is not None,
            )

            return generated

    def _resolve_reference_docx_path(
        self,
        *,
        reference_docx_file_ref: Optional[str],
        user_id: str,
        session_id: str,
    ) -> Optional[Path]:
        """解析当前流程。"""
        if reference_docx_file_ref is None:
            return None

        source = self.temp_file_resolver.resolve(
            file_ref=reference_docx_file_ref,
            user_id=user_id,
            session_id=session_id,
        )

        path = source.path
        if not path.exists() or not path.is_file():
            raise UnreadableDocumentRefError()

        if path.suffix.lower() != ".docx":
            raise DocumentConvertError(
                "reference_docx_file_ref must point to a .docx file"
            )

        return path
