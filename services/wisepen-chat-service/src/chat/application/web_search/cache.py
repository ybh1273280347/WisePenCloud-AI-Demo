from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from cachetools import TTLCache
from chat.application.web_search.models.common import SearchResponse
from chat.application.web_search.models.helpers import is_valid_result

SEARCH_CACHE_KEY_VERSION = "v1"

SEARCH_CACHE_TTL_SECONDS = {
    "recall": 30 * 60,
    "grounding": 24 * 3600,
}

_VALID_PURPOSES = frozenset(SEARCH_CACHE_TTL_SECONDS.keys())

_WEB_SEARCH_CACHE_MAXSIZE = 1024
_DEFAULT_MAXSIZE = _WEB_SEARCH_CACHE_MAXSIZE


@dataclass(frozen=True, slots=True)
class CachedSearchResponse:
    response: SearchResponse
    cached_at: float
    cache_key: str
    cache_hit: bool = True


def normalize_query(query: str) -> str:
    return " ".join(query.strip().lower().split())


def normalize_optional_token(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    value = value.strip().lower()
    return value or None


def normalize_engines(engines: Optional[Tuple[str, ...]]) -> Tuple[str, ...]:
    if not engines:
        return ()

    return tuple(sorted(engine.strip().lower() for engine in engines if engine.strip()))


def make_search_cache_key(
    *,
    source: str,
    query: str,
    max_results: int,
    with_images: bool,
    language: Optional[str] = None,
    engines: Optional[Tuple[str, ...]] = None,
    purpose: str = "recall",
    version: str = SEARCH_CACHE_KEY_VERSION,
    backend_mode: str = "platform",
    provider_mode: str = "default",
    user_id_hash: Optional[str] = None,
    provider_params_hash: Optional[str] = None,
) -> str:
    normalized_source = source.strip().lower()
    normalized_purpose = purpose.strip().lower()
    normalized_backend_mode = backend_mode.strip().lower()
    normalized_provider_mode = provider_mode.strip().lower()

    payload = {
        "source": normalized_source,
        "purpose": normalized_purpose,
        "backend_mode": normalized_backend_mode,
        "provider_mode": normalized_provider_mode,
        "user_id_hash": normalize_optional_token(user_id_hash),
        "query": normalize_query(query),
        "max_results": max_results,
        "with_images": with_images,
        "language": normalize_optional_token(language),
        "engines": normalize_engines(engines),
        "provider_params_hash": normalize_optional_token(provider_params_hash),
    }

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

    return f"search:{normalized_purpose}:{version}:{digest}"


def _is_usable_response(response: SearchResponse) -> bool:
    return any(is_valid_result(result) for result in response.results)


def _parse_purpose_from_key(key: str) -> str:
    parts = key.split(":", 3)
    if len(parts) >= 2:
        return parts[1]
    return "recall"


class SearchCache:
    def __init__(
        self,
        *,
        maxsize: int = _DEFAULT_MAXSIZE,
    ) -> None:
        self._caches: Dict[str, TTLCache[str, CachedSearchResponse]] = {
            purpose: TTLCache(maxsize=maxsize, ttl=ttl)
            for purpose, ttl in SEARCH_CACHE_TTL_SECONDS.items()
        }
        self._lock = asyncio.Lock()

    def _get_cache(self, key: str) -> TTLCache[str, CachedSearchResponse]:
        purpose = _parse_purpose_from_key(key)
        return self._caches.get(purpose, self._caches["recall"])

    async def get(self, key: str) -> Optional[CachedSearchResponse]:
        async with self._lock:
            return self._get_cache(key).get(key)

    async def set(
        self,
        key: str,
        response: SearchResponse,
        *,
        cached_at: Optional[float] = None,
    ) -> None:
        if not _is_usable_response(response):
            return

        wrapper = CachedSearchResponse(
            response=response,
            cached_at=cached_at or time.time(),
            cache_key=key,
        )

        async with self._lock:
            self._get_cache(key)[key] = wrapper

    async def get_many(
        self,
        keys: List[str],
    ) -> Tuple[Dict[str, CachedSearchResponse], List[str]]:
        hits: Dict[str, CachedSearchResponse] = {}
        misses: List[str] = []

        for key in keys:
            cached = await self.get(key)

            if cached is None:
                misses.append(key)
                continue

            hits[key] = cached

        return hits, misses

    async def get_fresh_many(
        self,
        queries: List[str],
        *,
        max_results: int,
        with_images: bool,
        language: Optional[str] = None,
        source: str = "searxng",
        purpose: str = "recall",
        engines: Optional[Tuple[str, ...]] = None,
        backend_mode: str = "platform",
        user_id_hash: Optional[str] = None,
    ) -> Tuple[List[SearchResponse], List[str]]:
        hits: List[SearchResponse] = []
        misses: List[str] = []

        for query in queries:
            key = make_search_cache_key(
                source=source,
                query=query,
                max_results=max_results,
                with_images=with_images,
                language=language,
                engines=engines,
                purpose=purpose,
                backend_mode=backend_mode,
                user_id_hash=user_id_hash,
            )
            cached = await self.get(key)

            if cached is None:
                misses.append(query)
                continue

            hits.append(cached.response)

        return hits, misses
