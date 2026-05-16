from __future__ import annotations

from dataclasses import replace
from typing import List

import httpx

from chat.application.paper_search.http import get_json_with_retry
from chat.application.paper_search.models import PaperSearchResult
from chat.application.paper_search.rate_limit import SourceRateGate
from chat.core.config.app_settings import settings


class UnpaywallSource:
    name = "unpaywall"

    def __init__(self, client: httpx.AsyncClient, gate: SourceRateGate) -> None:
        self._client = client
        self._gate = gate

    async def enrich(self, result: PaperSearchResult) -> tuple[PaperSearchResult, List[str]]:
        if not settings.TOOL_CONTACT_EMAIL:
            return result, ["unpaywall skipped: TOOL_CONTACT_EMAIL is missing"]
        if not result.doi:
            return result, []

        await self._gate.wait()
        try:
            data = await get_json_with_retry(
                self._client,
                f"{settings.UNPAYWALL_BASE_URL}/v2/{result.doi}",
                params={"email": settings.TOOL_CONTACT_EMAIL},
            )
        except Exception as e:
            return result, [f"unpaywall failed for DOI {result.doi}: {e}"]

        best_location = data.get("best_oa_location") or {}
        if not isinstance(best_location, dict):
            best_location = {}
        pdf_url = best_location.get("url_for_pdf") or result.pdf_url
        landing_url = best_location.get("url") or result.url
        source_urls = _merge_unique(result.source_urls, [url for url in [landing_url, pdf_url] if url])

        return replace(
            result,
            pdf_url=pdf_url,
            url=landing_url,
            is_open_access=data.get("is_oa"),
            source_names=_merge_unique(result.source_names, ["unpaywall"]),
            source_urls=source_urls,
        ), []


async def enrich_with_unpaywall_serial(
    results: List[PaperSearchResult],
    source: UnpaywallSource,
    *,
    limit: int,
) -> tuple[List[PaperSearchResult], List[str]]:
    enriched: List[PaperSearchResult] = []
    warnings: List[str] = []
    for result in results[:limit]:
        updated, item_warnings = await source.enrich(result)
        enriched.append(updated)
        warnings.extend(item_warnings)
    enriched.extend(results[limit:])
    return enriched, warnings


def _merge_unique(left: List[str], right: List[str]) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for item in [*left, *right]:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result
