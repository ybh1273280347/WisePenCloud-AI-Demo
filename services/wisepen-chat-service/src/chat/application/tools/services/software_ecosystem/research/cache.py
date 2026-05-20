from __future__ import annotations

from cachetools import TTLCache

from chat.application.tools.services.software_ecosystem import config

software_ecosystem_candidate_cache = TTLCache(
    maxsize=256,
    ttl=config.SOFTWARE_ECOSYSTEM_CANDIDATE_CACHE_TTL_SECONDS,
)

