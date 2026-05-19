from __future__ import annotations

from typing import Optional

import httpx

from chat.core.config.app_settings import build_tool_user_agent, settings

from ..config import DOI_HYDRATION_TIMEOUT_SECONDS
from ..models import DOIMetadataRecord
from ..parsers.csl_json_parser import parse_csl_json


class DOIContentNegotiationResolver:
    name = "doi_content_negotiation"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def resolve(self, doi: str) -> Optional[DOIMetadataRecord]:
        try:
            response = await self._client.get(
                f"{settings.DOI_BASE_URL.rstrip('/')}/{doi}",
                headers={
                    "User-Agent": build_tool_user_agent(),
                    "Accept": "application/vnd.citationstyles.csl+json",
                },
                timeout=DOI_HYDRATION_TIMEOUT_SECONDS,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
        except Exception:
            return None

        return parse_csl_json(data)
