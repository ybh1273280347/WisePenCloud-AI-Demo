from typing import Any

from chat.application.tools.services.document_parse.factory import build_document_parse_service
from chat.application.tools.services.document_file import DocumentTempFileResolver
from chat.application.tools.common.ocr import OcrImageAdapter, OcrProcessor
from chat.application.tools.common.ocr.config import (
    OCR_BACKEND,
    OCR_LANGUAGE,
    OCR_TIMEOUT_SECONDS,
    OCR_USE_DOC_ORIENTATION_CLASSIFY,
    OCR_USE_DOC_UNWARPING,
    OCR_USE_TEXTLINE_ORIENTATION,
    OCR_WORKER_IDLE_TTL_SECONDS,
    OCR_WORKER_MODE,
)
from chat.core.config.app_settings import settings
from dependency_injector import providers


def register_document_parse_providers(container_cls: Any) -> None:
    # 公共 OCR
    container_cls.ocr_processor = providers.Singleton(
        OcrProcessor,
        timeout=OCR_TIMEOUT_SECONDS,
        enabled=settings.ENABLE_OCR,
        backend=OCR_BACKEND,
        language=OCR_LANGUAGE,
        worker_mode=OCR_WORKER_MODE,
        worker_idle_ttl_seconds=OCR_WORKER_IDLE_TTL_SECONDS,
        use_doc_orientation_classify=OCR_USE_DOC_ORIENTATION_CLASSIFY,
        use_doc_unwarping=OCR_USE_DOC_UNWARPING,
        use_textline_orientation=OCR_USE_TEXTLINE_ORIENTATION,
    )
    container_cls.ocr_image_adapter = providers.Singleton(
        OcrImageAdapter,
        local_ocr_processor=container_cls.ocr_processor,
    )

    container_cls.document_parse_service = providers.Singleton(
        build_document_parse_service,
        local_ocr_processor=container_cls.ocr_processor,
    )
    container_cls.document_file_resolver = providers.Singleton(
        DocumentTempFileResolver,
    )
