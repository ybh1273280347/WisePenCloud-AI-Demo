from __future__ import annotations

from typing import List

import httpx

from chat.core.config.app_settings import settings

from ..config import FIND_SIMILAR_RESULTS_PER_SEED, FIND_SIMILAR_SEED_COUNT
from ..models import PaperEntity, PaperPointer
from ..sources.exa_search_source import map_exa_item_to_pointer


class ExaFindSimilarExpander:
    name = "exa_find_similar"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def expand(
        self,
        *,
        seeds: List[PaperEntity],
    ) -> tuple[List[PaperPointer], List[str]]:
        api_key = getattr(settings, "EXA_API_KEY", None)
        if not api_key:
            return [], ["exa findSimilar skipped: EXA_API_KEY is missing"]

        pointers: List[PaperPointer] = []
        warnings: List[str] = []

        for seed in seeds[:FIND_SIMILAR_SEED_COUNT]:
            if not seed.url:
                continue

            try:
                response = await self._client.post(
                    f"{settings.EXA_BASE_URL.rstrip('/')}/findSimilar",
                    headers={
                        "x-api-key": api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "url": seed.url,
                        "numResults": FIND_SIMILAR_RESULTS_PER_SEED,
                        "contents": {"highlights": True},
                    },
                )
                if response.status_code in (401, 403):
                    warnings.append(
                        f"exa findSimilar failed for {seed.url}: authentication failed"
                    )
                    continue
                if response.status_code == 429:
                    warnings.append(
                        f"exa findSimilar failed for {seed.url}: quota or rate limit reached"
                    )
                    continue
                response.raise_for_status()
            except Exception as e:
                warnings.append(f"exa findSimilar failed for {seed.url}: {e}")
                continue

            data = response.json()
            raw_results = data.get("results")
            if not isinstance(raw_results, list):
                warnings.append(f"exa findSimilar results is not a list for {seed.url}")
                continue

            for rank, item in enumerate(raw_results):
                pointer = map_exa_item_to_pointer(
                    item=item,
                    rank=rank,
                    rewrite_query=f"findSimilar:{seed.canonical_id}",
                    source_name=self.name,
                )
                if pointer is not None:
                    pointers.append(pointer)

        return pointers, warnings
