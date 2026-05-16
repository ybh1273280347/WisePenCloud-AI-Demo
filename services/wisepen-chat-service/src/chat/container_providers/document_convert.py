from typing import Any

from chat.application.document_convert import DocumentConvertService
from dependency_injector import providers


def register_document_convert_providers(container_cls: Any) -> None:
    container_cls.document_convert_service = providers.Singleton(
        DocumentConvertService,
        parse_service=container_cls.document_parse_service,
        export_service=container_cls.document_export_service,
        file_resolver=container_cls.document_file_resolver,
    )
