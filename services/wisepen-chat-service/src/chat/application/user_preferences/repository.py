from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from chat.domain.entities.user_preferences import UserPreferences


class UserPreferencesRepository:
    async def get_by_user_id(self, user_id: str) -> Optional[UserPreferences]:
        return await UserPreferences.find_one(UserPreferences.user_id == user_id)

    async def upsert(
        self,
        *,
        user_id: str,
        values: Dict[str, Any],
    ) -> UserPreferences:
        now = datetime.utcnow()
        preferences = await self.get_by_user_id(user_id)

        if preferences is None:
            preferences = UserPreferences(
                user_id=user_id,
                created_at=now,
                updated_at=now,
                **values,
            )
            await preferences.insert()
            return preferences

        for key, value in values.items():
            setattr(preferences, key, value)
        preferences.updated_at = now
        await preferences.save()
        return preferences
