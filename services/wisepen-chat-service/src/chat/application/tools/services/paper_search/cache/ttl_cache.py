from __future__ import annotations

from typing import Generic, Optional, TypeVar

from cachetools import TTLCache as CachetoolsTTLCache

T = TypeVar("T")


class TtlCache(Generic[T]):
    def __init__(self, *, ttl_seconds: float, max_items: int = 128) -> None:
        self._cache: CachetoolsTTLCache[str, T] = CachetoolsTTLCache(
            maxsize=max_items,
            ttl=ttl_seconds,
        )

    def get(self, key: str) -> Optional[T]:
        return self._cache.get(key)

    def set(self, key: str, value: T) -> None:
        self._cache[key] = value

    def clear(self) -> None:
        self._cache.clear()
