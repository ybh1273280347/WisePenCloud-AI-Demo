from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

from chat.application.tools.services.software_ecosystem import config
from chat.application.tools.services.software_ecosystem.common.http_client import (
    SoftwareEcosystemHttpClient,
)
from chat.core.config.app_settings import settings


class DepsDevClient:
    def __init__(
        self,
        http: Optional[SoftwareEcosystemHttpClient] = None,
        *,
        base_url: Optional[str] = None,
    ) -> None:
        self._http = http or SoftwareEcosystemHttpClient(
            timeout=config.SOFTWARE_ECOSYSTEM_TIMEOUT_SECONDS
        )
        self._base_url = (base_url or settings.DEPS_DEV_API_BASE_URL).rstrip("/")

    async def get_package(self, *, system: str, name: str) -> Dict[str, Any]:
        return await self._http.get_json(
            f"{self._base_url}/systems/{quote(system, safe='')}/packages/{quote(name, safe='')}"
        )

    async def get_version(self, *, system: str, name: str, version: str) -> Dict[str, Any]:
        return await self._http.get_json(
            f"{self._base_url}/systems/{quote(system, safe='')}/packages/{quote(name, safe='')}/versions/{quote(version, safe='')}"
        )

    async def get_requirements(self, *, system: str, name: str, version: str) -> Dict[str, Any]:
        return await self._http.get_json(
            f"{self._base_url}/systems/{quote(system, safe='')}/packages/{quote(name, safe='')}/versions/{quote(version, safe='')}:requirements"
        )

    async def get_dependencies(self, *, system: str, name: str, version: str) -> Dict[str, Any]:
        return await self._http.get_json(
            f"{self._base_url}/systems/{quote(system, safe='')}/packages/{quote(name, safe='')}/versions/{quote(version, safe='')}:dependencies"
        )

    async def close(self) -> None:
        await self._http.close()

