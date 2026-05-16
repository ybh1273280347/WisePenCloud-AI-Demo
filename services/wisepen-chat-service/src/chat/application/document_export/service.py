import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import uuid4

from common.logger import log_event

from .atomic_writer import AtomicExportWriter
from .constants import CONTENT_TYPES, FILE_EXTENSIONS, SUPPORTED_EXPORT_FORMATS
from .errors import (
    EmptyExportContentError,
    ExportOutputError,
    UnsupportedExportFormatError,
)
from .models import ExportOptions, ExportRequest, GeneratedDocumentFile
from .normalizer import ContentNormalizer
from .path_safety import is_path_within_root, sanitize_path_segment
from .renderers.registry import RendererRegistry


@dataclass(frozen=True, slots=True)
class DocumentExportService:
    output_root: Path
    normalizer: ContentNormalizer
    registry: RendererRegistry
    atomic_writer: AtomicExportWriter

    async def export_content(
        self,
        *,
        session_id: str,
        content: str,
        target_format: str,
        file_name: Optional[str] = None,
        source_format: str = "markdown",
        options: Optional[ExportOptions] = None,
    ) -> GeneratedDocumentFile:
        markdown = self.normalizer.normalize(
            content=content,
            source_format=source_format,
        )

        return await self.export_markdown(
            session_id=session_id,
            markdown=markdown,
            target_format=target_format,
            file_name=file_name,
            options=options,
        )

    async def export_markdown(
        self,
        *,
        session_id: str,
        markdown: str,
        target_format: str,
        file_name: Optional[str] = None,
        options: Optional[ExportOptions] = None,
    ) -> GeneratedDocumentFile:
        if target_format not in SUPPORTED_EXPORT_FORMATS:
            raise UnsupportedExportFormatError(target_format)

        if not markdown or not markdown.strip():
            raise EmptyExportContentError("Export content is empty.")

        started = time.monotonic()
        success = False
        renderer = self.registry.get(target_format)
        output_path = self._resolve_output_path(
            session_id=session_id,
            target_format=target_format,
            file_name=file_name,
        )

        if not is_path_within_root(output_path, self.output_root):
            raise ExportOutputError("Resolved output path escapes output root.")

        export_options = options or ExportOptions()

        async def render_to_tmp(tmp_path: Path) -> None:
            request = ExportRequest(
                session_id=session_id,
                markdown=markdown,
                target_format=target_format,
                output_path=tmp_path,
                file_name=file_name,
                options=export_options,
            )
            await renderer.render(request)

        try:
            await self.atomic_writer.write_with_renderer(
                output_path=output_path,
                render=render_to_tmp,
            )

            size_bytes = await asyncio.to_thread(lambda: output_path.stat().st_size)
            result = GeneratedDocumentFile(
                file_path=output_path,
                file_name=output_path.name,
                content_type=CONTENT_TYPES[target_format],
                target_format=target_format,
                size_bytes=size_bytes,
            )
            success = True
            return result
        finally:
            log_event(
                "tool_perf",
                tool_name="document_export",
                stage=f"export_{target_format}",
                elapsed_ms=int((time.monotonic() - started) * 1000),
                success=success,
                cache_hit=False,
                fallback_used=False,
            )

    def _resolve_output_path(
        self,
        *,
        session_id: str,
        target_format: str,
        file_name: Optional[str],
    ) -> Path:
        suffix = FILE_EXTENSIONS[target_format]
        safe_session = sanitize_path_segment(session_id, fallback="default")

        if file_name:
            safe_stem = sanitize_path_segment(
                Path(file_name).stem or "document",
                fallback="document",
            )
        else:
            safe_stem = f"document-{uuid4().hex}"

        return self.output_root / safe_session / f"{safe_stem}{suffix}"
