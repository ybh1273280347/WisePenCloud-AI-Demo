from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

import httpx

from chat.core.config.app_settings import settings

from .cache.doi_hydration_cache import DOIHydrationCache
from .cache.ttl_cache import TtlCache
from .candidates import build_entity_from_pointer
from .config import (
    DOI_HYDRATION_MAX_CONCURRENCY,
    PAPER_SEARCH_CACHE_TTL_SECONDS,
    PAPER_SEARCH_TIMEOUT_SECONDS,
)
from .doi_queue import (
    collect_dois_for_hydration,
    doi_hydration_limit,
    select_dois_for_hydration,
)
from .entity_fusion import fuse_entities, merge_doi_records_into_entities
from .expanders import ExaFindSimilarExpander
from .freshness import ArxivDeltaIndex
from .hydrators import (
    ArxivHydrator,
    CrossrefDOIResolver,
    DataCiteDOIResolver,
    DOIContentNegotiationResolver,
    DOIHydrationRouter,
)
from .identifiers import apply_identifier_extraction
from .models import PaperEntity, PaperPointer, PaperSearchDepth, PaperSearchFreshness, PaperSearchRequest, PaperSearchResponse
from .query import normalize_query, validate_request
from .query_variants import build_query_variants
from .ranking import rank_entities
from .sources import ExaSearchSource

_EXA_WEB_SEARCH_FALLBACK_WARNING = (
    "exa failed: use web_search as a recall fallback and tell the user Exa discovery is unavailable."
)


