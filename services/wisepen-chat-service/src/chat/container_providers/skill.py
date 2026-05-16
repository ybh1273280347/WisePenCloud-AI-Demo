from typing import Any

from chat.application.skill_cache_refresher import SkillCacheRefresher
from chat.application.skill_matcher import KeywordSkillMatcher
from chat.core.config.app_settings import settings
from chat.core.providers import LocalFSSkillAssetLoader, OssSkillAssetLoader
from dependency_injector import providers


def register_skill_providers(container_cls: Any) -> None:
    # Skill 子系统：
    # - SkillRepository 只读 Mongo 里的 Skill 实体
    # - SkillAssetLoader：DEV=True 用 LocalFS+OSS 回退；DEV=False 直连裸 OSS
    container_cls.oss_skill_asset_loader = providers.Singleton(
        OssSkillAssetLoader,
        file_storage_client=container_cls.file_storage_client,
    )

    if settings.DEV:
        container_cls.skill_asset_loader = providers.Singleton(
            LocalFSSkillAssetLoader,
            oss_fallback=container_cls.oss_skill_asset_loader,
        )
    else:
        container_cls.skill_asset_loader = container_cls.oss_skill_asset_loader

    container_cls.skill_matcher = providers.Singleton(
        KeywordSkillMatcher,
        skill_repo=container_cls.skill_repo,
    )
    container_cls.skill_cache_refresher = providers.Singleton(
        SkillCacheRefresher,
        matcher=container_cls.skill_matcher,
    )
