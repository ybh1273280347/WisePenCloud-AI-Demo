from typing import Any

from chat.core.persistence import (
    MongoMessageRepository,
    MongoSessionRepository,
    MongoSkillRepository,
    RedisHotContext,
)
from dependency_injector import providers


def register_persistence_providers(container_cls: Any) -> None:
    # 数据持久层：MongoDB + Redis
    container_cls.session_repo = providers.Singleton(MongoSessionRepository)
    container_cls.message_repo = providers.Singleton(MongoMessageRepository)
    container_cls.hot_context_repo = providers.Singleton(RedisHotContext)
    container_cls.skill_repo = providers.Singleton(MongoSkillRepository)
