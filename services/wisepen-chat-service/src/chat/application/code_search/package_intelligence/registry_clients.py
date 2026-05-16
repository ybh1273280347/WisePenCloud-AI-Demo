from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

from chat.application.code_search.common.http_client import (
    VerticalSearchHttpClient,
)
from chat.core.config.app_settings import settings

from . import config


class PyPIRegistryClient:
    def __init__(
        self,
        http: Optional[VerticalSearchHttpClient] = None,
        *,
        base_url: Optional[str] = None,
    ) -> None:
        self._http = http or VerticalSearchHttpClient(
            timeout=config.PACKAGE_INTELLIGENCE_TIMEOUT_SECONDS
        )
        self._base_url = (base_url or settings.PYPI_API_BASE_URL).rstrip("/")

    async def get_project(self, package_name: str) -> Dict[str, Any]:
        return await self._http.get_json(
            f"{self._base_url}/pypi/{quote(package_name, safe='')}/json"
        )

    async def close(self) -> None:
        await self._http.close()


class NpmRegistryClient:
    def __init__(
        self,
        http: Optional[VerticalSearchHttpClient] = None,
        *,
        base_url: Optional[str] = None,
    ) -> None:
        self._http = http or VerticalSearchHttpClient(
            timeout=config.PACKAGE_INTELLIGENCE_TIMEOUT_SECONDS
        )
        self._base_url = (base_url or settings.NPM_REGISTRY_BASE_URL).rstrip("/")

    async def get_package(self, package_name: str) -> Dict[str, Any]:
        return await self._http.get_json(
            f"{self._base_url}/{quote(package_name, safe='')}",
            headers={"Accept": "application/json"},
        )

    async def close(self) -> None:
        await self._http.close()
