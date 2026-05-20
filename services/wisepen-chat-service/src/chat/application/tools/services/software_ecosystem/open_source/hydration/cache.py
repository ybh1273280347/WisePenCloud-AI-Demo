from __future__ import annotations

from cachetools import TTLCache

from chat.application.tools.services.software_ecosystem import config

open_source_project_profile_cache = TTLCache(
    maxsize=512,
    ttl=config.OPEN_SOURCE_PROJECT_PROFILE_CACHE_TTL_SECONDS,
)

