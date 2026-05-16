from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from chat.domain.entities.search_provider_config import UserSearchProviderConfig


class SearchProviderConfigRepository:
    async def get_by_user_id(self, user_id: str) -> Optional[UserSearchProviderConfig]:
        return await UserSearchProviderConfig.find_one(
            UserSearchProviderConfig.user_id == user_id
        )

    async def upsert(
        self,
        *,
        user_id: str,
        values: dict[str, Any],
    ) -> UserSearchProviderConfig:
        now = datetime.utcnow()
        config = await self.get_by_user_id(user_id)

        if config is None:
            config = UserSearchProviderConfig(
                user_id=user_id,
                created_at=now,
                updated_at=now,
                **values,
            )
            await config.insert()
            return config

        for key, value in values.items():
            setattr(config, key, value)
        config.updated_at = now
        await config.save()
        return config
