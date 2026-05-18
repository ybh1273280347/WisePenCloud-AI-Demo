from __future__ import annotations

from typing import Any, Dict, Optional

from chat.application.tools.services.code_search.common.http_client import (
    VerticalSearchHttpClient,
)
from chat.core.config.app_settings import settings

from . import config


class ScorecardClient:
    def __init__(
        self,
        http: Optional[VerticalSearchHttpClient] = None,
        *,
        base_url: Optional[str] = None,
    ) -> None:
        self._http = http or VerticalSearchHttpClient(
            timeout=config.PACKAGE_INTELLIGENCE_TIMEOUT_SECONDS
        )
        self._base_url = (
            base_url or settings.OPENSFF_SCORECARD_API_BASE_URL
        ).rstrip("/")

    async def get_project(self, *, owner: str, repo: str) -> Dict[str, Any]:
        return await self._http.get_json(
            f"{self._base_url}/projects/github.com/{owner}/{repo}"
        )

    async def close(self) -> None:
        await self._http.close()
