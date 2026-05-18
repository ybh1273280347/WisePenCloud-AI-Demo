from typing import Any

from chat.application.tools.services.attachment_read import AttachmentReadService
from chat.application.tools.services.attachment_read.resolver import StubAttachmentResolver
from chat.application.tools.common.content_detection import ContentDetector
from chat.application.tools.common.file_handoff import (
    DEFAULT_HANDOFF_ROOT,
    DEFAULT_HANDOFF_TTL_SECONDS,
    TemporaryFileHandoffStore,
)
from dependency_injector import providers


def register_attachment_read_providers(container_cls: Any) -> None:
    container_cls.content_detector = providers.Singleton(
        ContentDetector,
    )
    container_cls.file_handoff_store = providers.Singleton(
        TemporaryFileHandoffStore,
        root_dir=providers.Object(DEFAULT_HANDOFF_ROOT),
        ttl_seconds=DEFAULT_HANDOFF_TTL_SECONDS,
    )
    container_cls.attachment_resolver = providers.Singleton(
        StubAttachmentResolver,
    )
    container_cls.attachment_read_service = providers.Singleton(
        AttachmentReadService,
        resolver=container_cls.attachment_resolver,
        content_detector=container_cls.content_detector,
        file_handoff_store=container_cls.file_handoff_store,
        ocr_image_adapter=container_cls.ocr_image_adapter,
    )