class PaperSearchService:
    def __init__(
        self,
        *,
        delta_index: Optional[ArxivDeltaIndex] = None,
        doi_cache: Optional[DOIHydrationCache] = None,
    ) -> None:
        self._cache: TtlCache[PaperSearchResponse] = TtlCache(
            ttl_seconds=PAPER_SEARCH_CACHE_TTL_SECONDS,
            max_items=128,
        )
        self._arxiv_delta_index = delta_index or ArxivDeltaIndex()
        self._doi_cache = doi_cache or DOIHydrationCache()

    async def search(self, request: PaperSearchRequest) -> PaperSearchResponse:
        validate_request(request)

        query = normalize_query(request.query)
        variants = build_query_variants(request)
        cache_key = _cache_key(query=query, request=request, variants=variants)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        searched_sources: List[str] = []
        skipped_sources: List[str] = []
        failed_sources: List[str] = []
        warnings: List[str] = []

        async with httpx.AsyncClient(timeout=PAPER_SEARCH_TIMEOUT_SECONDS) as client:
            exa_source = ExaSearchSource(client)
            arxiv_hydrator = ArxivHydrator(client)
            doi_router = DOIHydrationRouter(
                CrossrefDOIResolver(client),
                DataCiteDOIResolver(client),
                DOIContentNegotiationResolver(client),
                self._doi_cache,
                max_concurrency=DOI_HYDRATION_MAX_CONCURRENCY,
            )
            exa_expander = ExaFindSimilarExpander(client)

            pointers = await self._collect_initial_pointers(
                exa_source=exa_source,
                request=request,
                query=query,
                variants=variants,
                searched_sources=searched_sources,
                skipped_sources=skipped_sources,
                failed_sources=failed_sources,
                warnings=warnings,
            )

            entities = await self._hydrate_fuse_rank_ready_entities(
                arxiv_hydrator=arxiv_hydrator,
                doi_router=doi_router,
                pointers=pointers,
                request=request,
                warnings=warnings,
            )

            ranked = self._rank_entities(
                entities=entities,
                pointers=pointers,
                query=query,
                request=request,
            )

            if (
                request.depth == PaperSearchDepth.DEEP
                and settings.PAPER_SEARCH_ENABLE_EXA_FIND_SIMILAR
            ):
                expansion_pointers, expansion_warnings = await exa_expander.expand(
                    seeds=ranked
                )
                warnings.extend(expansion_warnings)

                if expansion_pointers:
                    if "exa_find_similar" not in searched_sources:
                        searched_sources.append("exa_find_similar")
                    all_pointers = [
                        *pointers,
                        *[apply_identifier_extraction(pointer) for pointer in expansion_pointers],
                    ]
                    entities = await self._hydrate_fuse_rank_ready_entities(
                        arxiv_hydrator=arxiv_hydrator,
                        doi_router=doi_router,
                        pointers=all_pointers,
                        request=request,
                        warnings=warnings,
                    )
                    ranked = self._rank_entities(
                        entities=entities,
                        pointers=all_pointers,
                        query=query,
                        request=request,
                    )

        response = PaperSearchResponse(
            query=query,
            results=ranked[: request.max_results],
            searched_sources=searched_sources,
            skipped_sources=skipped_sources,
            failed_sources=failed_sources,
            warnings=warnings,
        )
        self._cache.set(cache_key, response)
        return response

    async def close(self) -> None:
        self._cache.clear()
        self._doi_cache.clear()

    async def _collect_initial_pointers(
        self,
        *,
        exa_source: ExaSearchSource,
        request: PaperSearchRequest,
        query: str,
        variants: List[str],
        searched_sources: List[str],
        skipped_sources: List[str],
        failed_sources: List[str],
        warnings: List[str],
    ) -> List[PaperPointer]:
        pointers: List[PaperPointer] = []

        if request.freshness == PaperSearchFreshness.LATEST:
            if settings.PAPER_SEARCH_ENABLE_ARXIV_MONITOR:
                delta_pointers = self._arxiv_delta_index.search(
                    query,
                    max_results=request.max_results * 3,
                )
                pointers.extend(delta_pointers)
                searched_sources.append("arxiv_delta_index")
            else:
                skipped_sources.append("arxiv_delta_index: disabled")

        if not settings.PAPER_SEARCH_ENABLE_EXA:
            skipped_sources.append("exa: disabled")
            return [apply_identifier_extraction(pointer) for pointer in pointers]

        exa_failed = True
        exa_failure_warnings: List[str] = []
        for variant in variants:
            rewrite_pointers, rewrite_warnings = await exa_source.search(
                query=query,
                rewrite_query=variant,
                depth=request.depth,
                freshness=request.freshness,
            )
            pointers.extend(rewrite_pointers)
            warnings.extend(rewrite_warnings)
            exa_failure_warnings.extend(
                warning
                for warning in rewrite_warnings
                if warning.startswith("exa search failed:")
            )
            if rewrite_pointers:
                exa_failed = False

        searched_sources.append("exa")
        if exa_failure_warnings and "exa" not in failed_sources:
            failed_sources.append("exa")
            warnings.append(_EXA_WEB_SEARCH_FALLBACK_WARNING)
        elif exa_failed and not pointers:
            failed_sources.append("exa")
            warnings.append(_EXA_WEB_SEARCH_FALLBACK_WARNING)

        return [apply_identifier_extraction(pointer) for pointer in pointers]

    async def _hydrate_fuse_rank_ready_entities(
        self,
        *,
        arxiv_hydrator: ArxivHydrator,
        doi_router: DOIHydrationRouter,
        pointers: List[PaperPointer],
        request: PaperSearchRequest,
        warnings: List[str],
    ) -> List[PaperEntity]:
        candidates = [build_entity_from_pointer(pointer) for pointer in pointers]
        entities = fuse_entities(candidates)

        if settings.PAPER_SEARCH_ENABLE_ARXIV_HYDRATION:
            arxiv_entities, arxiv_warnings = await arxiv_hydrator.hydrate(pointers)
            warnings.extend(arxiv_warnings)
            entities = fuse_entities([*entities, *arxiv_entities])

        if not settings.PAPER_SEARCH_ENABLE_DOI_HYDRATION:
            return entities

        rough_ranked = self._rank_entities(
            entities=entities,
            pointers=pointers,
            query=request.query,
            request=request,
        )
        dois = collect_dois_for_hydration(rough_ranked)
        dois = select_dois_for_hydration(
            entities=rough_ranked,
            dois=dois,
            limit=doi_hydration_limit(request.depth),
        )

        doi_records, doi_failures = await doi_router.hydrate_many(dois)
        entities = merge_doi_records_into_entities(
            entities=entities,
            records=doi_records,
            failures=doi_failures,
        )

        return fuse_entities(entities)

    def _rank_entities(
        self,
        *,
        entities: List[PaperEntity],
        pointers: List[PaperPointer],
        query: str,
        request: PaperSearchRequest,
    ) -> List[PaperEntity]:
        return rank_entities(
            query=query,
            entities=entities,
            per_rewrite_rankings=_per_rewrite_rankings(pointers),
            freshness=request.freshness,
            reference_date=date.today(),
        )


def _per_rewrite_rankings(pointers: List[PaperPointer]) -> Dict[str, List[str]]:
    by_rewrite: Dict[str, List[str]] = {}
    for pointer in pointers:
        entity = build_entity_from_pointer(pointer)
        by_rewrite.setdefault(pointer.rewrite_query, [])
        if entity.canonical_id not in by_rewrite[pointer.rewrite_query]:
            by_rewrite[pointer.rewrite_query].append(entity.canonical_id)
    return by_rewrite


def _cache_key(
    *,
    query: str,
    request: PaperSearchRequest,
    variants: List[str],
) -> str:
    return "|".join(
        [
            query.lower(),
            str(request.max_results),
            request.freshness.value,
            request.depth.value,
            ",".join(variant.lower() for variant in variants),
        ]
    )
