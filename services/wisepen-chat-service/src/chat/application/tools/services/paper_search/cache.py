from __future__ import annotations

import time
from collections import OrderedDict
from typing import Generic, Optional, Tuple, TypeVar

T = TypeVar("T")


class TtlCache(Generic[T]):
    def __init__(self, *, ttl_seconds: float, max_items: int = 128) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_items = max_items
        self._items: OrderedDict[str, Tuple[float, T]] = OrderedDict()

    def get(self, key: str) -> Optional[T]:
        item = self._items.get(key)
        if item is None:
            return None

        created_at, value = item
        if time.monotonic() - created_at > self._ttl_seconds:
            self._items.pop(key, None)
            return None

        self._items.move_to_end(key)
        return value

    def set(self, key: str, value: T) -> None:
        self._items[key] = (time.monotonic(), value)
        self._items.move_to_end(key)
        while len(self._items) > self._max_items:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()
