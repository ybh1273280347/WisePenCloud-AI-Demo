from __future__ import annotations

from typing import List, Optional

from chat.application.tools.services.software_ecosystem import config
from chat.application.tools.services.software_ecosystem.common.errors import (
    InvalidSoftwareEcosystemQueryError,
)
from chat.application.tools.services.software_ecosystem.common.normalization import normalize_query
from chat.application.tools.services.software_ecosystem.providers.hacker_news import (
    HackerNewsClient,
    map_hacker_news_hit,
)

from .models import CommunityDiscussionSignal
from .ranking import rank_community_discussions


class CommunityDiscussionService:
    def __init__(self, client: Optional[HackerNewsClient] = None) -> None:
        self._client = client or HackerNewsClient()

    async def search(
        self,
        *,
        query: str,
        limit: int,
    ) -> List[CommunityDiscussionSignal]:
        query = _validate_query(query)
        _validate_limit(limit)
        payload = await self._client.search(query=query, limit=limit)
        hits = payload.get("hits") if isinstance(payload, dict) else None
        if not isinstance(hits, list):
            return []
        signals = [
            signal
            for item in hits
            if isinstance(item, dict)
            for signal in [map_hacker_news_hit(item)]
            if signal is not None
        ]
        return rank_community_discussions(query, signals)[:limit]

    async def close(self) -> None:
        await self._client.close()


def _validate_query(query: str) -> str:
    if not isinstance(query, str):
        raise InvalidSoftwareEcosystemQueryError("query must be a string")
    normalized = normalize_query(query)
    if not normalized:
        raise InvalidSoftwareEcosystemQueryError("query must not be empty")
    return normalized


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise InvalidSoftwareEcosystemQueryError("limit must be an integer")
    if limit < 1 or limit > config.SOFTWARE_ECOSYSTEM_MAX_LIMIT:
        raise InvalidSoftwareEcosystemQueryError(
            f"limit must be between 1 and {config.SOFTWARE_ECOSYSTEM_MAX_LIMIT}"
        )

