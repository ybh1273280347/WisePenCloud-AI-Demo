from __future__ import annotations

import asyncio
from typing import List, Optional, Sequence, Tuple

from chat.application.web_search.cache import SearchCache, make_search_cache_key
from chat.application.web_search.models.helpers import has_response_content
from chat.application.web_search.planning import QueryVariant, VariantSearchResponse
from chat.application.web_search.provider_policy import effective_with_images
from chat.application.web_search.searcher.serper_searcher import (
    SerperAuthError,
    SerperRateLimitError,
    SerperSearcher,
    SerperSearchError,
)
from chat.application.web_search.utils.notes import add_note
from common.logger import log_event, log_fail

_SERPER_PARALLEL_LIMIT = 2
_SERPER_CACHE_SOURCE = "serper"
_SERPER_CACHE_PURPOSE = "recall"
_SERPER_CACHE_ENGINES: Tuple[str, ...] = ("serper",)


async def run_serper_variants(
    *,
    variants: Sequence[QueryVariant],
    searcher: SerperSearcher,
    cache: SearchCache,
    with_images: bool = False,
    notes: Optional[List[str]] = None,
) -> List[VariantSearchResponse]:
    if not variants:
        return []

    semaphore = asyncio.Semaphore(_SERPER_PARALLEL_LIMIT)

    async def run_one(variant: QueryVariant) -> Optional[VariantSearchResponse]:
        async with semaphore:
            return await _run_one_serper_variant(
                variant,
                searcher=searcher,
                cache=cache,
                with_images=with_images,
                notes=notes,
            )

    raw_results = await asyncio.gather(
        *(run_one(variant) for variant in variants),
        return_exceptions=True,
    )

    results = [item for item in raw_results if isinstance(item, VariantSearchResponse)]

    log_event(
        "Serper variant 搜索完成",
        variants=len(variants),
        results=len(results),
    )

    return results


async def _run_one_serper_variant(
    variant: QueryVariant,
    *,
    searcher: SerperSearcher,
    cache: SearchCache,
    with_images: bool,
    notes: Optional[List[str]],
) -> Optional[VariantSearchResponse]:
    provider_with_images = effective_with_images(
        requested=with_images,
        provider="serper",
    )

    log_event(
        "Serper variant start",
        query=variant.text,
        language=variant.language,
        with_images=provider_with_images,
        max_results=variant.max_results,
        role=variant.role,
    )

    cache_key = make_search_cache_key(
        source=_SERPER_CACHE_SOURCE,
        query=variant.text,
        max_results=variant.max_results,
        with_images=provider_with_images,
        language=variant.language,
        engines=_SERPER_CACHE_ENGINES,
        purpose=_SERPER_CACHE_PURPOSE,
    )

    cached = await cache.get(cache_key)
    if cached is not None:
        return VariantSearchResponse(
            variant=variant,
            response=cached.response,
            cache_hit=True,
        )

    try:
        response = await searcher.search(
            variant.text,
            max_results=variant.max_results,
            with_images=provider_with_images,
            language=variant.language,
        )
    except SerperAuthError:
        add_note(
            notes,
            "Serper is enabled but authentication failed; Serper recall was skipped.",
        )
        return None
    except SerperRateLimitError:
        add_note(
            notes,
            "Serper rate limit was reached; Serper recall was skipped.",
        )
        return None
    except SerperSearchError as e:
        add_note(
            notes,
            "Serper recall failed; SearXNG results were kept.",
        )
        log_fail(
            "Serper variant 搜索",
            repr(e),
            query=variant.text,
            role=variant.role,
        )
        return None

    if not has_response_content(response):
        return None

    await cache.set(cache_key, response)

    return VariantSearchResponse(
        variant=variant,
        response=response,
        cache_hit=False,
    )
