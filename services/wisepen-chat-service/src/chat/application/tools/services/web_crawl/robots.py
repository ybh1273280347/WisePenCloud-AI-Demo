from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx


_ROBOTS_CACHE_TTL_SECONDS = 30 * 60
_DEFAULT_USER_AGENT = "WisePenBot"


@dataclass(frozen=True, slots=True)
class RobotsDecision:
    allowed: bool
    unavailable: bool = False
    reason: Optional[str] = None
    crawl_delay: Optional[float] = None


@dataclass(slots=True)
class _RobotsCacheEntry:
    parser: Optional[RobotFileParser]
    fetched_at: float
    unavailable: bool


class RobotsPolicy:
    def __init__(self, *, timeout_seconds: float = 5.0):
        self._timeout_seconds = timeout_seconds
        self._cache: Dict[str, _RobotsCacheEntry] = {}

    async def can_fetch(
        self,
        *,
        url: str,
        is_seed_url: bool,
        user_agent: str = _DEFAULT_USER_AGENT,
    ) -> RobotsDecision:
        parsed = urlparse(url)
        host_key = f"{parsed.scheme}://{parsed.netloc}".lower()
        entry = await self._get_or_fetch(host_key, user_agent=user_agent)

        if entry.unavailable:
            if is_seed_url:
                return RobotsDecision(
                    allowed=True,
                    unavailable=True,
                    reason="robots_unavailable_seed_allowed",
                )
            return RobotsDecision(
                allowed=False,
                unavailable=True,
                reason="robots_unavailable",
            )

        if entry.parser is None:
            return RobotsDecision(
                allowed=False,
                unavailable=True,
                reason="robots_unavailable",
            )

        allowed = entry.parser.can_fetch(user_agent, url)
        delay = entry.parser.crawl_delay(user_agent)
        return RobotsDecision(
            allowed=allowed,
            reason=None if allowed else "robots_disallowed",
            crawl_delay=float(delay) if delay is not None else None,
        )

    async def _get_or_fetch(
        self,
        host_key: str,
        *,
        user_agent: str,
    ) -> _RobotsCacheEntry:
        now = time.time()
        cached = self._cache.get(host_key)
        if cached is not None and now - cached.fetched_at <= _ROBOTS_CACHE_TTL_SECONDS:
            return cached

        robots_url = f"{host_key}/robots.txt"
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(robots_url, headers={"User-Agent": user_agent})
            if response.status_code >= 400:
                entry = _RobotsCacheEntry(parser=None, fetched_at=now, unavailable=True)
            else:
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(response.text.splitlines())
                entry = _RobotsCacheEntry(parser=parser, fetched_at=now, unavailable=False)
        except Exception:
            entry = _RobotsCacheEntry(parser=None, fetched_at=now, unavailable=True)

        self._cache[host_key] = entry
        return entry

