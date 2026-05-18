from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

from chat.application.tools.services.code_search.common.http_client import (
    VerticalSearchHttpClient,
)
from chat.core.config.app_settings import settings

from . import config


class DepsDevClient:
    def __init__(
        self,
        http: Optional[VerticalSearchHttpClient] = None,
        *,
        base_url: Optional[str] = None,
    ) -> None:
        self._http = http or VerticalSearchHttpClient(
            timeout=config.PACKAGE_INTELLIGENCE_TIMEOUT_SECONDS
        )
        self._base_url = (base_url or settings.DEPS_DEV_API_BASE_URL).rstrip("/")

    async def get_package(self, *, system: str, name: str) -> Dict[str, Any]:
        return await self._http.get_json(
            f"{self._base_url}/systems/{quote(system, safe='')}/packages/{quote(name, safe='')}"
        )

    async def get_version(
        self, *, system: str, name: str, version: str
    ) -> Dict[str, Any]:
        return await self._http.get_json(
            f"{self._base_url}/systems/{quote(system, safe='')}/packages/{quote(name, safe='')}/versions/{quote(version, safe='')}"
        )

    async def get_requirements(
        self, *, system: str, name: str, version: str
    ) -> Dict[str, Any]:
        return await self._http.get_json(
            f"{self._base_url}/systems/{quote(system, safe='')}/packages/{quote(name, safe='')}/versions/{quote(version, safe='')}:requirements"
        )

    async def get_dependencies(
        self, *, system: str, name: str, version: str
    ) -> Dict[str, Any]:
        return await self._http.get_json(
            f"{self._base_url}/systems/{quote(system, safe='')}/packages/{quote(name, safe='')}/versions/{quote(version, safe='')}:dependencies"
        )

    async def close(self) -> None:
        await self._http.close()
