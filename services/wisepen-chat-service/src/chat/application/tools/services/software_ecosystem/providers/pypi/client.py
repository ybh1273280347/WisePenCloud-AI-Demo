from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

from chat.application.tools.services.software_ecosystem import config
from chat.application.tools.services.software_ecosystem.common.http_client import (
    SoftwareEcosystemHttpClient,
)
from chat.core.config.app_settings import settings


class PyPIRegistryClient:
    def __init__(
        self,
        http: Optional[SoftwareEcosystemHttpClient] = None,
        *,
        base_url: Optional[str] = None,
    ) -> None:
        self._http = http or SoftwareEcosystemHttpClient(
            timeout=config.SOFTWARE_ECOSYSTEM_TIMEOUT_SECONDS
        )
        self._base_url = (base_url or settings.PYPI_API_BASE_URL).rstrip("/")

    async def get_project(self, package_name: str) -> Dict[str, Any]:
        return await self._http.get_json(
            f"{self._base_url}/pypi/{quote(package_name, safe='')}/json"
        )

    async def close(self) -> None:
        await self._http.close()

