from typing import Any

from chat.application.document_export import DocumentExportService
from chat.application.document_export.config import (
    DOCUMENT_EXPORT_MAX_PDF_CONTEXTS,
    DOCUMENT_EXPORT_PANDOC_BIN,
    DOCUMENT_EXPORT_PLAYWRIGHT_DISABLE_SANDBOX,
    document_export_output_path,
)
from chat.application.document_export.internal.atomic_writer import AtomicExportWriter
from chat.application.document_export.internal.infrastructure.playwright_pool import (
    PlaywrightBrowserPool,
)
from chat.application.document_export.internal.normalizer import ContentNormalizer
from chat.application.document_export.internal.renderers.docx_renderer import DocxRenderer
from chat.application.document_export.internal.renderers.html_renderer import HtmlRenderer
from chat.application.document_export.internal.renderers.markdown_renderer import MarkdownRenderer
from chat.application.document_export.internal.renderers.pdf_renderer import PdfRenderer
from chat.application.document_export.internal.renderers.registry import RendererRegistry
from chat.application.document_export.internal.renderers.txt_renderer import TxtRenderer
from dependency_injector import providers


def register_document_export_providers(container_cls: Any) -> None:
    container_cls.document_export_normalizer = providers.Singleton(
        ContentNormalizer,
    )
    container_cls.document_export_html_renderer = providers.Singleton(
        HtmlRenderer,
    )
    container_cls.document_export_browser_pool = providers.Singleton(
        PlaywrightBrowserPool,
        max_contexts=DOCUMENT_EXPORT_MAX_PDF_CONTEXTS,
        disable_sandbox=DOCUMENT_EXPORT_PLAYWRIGHT_DISABLE_SANDBOX,
    )
    container_cls.document_export_pdf_renderer = providers.Singleton(
        PdfRenderer,
        html_renderer=container_cls.document_export_html_renderer,
        browser_pool=container_cls.document_export_browser_pool,
    )
    container_cls.document_export_docx_renderer = providers.Singleton(
        DocxRenderer,
        pandoc_bin=DOCUMENT_EXPORT_PANDOC_BIN,
    )
    container_cls.document_export_markdown_renderer = providers.Singleton(
        MarkdownRenderer,
    )
    container_cls.document_export_txt_renderer = providers.Singleton(
        TxtRenderer,
    )
    container_cls.document_export_registry = providers.Singleton(
        RendererRegistry.from_renderers,
        renderers=providers.List(
            container_cls.document_export_markdown_renderer,
            container_cls.document_export_html_renderer,
            container_cls.document_export_pdf_renderer,
            container_cls.document_export_docx_renderer,
            container_cls.document_export_txt_renderer,
        ),
    )
    container_cls.document_export_atomic_writer = providers.Singleton(
        AtomicExportWriter,
    )
    container_cls.document_export_service = providers.Singleton(
        DocumentExportService,
        output_root=document_export_output_path(),
        normalizer=container_cls.document_export_normalizer,
        registry=container_cls.document_export_registry,
        atomic_writer=container_cls.document_export_atomic_writer,
    )
