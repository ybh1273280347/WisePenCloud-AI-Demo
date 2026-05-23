from __future__ import annotations

import asyncio
from typing import List, Optional, Sequence, Tuple

from chat.application.web_search.internal.cache import SearchCache, make_search_cache_key
from chat.application.web_search.internal.models.helpers import has_response_content
from chat.application.web_search.internal.planning.models import (
    QueryVariant,
    VariantSearchResponse,
)
from chat.application.web_search.internal.searcher.fourget_searcher import (
    FourGetSearcher,
)
from common.logger import log_event, log_fail

_FOURGET_PARALLEL_LIMIT = 2
_FOURGET_CACHE_SOURCE = "fourget"
_FOURGET_CACHE_PURPOSE = "recall"
_FOURGET_CACHE_ENGINES: Tuple[str, ...] = ("fourget",)


async def run_fourget_variants(
    *,
    search_call_id: str,
    variants: Sequence[QueryVariant],
    searcher: FourGetSearcher,
    cache: SearchCache,
    with_images: bool = False,
) -> List[VariantSearchResponse]:
    if not variants:
        return []

    semaphore = asyncio.Semaphore(_FOURGET_PARALLEL_LIMIT)

    async def run_one(variant: QueryVariant) -> Optional[VariantSearchResponse]:
        async with semaphore:
            return await _run_one_fourget_variant(
                variant,
                searcher=searcher,
                cache=cache,
                with_images=with_images,
                search_call_id=search_call_id,
            )

    raw_results = await asyncio.gather(
        *(run_one(variant) for variant in variants),
        return_exceptions=True,
    )

    results = [item for item in raw_results if isinstance(item, VariantSearchResponse)]

    log_event(
        "FourGet variant 搜索完成",
        search_call_id=search_call_id,
        variants=len(variants),
        results=len(results),
    )

    return results


async def _run_one_fourget_variant(
    variant: QueryVariant,
    *,
    searcher: FourGetSearcher,
    cache: SearchCache,
    with_images: bool,
    search_call_id: str,
) -> Optional[VariantSearchResponse]:
    task_key = _make_task_key(variant, with_images=with_images)

    log_event(
        "FourGet variant start",
        search_call_id=search_call_id,
        task_key=task_key,
        query=variant.text,
        language=variant.language,
        with_images=with_images,
        max_results=variant.max_results,
        role=variant.role,
    )

    cache_key = make_search_cache_key(
        source=_FOURGET_CACHE_SOURCE,
        query=variant.text,
        max_results=variant.max_results,
        with_images=with_images,
        language=variant.language,
        engines=_FOURGET_CACHE_ENGINES,
        purpose=_FOURGET_CACHE_PURPOSE,
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
            with_images=with_images,
            engines=None,
            language=variant.language,
            search_call_id=search_call_id,
            task_key=task_key,
        )
    except Exception as e:
        log_fail(
            "FourGet variant 搜索",
            repr(e),
            search_call_id=search_call_id,
            task_key=task_key,
            query=variant.text,
            role=variant.role,
        )
        return None

    if not has_response_content(response):
        return None

    response = response.with_source(_FOURGET_CACHE_SOURCE)
    await cache.set(cache_key, response)

    return VariantSearchResponse(
        variant=variant,
        response=response,
        cache_hit=False,
    )


def _make_task_key(variant: QueryVariant, *, with_images: bool) -> str:
    lang = variant.language or ""
    return f"{variant.text.strip().lower()}|{lang}|fourget|img={with_images}|mr={variant.max_results}"
