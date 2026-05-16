from __future__ import annotations

import asyncio
from typing import List

import httpx

from chat.core.config.app_settings import settings

from .cache import TtlCache
from .config import (
    PAPER_SEARCH_CACHE_TTL_SECONDS,
    PAPER_SEARCH_MAX_RESULTS,
    PAPER_SEARCH_TIMEOUT_SECONDS,
    SOURCE_RATE_LIMIT_SECONDS,
    UNPAYWALL_ENRICH_LIMIT,
)
from .dedup import deduplicate_paper_candidates
from .models import PaperSearchRequest, PaperSearchResponse
from .query import normalize_query
from .ranking import normalize_paper_candidates, rank_paper_candidates
from .rate_limit import SourceRateGate
from .sources import (
    ArxivSource,
    CrossrefSource,
    DataCiteSource,
    UnpaywallSource,
    enrich_with_unpaywall_serial,
)


_crossref_gate = SourceRateGate(SOURCE_RATE_LIMIT_SECONDS)
_datacite_gate = SourceRateGate(SOURCE_RATE_LIMIT_SECONDS)
_unpaywall_gate = SourceRateGate(SOURCE_RATE_LIMIT_SECONDS)


class PaperSearchService:
    def __init__(self) -> None:
        self._cache: TtlCache[PaperSearchResponse] = TtlCache(
            ttl_seconds=PAPER_SEARCH_CACHE_TTL_SECONDS,
            max_items=128,
        )

    async def search(self, request: PaperSearchRequest) -> PaperSearchResponse:
        query = normalize_query(request.query)
        if not query:
            raise ValueError("query is required.")
        max_results = max(1, min(int(request.max_results), PAPER_SEARCH_MAX_RESULTS))
        cache_key = f"{query.lower()}|{max_results}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        headers = {"User-Agent": settings.TOOL_USER_AGENT}
        async with httpx.AsyncClient(timeout=PAPER_SEARCH_TIMEOUT_SECONDS, headers=headers) as client:
            source_tasks = self._build_source_tasks(client, query=query)
            source_responses = await asyncio.gather(*source_tasks, return_exceptions=True)

            searched_sources: List[str] = []
            failed_sources: List[str] = []
            warnings: List[str] = []
            raw_results = []

            for response in source_responses:
                if isinstance(response, Exception):
                    warnings.append(f"source task failed: {response}")
                    continue
                searched_sources.append(response.source_name)
                warnings.extend(response.warnings)
                if response.failed:
                    failed_sources.append(response.source_name)
                raw_results.extend(response.results)

            main_sources = {"crossref", "arxiv", "datacite"}
            searched_main_sources = [source for source in searched_sources if source in main_sources]
            if searched_main_sources and all(source in failed_sources for source in searched_main_sources):
                raise RuntimeError("all paper search sources failed")

            candidates = normalize_paper_candidates(raw_results)
            deduped_candidates = deduplicate_paper_candidates(candidates)
            ranked = rank_paper_candidates(query=query, candidates=deduped_candidates)
            skipped_sources: List[str] = []

            if settings.PAPER_SEARCH_ENABLE_UNPAYWALL:
                if settings.TOOL_CONTACT_EMAIL:
                    unpaywall = UnpaywallSource(client, _unpaywall_gate)
                    ranked, unpaywall_warnings = await enrich_with_unpaywall_serial(
                        ranked[:max_results],
                        unpaywall,
                        limit=UNPAYWALL_ENRICH_LIMIT,
                    )
                    warnings.extend(unpaywall_warnings)
                else:
                    skipped_sources.append("unpaywall: TOOL_CONTACT_EMAIL is missing")
                    warnings.append("unpaywall skipped: TOOL_CONTACT_EMAIL is missing")

            response = PaperSearchResponse(
                query=query,
                results=ranked[:max_results],
                searched_sources=searched_sources,
                skipped_sources=skipped_sources,
                failed_sources=failed_sources,
                warnings=warnings,
            )
            self._cache.set(cache_key, response)
            return response

    async def close(self) -> None:
        self._cache.clear()

    def _build_source_tasks(self, client: httpx.AsyncClient, *, query: str) -> List[asyncio.Task]:
        tasks = []
        if settings.PAPER_SEARCH_ENABLE_CROSSREF:
            tasks.append(CrossrefSource(client, _crossref_gate).search(query, rows=5))
        if settings.PAPER_SEARCH_ENABLE_ARXIV:
            tasks.append(ArxivSource().search(query, rows=5))
        if settings.PAPER_SEARCH_ENABLE_DATACITE:
            tasks.append(DataCiteSource(client, _datacite_gate).search(query, rows=5))
        return tasks
