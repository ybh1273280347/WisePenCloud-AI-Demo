from chat.api.schemas.user_preferences import (
    UpdateLocaleRequest,
    UpdateTimezoneRequest,
    UserPreferencesResponse,
)
from chat.application.user_preferences import (
    UserPreferencesService,
    UserPreferencesSnapshot,
)
from chat.container import Container
from common.core.domain import R
from common.security import require_login
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

router = APIRouter()


@router.get("/preferences", response_model=R[UserPreferencesResponse])
@inject
async def get_user_preferences(
    user_id: str = Depends(require_login),
    service: UserPreferencesService = Depends(
        Provide[Container.user_preferences_service]
    ),
):
    preferences = await service.get_preferences(user_id=user_id)
    return R.success(data=_to_response(preferences))


@router.post("/preferences/timezone", response_model=R[UserPreferencesResponse])
@inject
async def update_user_timezone(
    req: UpdateTimezoneRequest,
    user_id: str = Depends(require_login),
    service: UserPreferencesService = Depends(
        Provide[Container.user_preferences_service]
    ),
):
    preferences = await service.update_timezone(
        user_id=user_id,
        timezone=req.timezone,
    )
    return R.success(data=_to_response(preferences))


@router.post("/preferences/locale", response_model=R[UserPreferencesResponse])
@inject
async def update_user_locale(
    req: UpdateLocaleRequest,
    user_id: str = Depends(require_login),
    service: UserPreferencesService = Depends(
        Provide[Container.user_preferences_service]
    ),
):
    preferences = await service.update_locale(
        user_id=user_id,
        locale=req.locale,
    )
    return R.success(data=_to_response(preferences))


def _to_response(preferences: UserPreferencesSnapshot) -> UserPreferencesResponse:
    return UserPreferencesResponse(
        timezone=preferences.timezone,
        locale=preferences.locale,
    )
