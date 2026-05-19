from __future__ import annotations

from typing import Optional

import httpx

from chat.core.config.app_settings import build_tool_user_agent, settings

from ..config import DOI_HYDRATION_TIMEOUT_SECONDS
from ..models import DOIMetadataRecord
from ..parsers.crossref_parser import parse_crossref_work


class CrossrefDOIResolver:
    name = "crossref"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def resolve(self, doi: str) -> Optional[DOIMetadataRecord]:
        params = {}
        if settings.TOOL_CONTACT_EMAIL:
            params["mailto"] = settings.TOOL_CONTACT_EMAIL

        try:
            response = await self._client.get(
                f"{settings.CROSSREF_BASE_URL.rstrip('/')}/works/{doi}",
                headers={"User-Agent": build_tool_user_agent()},
                params=params,
                timeout=DOI_HYDRATION_TIMEOUT_SECONDS,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
        except Exception:
            return None

        return parse_crossref_work(data)
