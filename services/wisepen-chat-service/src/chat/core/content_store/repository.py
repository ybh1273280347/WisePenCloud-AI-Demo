import threading
from typing import Optional

from cachetools import TTLCache

from .models import StoredContent


class TTLContentRepository:
    def __init__(
        self,
        *,
        ttl_seconds: int,
        max_total_chars: int,
    ):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than 0")

        if max_total_chars <= 0:
            raise ValueError("max_total_chars must be greater than 0")

        self._lock = threading.RLock()
        self._items: TTLCache[str, StoredContent] = TTLCache(
            maxsize=max_total_chars,
            ttl=ttl_seconds,
            # max_total_chars is a stored-text character budget, not a strict memory limit.
            getsizeof=lambda item: len(item.text),
        )

    def put(self, content: StoredContent) -> None:
        with self._lock:
            self._items.expire()
            self._items[content.content_id] = content

    def get(self, content_id: str) -> Optional[StoredContent]:
        # StoredContent is returned by reference and should be treated as read-only.
        # Avoid mutating item.text, item.chunks, or item.metadata outside the repository.
        with self._lock:
            self._items.expire()
            return self._items.get(content_id)

    def delete(self, content_id: str) -> None:
        with self._lock:
            self._items.pop(content_id, None)

    def expire(self) -> None:
        with self._lock:
            self._items.expire()

    def clear(self) -> None:
        with self._lock:
            self._items.clear()