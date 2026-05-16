from __future__ import annotations

import asyncio
import time


class SourceRateGate:
    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval_seconds = max(0.0, min_interval_seconds)
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait_seconds = self._min_interval_seconds - (now - self._last_request_at)
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            self._last_request_at = time.monotonic()
