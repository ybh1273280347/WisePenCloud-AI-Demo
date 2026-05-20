from __future__ import annotations

from cachetools import TTLCache

from chat.application.tools.services.software_ecosystem import config

open_source_project_query_cache = TTLCache(
    maxsize=256,
    ttl=config.OPEN_SOURCE_PROJECT_QUERY_CACHE_TTL_SECONDS,
)

