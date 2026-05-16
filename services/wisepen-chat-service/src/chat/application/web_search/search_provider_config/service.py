from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from chat.application.web_search.search_provider_config.constants import (
    ERROR_MASTER_KEY_REQUIRED,
    ERROR_NOT_CONFIGURED,
    ERROR_PROVIDER_ERROR,
    MODE_CUSTOM,
    MODE_DEFAULT,
    MODES,
    PROVIDERS,
    PUBLIC_ERROR_NOT_CONFIGURED,
    PUBLIC_ERROR_PROVIDER_ERROR,
    STATUS_PROVIDER_ERROR,
    STATUS_UNSET,
    STATUS_UNTESTED,
)
from chat.application.web_search.search_provider_config.encryption import (
    CredentialDecryptionError,
    CredentialEncryptionError,
    MASTER_KEY_REQUIRED_MESSAGE,
    SearchProviderCredentialCipher,
)
from chat.application.web_search.search_provider_config.repository import (
    SearchProviderConfigRepository,
)
from chat.application.web_search.search_provider_config.validator import (
    SearchProviderConfigValidator,
)
from chat.domain.entities.search_provider_config import UserSearchProviderConfig
from chat.domain.error_codes import ChatErrorCode
from common.core.exceptions import ServiceException


@dataclass(frozen=True, slots=True)
class RuntimeSearchProviderContext:
    mode: str
    custom_providers: Optional[List[Dict[str, Any]]] = None
    error_public_code: Optional[str] = None
    error_status: Optional[str] = None
    error_last_error_code: Optional[str] = None
    error_message: Optional[str] = None


