from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from chat.application.web_crawl.models import CrawlFrontierItem, CrawlSkipReason

MAX_EXTERNAL_DEPTH = 1
MAX_PAGES_PER_EXTERNAL_HOST = 2


@dataclass(slots=True)
class CrawlScopeState:
    origin_hosts: Set[str]
    max_external_pages_total: int
    max_pages_per_external_host: int = MAX_PAGES_PER_EXTERNAL_HOST
    max_external_depth: int = MAX_EXTERNAL_DEPTH
    external_pages_total: int = 0
    pages_by_external_host: Dict[str, int] = field(default_factory=dict)


class CrawlFrontier:
    def __init__(
        self,
        *,
        seed_urls: List[str],
        max_pages: int,
        max_depth: int,
    ):
        self._max_pages = max_pages
        self._max_depth = max_depth
        self._visited: Set[str] = set()
        self._queued: Set[str] = set()
        self._pending_by_depth: Dict[int, List[CrawlFrontierItem]] = defaultdict(list)
        self._accepted_count = 0
        self._queued_external_total = 0
        self._queued_by_external_host: Dict[str, int] = {}

        origin_hosts = {host for url in seed_urls for host in [_host_of(url)] if host}
        self.scope = CrawlScopeState(
            origin_hosts=origin_hosts,
            max_external_pages_total=min(5, max_pages // 2),
        )

        for url in seed_urls:
            host = _host_of(url)
            self.add_seed(url=url, host=host)

    def add_seed(self, *, url: str, host: str) -> bool:
        if url in self._queued:
            return False

        self._queued.add(url)
        self._pending_by_depth[0].append(
            CrawlFrontierItem(
                url=url,
                depth=0,
                origin_host=host,
                current_host=host,
                score=1.0,
                is_external=False,
                external_depth=0,
            )
        )
        return True

    def add_candidate(self, item: CrawlFrontierItem) -> Tuple[bool, Optional[str]]:
        if item.depth > self._max_depth:
            return False, CrawlSkipReason.DEPTH_LIMIT.value

        if self.reached_page_budget():
            return False, CrawlSkipReason.PAGE_LIMIT.value

        if item.url in self._visited or item.url in self._queued:
            return False, CrawlSkipReason.DUPLICATE_URL.value

        ok, reason = self._check_external_budget(item)
        if not ok:
            return False, reason

        self._queued.add(item.url)
        self._pending_by_depth[item.depth].append(item)
        self._mark_scope_queued(item)
        return True, None

    def pop_batch_for_depth(self, depth: int, *, limit: int) -> List[CrawlFrontierItem]:
        items = self._pending_by_depth.pop(depth, [])
        items.sort(key=lambda item: item.score, reverse=True)

        remaining_budget = max(0, self._max_pages - self._accepted_count)
        selected = items[: min(limit, remaining_budget)]

        for item in selected:
            self._visited.add(item.url)
            self._accepted_count += 1
            self._mark_scope_fetched(item)

        return selected

    def reached_page_budget(self) -> bool:
        return self._accepted_count >= self._max_pages

    def has_pending(self) -> bool:
        return any(self._pending_by_depth.values())

    def _check_external_budget(
        self, item: CrawlFrontierItem
    ) -> Tuple[bool, Optional[str]]:
        if not item.is_external:
            return True, None

        if item.external_depth > self.scope.max_external_depth:
            return False, CrawlSkipReason.EXTERNAL_DEPTH_LIMIT.value

        if self._queued_external_total >= self.scope.max_external_pages_total:
            return False, CrawlSkipReason.EXTERNAL_BUDGET_EXCEEDED.value

        host_count = self._queued_by_external_host.get(item.current_host, 0)
        if host_count >= self.scope.max_pages_per_external_host:
            return False, CrawlSkipReason.EXTERNAL_HOST_BUDGET_EXCEEDED.value

        return True, None

    def _mark_scope_queued(self, item: CrawlFrontierItem) -> None:
        if not item.is_external:
            return
        self._queued_external_total += 1
        self._queued_by_external_host[item.current_host] = (
            self._queued_by_external_host.get(item.current_host, 0) + 1
        )

    def _mark_scope_fetched(self, item: CrawlFrontierItem) -> None:
        if not item.is_external:
            return
        self.scope.external_pages_total += 1
        self.scope.pages_by_external_host[item.current_host] = (
            self.scope.pages_by_external_host.get(item.current_host, 0) + 1
        )


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()
