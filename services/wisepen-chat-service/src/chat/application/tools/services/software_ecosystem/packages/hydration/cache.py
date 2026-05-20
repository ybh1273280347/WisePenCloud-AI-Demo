from __future__ import annotations

from cachetools import TTLCache

from chat.application.tools.services.software_ecosystem import config

package_profile_cache = TTLCache(maxsize=512, ttl=config.PACKAGE_PROFILE_CACHE_TTL_SECONDS)
latest_pointer_cache = TTLCache(maxsize=512, ttl=config.PACKAGE_LATEST_POINTER_CACHE_TTL_SECONDS)