class SearchProviderConfigService:
    def __init__(
        self,
        *,
        repository: SearchProviderConfigRepository,
        cipher: SearchProviderCredentialCipher,
        validator: SearchProviderConfigValidator,
    ) -> None:
        self._repository = repository
        self._cipher = cipher
        self._validator = validator

    async def get_config(
        self,
        *,
        user_id: str,
    ) -> Optional[UserSearchProviderConfig]:
        return await self._repository.get_by_user_id(user_id)

    async def set_mode(
        self,
        *,
        user_id: str,
        mode: str,
    ) -> UserSearchProviderConfig:
        _validate_mode(mode)

        if mode == MODE_DEFAULT:
            return await self._repository.upsert(
                user_id=user_id,
                values={
                    "mode": MODE_DEFAULT,
                },
            )

        config = await self._repository.get_by_user_id(user_id)
        if not _has_complete_custom_provider(config):
            raise ServiceException(ChatErrorCode.CUSTOM_PROVIDER_NOT_CONFIGURED)

        return await self._repository.upsert(
            user_id=user_id,
            values={
                "mode": MODE_CUSTOM,
            },
        )

    async def set_custom_provider(
        self,
        *,
        user_id: str,
        provider: str,
        api_key: str,
    ) -> UserSearchProviderConfig:
        _validate_provider(provider)
        normalized_key = api_key.strip()
        if not normalized_key:
            raise ServiceException(
                ChatErrorCode.CUSTOM_PROVIDER_NOT_CONFIGURED,
                custom_msg="自定义搜索源 API Key 不能为空",
            )

        try:
            encrypted = self._cipher.encrypt(
                user_id=user_id,
                provider=provider,
                api_key=normalized_key,
            )
        except CredentialEncryptionError as e:
            raise ServiceException(
                ChatErrorCode.CUSTOM_PROVIDER_ERROR,
                custom_msg=str(e),
            ) from e

        existing = await self._repository.get_by_user_id(user_id)
        if (
            existing is not None
            and existing.provider == provider
            and existing.key_fingerprint == encrypted.key_fingerprint
        ):
            raise ServiceException(ChatErrorCode.CUSTOM_PROVIDER_KEY_ALREADY_EXISTS)

        return await self._repository.upsert(
            user_id=user_id,
            values={
                "mode": MODE_CUSTOM,
                "provider": provider,
                "encrypted_api_key": encrypted.encrypted_value,
                "encryption_key_id": encrypted.encryption_key_id,
                "key_fingerprint": encrypted.key_fingerprint,
                "key_prefix4": encrypted.key_prefix4,
                "key_last4": encrypted.key_last4,
                "status": STATUS_UNTESTED,
                "last_verified_at": None,
                "last_error_code": None,
            },
        )

    async def clear_custom_provider(
        self,
        *,
        user_id: str,
    ) -> UserSearchProviderConfig:
        return await self._repository.upsert(
            user_id=user_id,
            values={
                "mode": MODE_DEFAULT,
                "provider": None,
                "encrypted_api_key": None,
                "encryption_key_id": None,
                "key_fingerprint": None,
                "key_prefix4": None,
                "key_last4": None,
                "status": STATUS_UNSET,
                "last_verified_at": None,
                "last_error_code": None,
            },
        )

    async def verify(
        self,
        *,
        user_id: str,
    ) -> UserSearchProviderConfig:
        config = await self._repository.get_by_user_id(user_id)
        if not _has_complete_custom_provider(config):
            raise ServiceException(ChatErrorCode.CUSTOM_PROVIDER_NOT_CONFIGURED)

        assert config is not None
        try:
            api_key = self._decrypt_config_api_key(config)
        except CredentialDecryptionError as e:
            raise ServiceException(
                ChatErrorCode.CUSTOM_PROVIDER_ERROR,
                custom_msg=str(e),
            ) from e

        result = await self._validator.verify(
            provider=config.provider or "",
            api_key=api_key,
        )

        return await self._repository.upsert(
            user_id=user_id,
            values={
                "status": result.status,
                "last_verified_at": datetime.utcnow(),
                "last_error_code": result.last_error_code,
            },
        )

    async def runtime_context(
        self,
        *,
        user_id: str,
        require_custom: bool = False,
    ) -> RuntimeSearchProviderContext:
        config = await self._repository.get_by_user_id(user_id)
        if config is None:
            if require_custom:
                return _missing_custom_provider_context()
            return RuntimeSearchProviderContext(mode=MODE_DEFAULT)

        if config.mode == MODE_DEFAULT and not require_custom:
            return RuntimeSearchProviderContext(mode=MODE_DEFAULT)

        if not require_custom and config.mode != MODE_CUSTOM:
            return _missing_custom_provider_context()

        if not _has_complete_custom_provider(config):
            return _missing_custom_provider_context()

        try:
            api_key = self._decrypt_config_api_key(config)
        except CredentialDecryptionError as e:
            last_error_code = (
                ERROR_MASTER_KEY_REQUIRED
                if str(e) == MASTER_KEY_REQUIRED_MESSAGE
                else ERROR_PROVIDER_ERROR
            )
            await self.record_runtime_failure(
                user_id=user_id,
                status=STATUS_PROVIDER_ERROR,
                last_error_code=last_error_code,
            )
            return RuntimeSearchProviderContext(
                mode=MODE_CUSTOM,
                custom_providers=[],
                error_public_code=PUBLIC_ERROR_PROVIDER_ERROR,
                error_status=STATUS_PROVIDER_ERROR,
                error_last_error_code=last_error_code,
                error_message=str(e),
            )

        return RuntimeSearchProviderContext(
            mode=MODE_CUSTOM,
            custom_providers=[
                {
                    "provider": config.provider,
                    "api_key": api_key,
                    "enabled": True,
                }
            ],
        )

    async def record_runtime_failure(
        self,
        *,
        user_id: str,
        status: str,
        last_error_code: str,
    ) -> None:
        config = await self._repository.get_by_user_id(user_id)
        if config is None:
            return

        config.status = status
        config.last_error_code = last_error_code
        config.updated_at = datetime.utcnow()
        await config.save()

    def _decrypt_config_api_key(self, config: UserSearchProviderConfig) -> str:
        if not _has_complete_custom_provider(config):
            raise CredentialDecryptionError("custom provider is not configured")

        assert config.provider is not None
        assert config.encrypted_api_key is not None
        assert config.encryption_key_id is not None
        return self._cipher.decrypt(
            user_id=config.user_id,
            provider=config.provider,
            encrypted_api_key=config.encrypted_api_key,
            encryption_key_id=config.encryption_key_id,
        )


def _validate_mode(mode: str) -> None:
    if mode not in MODES:
        raise ServiceException(ChatErrorCode.CUSTOM_PROVIDER_INVALID_MODE)


def _validate_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise ServiceException(ChatErrorCode.CUSTOM_PROVIDER_INVALID_PROVIDER)


def _has_complete_custom_provider(
    config: Optional[UserSearchProviderConfig],
) -> bool:
    if config is None:
        return False

    return all(
        (
            config.provider,
            config.encrypted_api_key,
            config.encryption_key_id,
            config.key_fingerprint,
            config.key_prefix4,
            config.key_last4,
        )
    )


def _missing_custom_provider_context() -> RuntimeSearchProviderContext:
    return RuntimeSearchProviderContext(
        mode=MODE_CUSTOM,
        custom_providers=[],
        error_public_code=PUBLIC_ERROR_NOT_CONFIGURED,
        error_status=STATUS_PROVIDER_ERROR,
        error_last_error_code=ERROR_NOT_CONFIGURED,
        error_message=(
            "Missing custom provider credential. Provide a temporary custom provider "
            "API key or choose a saved key."
        ),
    )
