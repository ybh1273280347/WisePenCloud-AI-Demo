from typing import Any

from chat.application.chat_turn_coordinator import ChatTurnCoordinator
from chat.application.user_preferences.repository import UserPreferencesRepository
from chat.application.user_preferences.service import UserPreferencesService
from chat.application.web_search.search_provider_config.encryption import (
    SearchProviderCredentialCipher,
)
from chat.application.web_search.search_provider_config.repository import (
    SearchProviderConfigRepository,
)
from chat.application.web_search.search_provider_config.service import (
    SearchProviderConfigService,
)
from chat.application.web_search.search_provider_config.validator import (
    SearchProviderConfigValidator,
)
from chat.core.config.app_settings import settings
from dependency_injector import providers


def register_application_providers(container_cls: Any) -> None:
    container_cls.user_preferences_repository = providers.Singleton(
        UserPreferencesRepository,
    )
    container_cls.user_preferences_service = providers.Singleton(
        UserPreferencesService,
        repository=container_cls.user_preferences_repository,
    )

    container_cls.search_provider_config_repository = providers.Singleton(
        SearchProviderConfigRepository,
    )
    container_cls.search_provider_credential_cipher = providers.Singleton(
        SearchProviderCredentialCipher,
        master_key=settings.SEARCH_PROVIDER_CREDENTIAL_MASTER_KEY,
        key_id=settings.SEARCH_PROVIDER_CREDENTIAL_KEY_ID,
        hmac_secret=settings.SEARCH_PROVIDER_CREDENTIAL_HMAC_SECRET,
    )
    container_cls.search_provider_config_validator = providers.Singleton(
        SearchProviderConfigValidator,
    )
    container_cls.search_provider_config_service = providers.Singleton(
        SearchProviderConfigService,
        repository=container_cls.search_provider_config_repository,
        cipher=container_cls.search_provider_credential_cipher,
        validator=container_cls.search_provider_config_validator,
    )

    # 应用层组件
    container_cls.chat_turn_coordinator = providers.Factory(
        ChatTurnCoordinator,
        llm=container_cls.llm_provider,
        memory=container_cls.memory_provider,
        model_resolver=container_cls.model_resolver,
        session_repo=container_cls.session_repo,
        message_repo=container_cls.message_repo,
        hot_context_repo=container_cls.hot_context_repo,
        tool_registry=container_cls.tool_registry,
        kafka_producer=container_cls.kafka_producer,
        skill_matcher=container_cls.skill_matcher,
        search_provider_config_service=container_cls.search_provider_config_service,
        user_preferences_service=container_cls.user_preferences_service,
    )
