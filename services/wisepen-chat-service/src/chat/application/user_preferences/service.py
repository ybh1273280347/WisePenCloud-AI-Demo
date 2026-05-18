from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from chat.application.user_preferences.constants import (
    ALLOWED_LOCALES,
    DEFAULT_LOCALE,
    DEFAULT_TIMEZONE,
)
from chat.application.user_preferences.repository import UserPreferencesRepository
from chat.domain.entities.user_preferences import UserPreferences


@dataclass(frozen=True, slots=True)
class UserPreferencesSnapshot:
    user_id: str
    timezone: str
    locale: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class UserPreferencesService:
    def __init__(self, *, repository: UserPreferencesRepository) -> None:
        self._repository = repository

    async def get_preferences(self, *, user_id: str) -> UserPreferencesSnapshot:
        preferences = await self._repository.get_by_user_id(user_id)
        if preferences is None:
            return UserPreferencesSnapshot(
                user_id=user_id,
                timezone=DEFAULT_TIMEZONE,
                locale=DEFAULT_LOCALE,
            )
        return _to_snapshot(preferences)

    async def update_timezone(
        self,
        *,
        user_id: str,
        timezone: str,
    ) -> UserPreferencesSnapshot:
        validated_timezone = validate_timezone(timezone)
        preferences = await self._repository.upsert(
            user_id=user_id,
            values={"timezone": validated_timezone},
        )
        return _to_snapshot(preferences)

    async def update_locale(
        self,
        *,
        user_id: str,
        locale: str,
    ) -> UserPreferencesSnapshot:
        validated_locale = validate_locale(locale)
        preferences = await self._repository.upsert(
            user_id=user_id,
            values={"locale": validated_locale},
        )
        return _to_snapshot(preferences)


def validate_timezone(value: Optional[str]) -> str:
    if value is None:
        return DEFAULT_TIMEZONE

    if not isinstance(value, str) or not value:
        raise ValueError("timezone must be a non-empty IANA timezone")

    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as e:
        raise ValueError("timezone must be a valid IANA timezone") from e

    return value


def validate_locale(value: Optional[str]) -> str:
    if value is None:
        return DEFAULT_LOCALE

    if not isinstance(value, str) or value not in ALLOWED_LOCALES:
        raise ValueError("locale is not supported")

    return value


def _to_snapshot(preferences: UserPreferences) -> UserPreferencesSnapshot:
    return UserPreferencesSnapshot(
        user_id=preferences.user_id,
        timezone=validate_timezone(preferences.timezone),
        locale=validate_locale(preferences.locale),
        created_at=preferences.created_at,
        updated_at=preferences.updated_at,
    )
