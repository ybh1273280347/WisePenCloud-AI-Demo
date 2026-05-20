from __future__ import annotations

from typing import Any, List, Optional

from chat.application.tools.services.software_ecosystem import config
from chat.application.tools.services.software_ecosystem.common.http_client import (
    SoftwareEcosystemHttpClient,
)

_ECOSYSTEM_TO_REGISTRY = {
    "npm": "npmjs.org",
    "pypi": "pypi.org",
}


class EcosystemsPackagesClient:
    def __init__(
        self,
        http: Optional[SoftwareEcosystemHttpClient] = None,
        *,
        base_url: str = "https://packages.ecosyste.ms/api/v1",
    ) -> None:
        self._http = http or SoftwareEcosystemHttpClient(
            timeout=config.SOFTWARE_ECOSYSTEM_TIMEOUT_SECONDS
        )
        self._base_url = base_url.rstrip("/")

    async def search_packages(self, *, query: str, ecosystem: str, limit: int) -> List[Any]:
        registry = _ECOSYSTEM_TO_REGISTRY[ecosystem]
        payload = await self._http.get_json(
            f"{self._base_url}/registries/{registry}/packages/search",
            params={"q": query, "per_page": limit},
        )
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("packages"), list):
            return payload["packages"]
        return []

    async def close(self) -> None:
        await self._http.close()

