from __future__ import annotations

from typing import Any, Dict, Optional

from chat.application.tools.services.software_ecosystem import config
from chat.application.tools.services.software_ecosystem.common.http_client import (
    SoftwareEcosystemHttpClient,
)


class HackerNewsClient:
    def __init__(
        self,
        http: Optional[SoftwareEcosystemHttpClient] = None,
        *,
        base_url: str = "https://hn.algolia.com/api/v1",
    ) -> None:
        self._http = http or SoftwareEcosystemHttpClient(
            timeout=config.SOFTWARE_ECOSYSTEM_TIMEOUT_SECONDS
        )
        self._base_url = base_url.rstrip("/")

    async def search(self, *, query: str, limit: int) -> Dict[str, Any]:
        return await self._http.get_json(
            f"{self._base_url}/search",
            params={"query": query, "tags": "story", "hitsPerPage": limit},
        )

    async def close(self) -> None:
        await self._http.close()

