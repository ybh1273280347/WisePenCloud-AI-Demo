from typing import Any

from chat.application.model_resolver import ModelResolver
from chat.core.config.app_settings import settings
from chat.core.providers import LiteLLMAdapter, Mem0Adapter
from common.kafka.producer import KafkaProducerClient
from dependency_injector import providers


def register_core_providers(container_cls: Any) -> None:
    container_cls.llm_provider = providers.Singleton(LiteLLMAdapter)
    container_cls.memory_provider = providers.Singleton(Mem0Adapter)
    container_cls.model_resolver = providers.Singleton(ModelResolver)
    container_cls.kafka_producer = providers.Singleton(
        KafkaProducerClient,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
    )
