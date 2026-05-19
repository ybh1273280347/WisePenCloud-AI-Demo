from __future__ import annotations

from typing import List

import httpx

from chat.core.config.app_settings import build_tool_user_agent, settings

from ..config import ARXIV_MONITOR_TIMEOUT_SECONDS
from ..rate_limit import SourceRateGate
from .arxiv_delta_index import ArxivDeltaIndex
from .arxiv_rss_parser import parse_arxiv_atom_feed


class ArxivMonitor:
    def __init__(
        self,
        client: httpx.AsyncClient,
        delta_index: ArxivDeltaIndex,
        gate: SourceRateGate,
    ) -> None:
        self._client = client
        self._delta_index = delta_index
        self._gate = gate

    async def sync_categories(self, categories: List[str]) -> List[str]:
        warnings: List[str] = []

        for category in categories:
            await self._gate.wait()
            url = f"{settings.ARXIV_RSS_BASE_URL.rstrip('/')}/{category}"

            try:
                response = await self._client.get(
                    url,
                    headers={"User-Agent": build_tool_user_agent()},
                    timeout=ARXIV_MONITOR_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
            except Exception as e:
                warnings.append(f"arxiv rss sync failed for {category}: {e}")
                continue

            try:
                records = parse_arxiv_atom_feed(response.text, source_feed=url)
            except Exception as e:
                warnings.append(f"arxiv rss parse failed for {category}: {e}")
                continue

            self._delta_index.upsert_many(records)

        return warnings
