from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Dict
from urllib.parse import urlparse


@dataclass(slots=True)
class _HostState:
    lock: asyncio.Lock
    last_request_at: float = 0.0
    blocked: bool = False


class PerHostPoliteness:
    def __init__(self, *, min_interval_seconds: float = 1.0):
        self._min_interval_seconds = min_interval_seconds
        self._states: Dict[str, _HostState] = {}

    def mark_blocked(self, url: str) -> None:
        state = self._get_state(url)
        state.blocked = True

    def is_blocked(self, url: str) -> bool:
        return self._get_state(url).blocked

    async def wait_turn(self, url: str) -> None:
        state = self._get_state(url)
        async with state.lock:
            now = time.monotonic()
            elapsed = now - state.last_request_at
            wait_seconds = self._min_interval_seconds - elapsed
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            state.last_request_at = time.monotonic()

    def _get_state(self, url: str) -> _HostState:
        host = (urlparse(url).hostname or "").lower()
        state = self._states.get(host)
        if state is None:
            state = _HostState(lock=asyncio.Lock())
            self._states[host] = state
        return state

