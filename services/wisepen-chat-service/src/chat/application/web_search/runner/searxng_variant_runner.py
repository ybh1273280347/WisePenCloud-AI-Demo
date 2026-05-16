from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from chat.application.web_search.cache import (
    SearchCache,
    make_search_cache_key,
    normalize_query,
)
from chat.application.web_search.models.helpers import has_response_content
from chat.application.web_search.planning import QueryVariant, VariantSearchResponse
from chat.application.web_search.provider_policy import effective_with_images
from chat.application.web_search.searcher.searxng_searcher import SearXNGSearcher
from common.logger import log_event, log_fail

_SERIAL_VARIANT_LIMIT = 2

_ROLE_PRIORITY = {
    "primary": 0,
    "secondary": 1,
    "extra": 2,
}


def _variant_execution_key(variant: QueryVariant, *, with_images: bool) -> tuple:
    return (
        normalize_query(variant.text),
        variant.language or "",
        tuple(sorted(variant.engines)) if variant.engines else (),
        with_images,
        variant.max_results,
    )


def _make_task_key(variant: QueryVariant, *, with_images: bool) -> str:
    norm_text = normalize_query(variant.text)
    lang = variant.language or ""
    engines_str = ",".join(sorted(variant.engines)) if variant.engines else ""
    return (
        f"{norm_text}|{lang}|{engines_str}|img={with_images}|mr={variant.max_results}"
    )


def _deduplicate_variants(
    variants: List[QueryVariant], *, with_images: bool
) -> List[QueryVariant]:
    seen: Dict[tuple, QueryVariant] = {}
    for variant in variants:
        key = _variant_execution_key(variant, with_images=with_images)
        existing = seen.get(key)
        if existing is None:
            seen[key] = variant
        else:
            existing_priority = _ROLE_PRIORITY.get(existing.role, 99)
            current_priority = _ROLE_PRIORITY.get(variant.role, 99)
            if current_priority < existing_priority:
                seen[key] = variant
                log_event(
                    "SearXNG variant dedup 替换为更高优先级 role",
                    task_key=_make_task_key(variant, with_images=with_images),
                    kept_role=variant.role,
                    replaced_role=existing.role,
                )
            else:
                log_event(
                    "SearXNG variant dedup 跳过重复",
                    task_key=_make_task_key(variant, with_images=with_images),
                    kept_role=existing.role,
                    skipped_role=variant.role,
                )
    return list(seen.values())


async def run_searxng_variants(
    *,
    search_call_id: str,
    variants: List[QueryVariant],
    searcher: SearXNGSearcher,
    cache: SearchCache,
    with_images: bool = False,
) -> List[VariantSearchResponse]:
    if not variants:
        return []

    deduped = _deduplicate_variants(variants, with_images=with_images)

    inflight: Dict[str, asyncio.Task[Optional[VariantSearchResponse]]] = {}

    parallel_variants = [v for v in deduped if not v.serial]
    serial_variants = [v for v in deduped if v.serial]

    results: List[VariantSearchResponse] = []

    if parallel_variants:
        parallel_results = await _run_parallel_variants(
            parallel_variants,
            searcher=searcher,
            cache=cache,
            with_images=with_images,
            search_call_id=search_call_id,
            inflight=inflight,
        )
        results.extend(parallel_results)

    if serial_variants:
        serial_results = await _run_serial_variants(
            serial_variants,
            searcher=searcher,
            cache=cache,
            with_images=with_images,
            search_call_id=search_call_id,
            inflight=inflight,
        )
        results.extend(serial_results)

    log_event(
        "SearXNG variant 搜索完成",
        search_call_id=search_call_id,
        variants_total=len(variants),
        variants_deduped=len(deduped),
        parallel=len(parallel_variants),
        serial=len(serial_variants),
        results=len(results),
    )

    return results


async def _schedule_variant(
    inflight: Dict[str, asyncio.Task[Optional[VariantSearchResponse]]],
    variant: QueryVariant,
    *,
    searcher: SearXNGSearcher,
    cache: SearchCache,
    with_images: bool,
    search_call_id: str,
) -> Optional[VariantSearchResponse]:
    provider_with_images = effective_with_images(
        requested=with_images,
        provider="searxng",
    )
    task_key = _make_task_key(variant, with_images=provider_with_images)

    existing = inflight.get(task_key)
    if existing is not None:
        log_event(
            "SearXNG task coalesced",
            search_call_id=search_call_id,
            task_key=task_key,
            role=variant.role,
        )
        return await existing

    task = asyncio.create_task(
        _run_one_variant(
            variant,
            searcher=searcher,
            cache=cache,
            with_images=with_images,
            search_call_id=search_call_id,
        )
    )
    inflight[task_key] = task
    return await task


async def _run_parallel_variants(
    variants: List[QueryVariant],
    *,
    searcher: SearXNGSearcher,
    cache: SearchCache,
    with_images: bool,
    search_call_id: str,
    inflight: Dict[str, asyncio.Task[Optional[VariantSearchResponse]]],
) -> List[VariantSearchResponse]:
    coros = [
        _schedule_variant(
            inflight,
            v,
            searcher=searcher,
            cache=cache,
            with_images=with_images,
            search_call_id=search_call_id,
        )
        for v in variants
    ]

    raw_results = await asyncio.gather(
        *coros,
        return_exceptions=True,
    )

    return [item for item in raw_results if isinstance(item, VariantSearchResponse)]


async def _run_serial_variants(
    variants: List[QueryVariant],
    *,
    searcher: SearXNGSearcher,
    cache: SearchCache,
    with_images: bool,
    search_call_id: str,
    inflight: Dict[str, asyncio.Task[Optional[VariantSearchResponse]]],
) -> List[VariantSearchResponse]:
    limited = variants[:_SERIAL_VARIANT_LIMIT]
    results: List[VariantSearchResponse] = []

    for variant in limited:
        result = await _schedule_variant(
            inflight,
            variant,
            searcher=searcher,
            cache=cache,
            with_images=with_images,
            search_call_id=search_call_id,
        )

        if result is not None:
            results.append(result)

    return results


async def _run_one_variant(
    variant: QueryVariant,
    *,
    searcher: SearXNGSearcher,
    cache: SearchCache,
    with_images: bool,
    search_call_id: str,
) -> Optional[VariantSearchResponse]:
    provider_with_images = effective_with_images(
        requested=with_images,
        provider="searxng",
    )
    cache_key = make_search_cache_key(
        source="searxng",
        query=variant.text,
        max_results=variant.max_results,
        with_images=provider_with_images,
        language=variant.language,
        engines=variant.engines,
        purpose="recall",
    )

    task_key = _make_task_key(variant, with_images=provider_with_images)

    log_event(
        "SearXNG variant start",
        search_call_id=search_call_id,
        task_key=task_key,
        query=variant.text,
        language=variant.language,
        engines=variant.engines,
        with_images=provider_with_images,
        max_results=variant.max_results,
        role=variant.role,
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
            engines=variant.engines,
            language=variant.language,
            search_call_id=search_call_id,
            task_key=task_key,
        )
    except Exception as e:
        log_fail(
            "SearXNG variant 搜索",
            repr(e),
            search_call_id=search_call_id,
            task_key=task_key,
            query=variant.text,
            role=variant.role,
            engines=variant.engines,
        )
        return None

    if not has_response_content(response):
        return None

    response = response.with_source("searxng")
    await cache.set(cache_key, response)

    return VariantSearchResponse(
        variant=variant,
        response=response,
        cache_hit=False,
    )
