from typing import Optional

from chat.api.schemas.search_provider import (
    SearchProviderConfigResponse,
    SetCustomSearchProviderRequest,
    SetSearchProviderModeRequest,
)
from chat.application.web_search.search_provider_config import (
    SearchProviderConfigService,
)
from chat.container import Container
from chat.domain.entities.search_provider_config import UserSearchProviderConfig
from common.core.domain import R
from common.security import require_login
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

router = APIRouter()


@router.get("/get", response_model=R[SearchProviderConfigResponse])
@inject
async def get_search_provider_config(
    user_id: str = Depends(require_login),
    service: SearchProviderConfigService = Depends(
        Provide[Container.search_provider_config_service]
    ),
):
    config = await service.get_config(user_id=user_id)
    return R.success(data=_to_response(config))


@router.post("/setMode", response_model=R[SearchProviderConfigResponse])
@inject
async def set_search_provider_mode(
    req: SetSearchProviderModeRequest,
    user_id: str = Depends(require_login),
    service: SearchProviderConfigService = Depends(
        Provide[Container.search_provider_config_service]
    ),
):
    config = await service.set_mode(user_id=user_id, mode=req.mode)
    return R.success(data=_to_response(config))


@router.post("/setCustomProvider", response_model=R[SearchProviderConfigResponse])
@inject
async def set_custom_search_provider(
    req: SetCustomSearchProviderRequest,
    user_id: str = Depends(require_login),
    service: SearchProviderConfigService = Depends(
        Provide[Container.search_provider_config_service]
    ),
):
    config = await service.set_custom_provider(
        user_id=user_id,
        provider=req.provider,
        api_key=req.api_key,
    )
    return R.success(data=_to_response(config))


@router.post("/clearCustomProvider", response_model=R[SearchProviderConfigResponse])
@inject
async def clear_custom_search_provider(
    user_id: str = Depends(require_login),
    service: SearchProviderConfigService = Depends(
        Provide[Container.search_provider_config_service]
    ),
):
    config = await service.clear_custom_provider(user_id=user_id)
    return R.success(data=_to_response(config))


@router.post("/verify", response_model=R[SearchProviderConfigResponse])
@inject
async def verify_custom_search_provider(
    user_id: str = Depends(require_login),
    service: SearchProviderConfigService = Depends(
        Provide[Container.search_provider_config_service]
    ),
):
    config = await service.verify(user_id=user_id)
    return R.success(data=_to_response(config))


def _to_response(
    config: Optional[UserSearchProviderConfig],
) -> SearchProviderConfigResponse:
    if config is None:
        return SearchProviderConfigResponse(
            mode="default",
            provider=None,
            key_prefix4=None,
            key_last4=None,
            status="unset",
            last_verified_at=None,
            last_error_code=None,
        )

    return SearchProviderConfigResponse(
        mode=config.mode,
        provider=config.provider,
        key_prefix4=config.key_prefix4,
        key_last4=config.key_last4,
        status=config.status,
        last_verified_at=(
            config.last_verified_at.isoformat() if config.last_verified_at else None
        ),
        last_error_code=config.last_error_code,
    )
