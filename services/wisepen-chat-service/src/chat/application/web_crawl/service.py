from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import unquote, urlparse

from chat.application.algorithms.ranking import (
    FieldedDocument,
    score_fielded_bm25,
    tokenize_for_bm25,
)
from chat.application.algorithms.url import canonicalize_url, stable_hash
from chat.application.file_handoff import TemporaryFileHandoffStore
from chat.application.security.network import UrlSecurityError, validate_public_http_url
from chat.application.security.references import reject_non_url_reference
from chat.application.tool_content_store import cache_and_format
from chat.application.tools.config import TOOL_RESULT_MAX_CHARS
from chat.application.web_crawl.errors import CrawlConfigurationError, CrawlInputError
from chat.application.web_crawl.frontier import CrawlFrontier
from chat.application.web_crawl.link_extractor import (
    extract_markdown_links,
    extract_markdown_title,
)
from chat.application.web_crawl.models import (
    CrawlFrontierItem,
    CrawlItemKind,
    CrawlRequest,
    CrawlResult,
    CrawlResultItem,
    CrawlSkipReason,
    LinkCandidate,
    RankedLinkCandidate,
)
from chat.application.web_crawl.politeness import PerHostPoliteness
from chat.application.web_crawl.robots import RobotsPolicy
from chat.application.web_fetch.fetch_coordinator import FetchCoordinator, FetchResultItem
from chat.application.web_fetch.models import FetchedDocument


_BLOCKED_SCHEMES = {
    "mailto",
    "tel",
    "javascript",
    "data",
    "blob",
    "ftp",
    "chrome",
    "about",
    "file",
}

_BLOCKED_MEDIA_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".svg",
    ".ico",
    ".mp4",
    ".mp3",
    ".wav",
    ".woff",
    ".woff2",
    ".ttf",
)

_BLOCKED_PATH_PARTS = (
    "/login",
    "/signin",
    "/signup",
    "/register",
    "/logout",
    "/account",
    "/user",
    "/profile",
    "/cart",
    "/checkout",
    "/payment",
    "/billing",
    "/admin",
)

_LINK_FIELD_WEIGHTS = {
    "anchor_text": 3.0,
    "surrounding_text": 2.0,
    "source_title": 1.0,
    "url_terms": 0.7,
}

_INTERNAL_ACCEPT_THRESHOLD = 0.30
_EXTERNAL_ACCEPT_THRESHOLD = 0.45
_FETCH_WAVE_LIMIT = 3
_TOOL_RESULT_MAX_CHARS = TOOL_RESULT_MAX_CHARS


@dataclass(frozen=True, slots=True)
class _PreFetchDecision:
    allowed: bool
    item: CrawlFrontierItem
    skip_reason: Optional[str] = None
    error: Optional[str] = None

    def to_result_item(self) -> CrawlResultItem:
        return CrawlResultItem(
            url=self.item.url,
            kind=CrawlItemKind.SKIPPED.value,
            depth=self.item.depth,
            success=False,
            source_url=self.item.source_url,
            error=self.error,
            skip_reason=self.skip_reason,
        )


@dataclass(frozen=True, slots=True)
class _HandleFetchResult:
    items: List[CrawlResultItem]
    fetched_pages: int = 0
    documents_found: int = 0
    skipped_count: int = 0


@dataclass(frozen=True, slots=True)
class _DiscoveryResult:
    items: List[CrawlResultItem]
    skipped_count: int = 0


class WebCrawlService:
    def __init__(
        self,
        *,
        fetch_coordinator: FetchCoordinator,
        file_handoff_store: TemporaryFileHandoffStore,
        max_depth_hard_limit: int = 2,
        max_pages_hard_limit: int = 20,
        robots_policy: Optional[RobotsPolicy] = None,
        politeness_min_interval_seconds: float = 1.0,
    ):
        self._fetch_coordinator = fetch_coordinator
        self._file_handoff_store = file_handoff_store
        self._max_depth_hard_limit = max_depth_hard_limit
        self._max_pages_hard_limit = max_pages_hard_limit
        self._robots_policy = robots_policy
        self._politeness_min_interval_seconds = politeness_min_interval_seconds

    async def crawl(self, request: CrawlRequest) -> CrawlResult:
        self._validate_request(request)
        seed_urls = [self._normalize_and_validate_seed(url) for url in request.seed_urls]

        frontier = CrawlFrontier(
            seed_urls=seed_urls,
            max_pages=request.max_pages,
            max_depth=request.max_depth,
        )
        robots_policy = self._robots_policy or RobotsPolicy()
        politeness = PerHostPoliteness(
            min_interval_seconds=self._politeness_min_interval_seconds
        )

        items: List[CrawlResultItem] = []
        fetched_pages = 0
        documents_found = 0
        skipped_count = 0

        for depth in range(request.max_depth + 1):
            batch = frontier.pop_batch_for_depth(depth, limit=request.max_pages)
            if not batch:
                continue

            allowed_items: List[CrawlFrontierItem] = []
            for item in batch:
                decision = await self._pre_fetch_check(
                    item=item,
                    robots_policy=robots_policy,
                    politeness=politeness,
                    is_seed_url=item.depth == 0,
                )
                if decision.allowed:
                    allowed_items.append(decision.item)
                else:
                    items.append(decision.to_result_item())
                    skipped_count += 1

            while allowed_items:
                still_allowed: List[CrawlFrontierItem] = []
                for item in allowed_items:
                    if politeness.is_blocked(item.url):
                        items.append(
                            CrawlResultItem(
                                url=item.url,
                                kind=CrawlItemKind.SKIPPED.value,
                                depth=item.depth,
                                success=False,
                                source_url=item.source_url,
                                error="host blocked due to prior 429/403/503",
                                skip_reason=CrawlSkipReason.RATE_LIMITED.value,
                            )
                        )
                        skipped_count += 1
                    else:
                        still_allowed.append(item)

                if not still_allowed:
                    break

                allowed_items = still_allowed
                wave, allowed_items = _take_fetch_wave(allowed_items)
                await self._wait_politeness(wave, politeness)
                fetch_results = await self._fetch_coordinator.fetch_many(
                    [item.url for item in wave]
                )

                for frontier_item, fetch_item in zip(wave, fetch_results):
                    self._mark_blocked_if_needed(fetch_item, politeness)
                    handled = await self._handle_fetch_result(
                        request=request,
                        frontier=frontier,
                        frontier_item=frontier_item,
                        fetch_item=fetch_item,
                    )
                    items.extend(handled.items)
                    fetched_pages += handled.fetched_pages
                    documents_found += handled.documents_found
                    skipped_count += handled.skipped_count

        return CrawlResult(
            objective=request.objective,
            seed_urls=seed_urls,
            items=items,
            fetched_pages=fetched_pages,
            documents_found=documents_found,
            skipped_count=skipped_count,
            max_depth=request.max_depth,
            max_pages=request.max_pages,
            crawl_budget_exhausted=frontier.reached_page_budget() and frontier.has_pending(),
        )

    def _validate_request(self, request: CrawlRequest) -> None:
        if not request.session_id.strip():
            raise CrawlInputError("session_id is required.")
        if not request.seed_urls:
            raise CrawlInputError("seed_urls must be non-empty.")
        if len(request.objective.strip()) < 8:
            raise CrawlInputError("objective must be a specific non-empty string.")
        if request.max_depth < 1 or request.max_depth > self._max_depth_hard_limit:
            raise CrawlConfigurationError(
                f"max_depth must be between 1 and {self._max_depth_hard_limit}."
            )
        if request.max_pages < 2 or request.max_pages > self._max_pages_hard_limit:
            raise CrawlConfigurationError(
                f"max_pages must be between 2 and {self._max_pages_hard_limit}."
            )

    def _normalize_and_validate_seed(self, raw_url: str) -> str:
        reference_kind = reject_non_url_reference(raw_url)
        if reference_kind is not None:
            raise CrawlInputError(f"{reference_kind} is not a URL")

        canonical = canonicalize_url(raw_url)
        if not _is_supported_scheme(canonical):
            raise CrawlInputError("seed URL scheme must be http or https")
        if _is_blocked_path(canonical):
            raise CrawlInputError("seed URL path is blocked")
        if _is_media_url(canonical):
            raise CrawlInputError("seed URL points to unsupported media")
        return validate_public_http_url(canonical)

    async def _pre_fetch_check(
        self,
        *,
        item: CrawlFrontierItem,
        robots_policy: RobotsPolicy,
        politeness: PerHostPoliteness,
        is_seed_url: bool,
    ) -> _PreFetchDecision:
        reference_kind = reject_non_url_reference(item.url)
        if reference_kind is not None:
            return _PreFetchDecision(
                allowed=False,
                item=item,
                skip_reason=CrawlSkipReason.NON_URL_REFERENCE.value,
                error=f"{reference_kind} is not a URL",
            )

        try:
            canonical = canonicalize_url(item.url)
            blocked_reason = _hard_filter_reason(canonical)
            if blocked_reason is not None:
                return _PreFetchDecision(False, item, blocked_reason)
            validated = validate_public_http_url(canonical)
        except UrlSecurityError as exc:
            return _PreFetchDecision(
                False,
                item,
                CrawlSkipReason.URL_SECURITY_REJECTED.value,
                str(exc),
            )

        if politeness.is_blocked(validated):
            return _PreFetchDecision(
                False,
                item,
                CrawlSkipReason.RATE_LIMITED.value,
                "host blocked due to prior 429/403/503",
            )

        robots = await robots_policy.can_fetch(
            url=validated,
            is_seed_url=is_seed_url,
        )
        if not robots.allowed:
            return _PreFetchDecision(
                False,
                item,
                CrawlSkipReason.ROBOTS_UNAVAILABLE.value
                if robots.unavailable
                else CrawlSkipReason.ROBOTS_DISALLOWED.value,
                robots.reason,
            )

        return _PreFetchDecision(
            allowed=True,
            item=CrawlFrontierItem(
                url=validated,
                depth=item.depth,
                origin_host=item.origin_host,
                current_host=_host_of(validated),
                source_url=item.source_url,
                anchor_text=item.anchor_text,
                surrounding_text=item.surrounding_text,
                score=item.score,
                is_external=item.is_external,
                external_depth=item.external_depth,
            ),
        )

    async def _wait_politeness(
        self,
        items: List[CrawlFrontierItem],
        politeness: PerHostPoliteness,
    ) -> None:
        for item in items:
            await politeness.wait_turn(item.url)

    async def _handle_fetch_result(
        self,
        *,
        request: CrawlRequest,
        frontier: CrawlFrontier,
        frontier_item: CrawlFrontierItem,
        fetch_item: FetchResultItem,
    ) -> _HandleFetchResult:
        if not fetch_item.success:
            reason = _map_fetch_error_to_skip_reason(fetch_item.error)
            return _HandleFetchResult(
                items=[
                    CrawlResultItem(
                        url=fetch_item.url,
                        kind=CrawlItemKind.ERROR.value,
                        depth=frontier_item.depth,
                        success=False,
                        source_url=frontier_item.source_url,
                        error=fetch_item.error,
                        skip_reason=reason,
                    )
                ],
                skipped_count=1,
            )

        if fetch_item.document is not None:
            try:
                file_ref = self._extract_file_ref(
                    session_id=request.session_id,
                    document=fetch_item.document,
                )
            except Exception as exc:
                return _HandleFetchResult(
                    items=[
                        CrawlResultItem(
                            url=fetch_item.url,
                            kind=CrawlItemKind.ERROR.value,
                            depth=frontier_item.depth,
                            success=False,
                            source_url=frontier_item.source_url,
                            error=f"document handoff failed: {exc.__class__.__name__}",
                            skip_reason=CrawlSkipReason.FETCH_FAILED.value,
                        )
                    ],
                    skipped_count=1,
                )

            return _HandleFetchResult(
                items=[
                    CrawlResultItem(
                        url=fetch_item.url,
                        kind=CrawlItemKind.DOCUMENT.value,
                        depth=frontier_item.depth,
                        success=True,
                        source_url=frontier_item.source_url,
                        file_ref=file_ref,
                    )
                ],
                documents_found=1,
            )

        markdown = fetch_item.content or ""
        content_block = cache_and_format(
            session_id=request.session_id,
            tool_name="web_crawl",
            source=fetch_item.url,
            text=markdown,
            content_type="text/markdown",
            metadata={
                "url": fetch_item.url,
                "kind": "web_crawl_page",
                "depth": str(frontier_item.depth),
                "source_url": frontier_item.source_url or "",
            },
            limit=_TOOL_RESULT_MAX_CHARS,
        )

        result_item = CrawlResultItem(
            url=fetch_item.url,
            kind=CrawlItemKind.PAGE.value,
            depth=frontier_item.depth,
            success=True,
            source_url=frontier_item.source_url,
            content_block=content_block,
        )

        discovery = _DiscoveryResult(items=[])
        if frontier_item.depth < request.max_depth:
            discovery = self._discover_next_links(
                request=request,
                frontier=frontier,
                source_item=frontier_item,
                source_url=fetch_item.url,
                markdown=markdown,
            )

        return _HandleFetchResult(
            items=[result_item, *discovery.items],
            fetched_pages=1,
            skipped_count=discovery.skipped_count,
        )

    def _extract_file_ref(self, *, session_id: str, document: FetchedDocument) -> str:
        handoff = self._file_handoff_store.write_bytes(
            session_id=session_id,
            filename=document.filename,
            content=document.content,
            canonical_suffix=Path(document.filename).suffix,
        )
        return handoff.file_ref

    def _discover_next_links(
        self,
        *,
        request: CrawlRequest,
        frontier: CrawlFrontier,
        source_item: CrawlFrontierItem,
        source_url: str,
        markdown: str,
    ) -> _DiscoveryResult:
        extracted = extract_markdown_links(markdown)
        source_title = extract_markdown_title(markdown)
        candidates: List[LinkCandidate] = []
        skipped_items: List[CrawlResultItem] = []
        skipped_count = 0

        for link in extracted:
            reference_kind = reject_non_url_reference(link.url)
            if reference_kind is not None:
                skipped_items.append(
                    _skipped_item(
                        url=link.url,
                        source_url=source_url,
                        depth=source_item.depth + 1,
                        reason=CrawlSkipReason.NON_URL_REFERENCE.value,
                        error=f"{reference_kind} is not a URL",
                    )
                )
                skipped_count += 1
                continue

            try:
                canonical = canonicalize_url(link.url, base_url=source_url)
            except Exception as exc:
                skipped_items.append(
                    _skipped_item(
                        url=link.url,
                        source_url=source_url,
                        depth=source_item.depth + 1,
                        reason=CrawlSkipReason.URL_SECURITY_REJECTED.value,
                        error=f"URL canonicalization failed: {exc.__class__.__name__}",
                    )
                )
                skipped_count += 1
                continue

            blocked_reason = _hard_filter_reason(canonical)
            if blocked_reason is not None:
                skipped_items.append(
                    _skipped_item(
                        url=canonical,
                        source_url=source_url,
                        depth=source_item.depth + 1,
                        reason=blocked_reason,
                    )
                )
                skipped_count += 1
                continue

            try:
                validated = validate_public_http_url(canonical)
            except UrlSecurityError as exc:
                skipped_items.append(
                    _skipped_item(
                        url=canonical,
                        source_url=source_url,
                        depth=source_item.depth + 1,
                        reason=CrawlSkipReason.URL_SECURITY_REJECTED.value,
                        error=str(exc),
                    )
                )
                skipped_count += 1
                continue

            current_host = _host_of(validated)
            is_external = current_host != source_item.origin_host
            external_depth = (
                source_item.external_depth + 1
                if source_item.is_external or is_external
                else 0
            )
            candidates.append(
                LinkCandidate(
                    id=stable_hash(validated),
                    url=validated,
                    anchor_text=link.anchor_text,
                    surrounding_text=link.surrounding_text,
                    source_title=source_title,
                    source_url=source_url,
                    depth=source_item.depth + 1,
                    origin_host=source_item.origin_host,
                    current_host=current_host,
                    is_external=is_external,
                    external_depth=external_depth,
                )
            )

        ranked = _rank_link_candidates(
            objective=request.objective,
            candidates=candidates,
        )
        for item in ranked:
            if not item.accepted:
                skipped_count += 1
                continue

            frontier_item = CrawlFrontierItem(
                url=item.candidate.url,
                depth=item.candidate.depth,
                origin_host=item.candidate.origin_host,
                current_host=item.candidate.current_host,
                source_url=item.candidate.source_url,
                anchor_text=item.candidate.anchor_text,
                surrounding_text=item.candidate.surrounding_text,
                score=item.score,
                is_external=item.candidate.is_external,
                external_depth=item.candidate.external_depth,
            )
            added, reason = frontier.add_candidate(frontier_item)
            if added or reason == CrawlSkipReason.DUPLICATE_URL.value:
                continue
            skipped_items.append(
                _skipped_item(
                    url=item.candidate.url,
                    source_url=source_url,
                    depth=item.candidate.depth,
                    reason=reason or CrawlSkipReason.FETCH_FAILED.value,
                )
            )
            skipped_count += 1

        return _DiscoveryResult(items=skipped_items, skipped_count=skipped_count)

    def _mark_blocked_if_needed(
        self,
        fetch_item: FetchResultItem,
        politeness: PerHostPoliteness,
    ) -> None:
        if fetch_item.success:
            return
        error = (fetch_item.error or "").lower()
        if "429" in error or "403" in error or "503" in error or "rate limit" in error:
            politeness.mark_blocked(fetch_item.url)


def _rank_link_candidates(
    *,
    objective: str,
    candidates: List[LinkCandidate],
) -> List[RankedLinkCandidate]:
    if not candidates:
        return []

    documents = [
        FieldedDocument(
            id=candidate.id,
            fields={
                "anchor_text": candidate.anchor_text,
                "surrounding_text": candidate.surrounding_text,
                "source_title": candidate.source_title,
                "url_terms": _url_terms(candidate.url),
            },
        )
        for candidate in candidates
    ]
    raw_scores = score_fielded_bm25(objective, documents, _LINK_FIELD_WEIGHTS)
    if not any(score > 0 for score in raw_scores.values()):
        raw_scores = {
            candidate.id: _score_link_text_overlap(objective, candidate)
            for candidate in candidates
        }
    normalized_scores = _normalize_scores(raw_scores)
    by_id = {candidate.id: candidate for candidate in candidates}

    ranked: List[RankedLinkCandidate] = []
    for candidate_id, score in sorted(
        normalized_scores.items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        candidate = by_id[candidate_id]
        threshold = (
            _EXTERNAL_ACCEPT_THRESHOLD
            if candidate.is_external
            else _INTERNAL_ACCEPT_THRESHOLD
        )
        accepted = score >= threshold
        ranked.append(
            RankedLinkCandidate(
                candidate=candidate,
                score=score,
                accepted=accepted,
                reject_reason=None if accepted else CrawlSkipReason.LOW_RELEVANCE.value,
            )
        )

    if len(candidates) <= 3 and not any(item.accepted for item in ranked):
        for index, item in enumerate(ranked):
            if item.candidate.is_external:
                continue
            ranked[index] = RankedLinkCandidate(
                candidate=item.candidate,
                score=item.score,
                accepted=True,
                reject_reason=None,
            )
            break

    return ranked


def _normalize_scores(scores: Dict[str, float]) -> Dict[str, float]:
    max_score = max(scores.values()) if scores else 0.0
    if max_score <= 0:
        return {key: 0.0 for key in scores}
    return {key: value / max_score for key, value in scores.items()}


def _score_link_text_overlap(objective: str, candidate: LinkCandidate) -> float:
    query_tokens = set(tokenize_for_bm25(objective))
    if not query_tokens:
        return 0.0

    fields = {
        "anchor_text": candidate.anchor_text,
        "surrounding_text": candidate.surrounding_text,
        "source_title": candidate.source_title,
        "url_terms": _url_terms(candidate.url),
    }
    score = 0.0
    for field_name, text in fields.items():
        field_tokens = set(tokenize_for_bm25(text))
        if not field_tokens:
            continue
        score += _LINK_FIELD_WEIGHTS[field_name] * len(query_tokens & field_tokens)
    return score / max(1, len(query_tokens))


def _url_terms(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = unquote(parsed.path or "")
    text = f"{host} {path}"
    return (
        text.replace("/", " ")
        .replace("-", " ")
        .replace("_", " ")
        .replace(".", " ")
    )


def _hard_filter_reason(url: str) -> Optional[str]:
    if not _is_supported_scheme(url):
        return CrawlSkipReason.UNSUPPORTED_SCHEME.value
    if _is_blocked_path(url):
        return CrawlSkipReason.BLOCKED_PATH.value
    if _is_media_url(url):
        return CrawlSkipReason.UNSUPPORTED_MEDIA.value
    return None


def _is_supported_scheme(url: str) -> bool:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme in _BLOCKED_SCHEMES:
        return False
    return scheme in {"http", "https"}


def _is_media_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(_BLOCKED_MEDIA_EXTENSIONS)


def _is_blocked_path(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path == part or path.startswith(part + "/") for part in _BLOCKED_PATH_PARTS)


def _map_fetch_error_to_skip_reason(error: Optional[str]) -> str:
    text = (error or "").lower()
    if "captcha" in text:
        return CrawlSkipReason.CAPTCHA_DETECTED.value
    if "bot" in text or "challenge" in text:
        return CrawlSkipReason.BOT_CHALLENGE.value
    if "login" in text or "signin" in text:
        return CrawlSkipReason.LOGIN_REQUIRED.value
    if "paywall" in text:
        return CrawlSkipReason.PAYWALL_DETECTED.value
    if "permission" in text or "403" in text:
        return CrawlSkipReason.PERMISSION_DENIED.value
    if "429" in text or "rate limit" in text:
        return CrawlSkipReason.RATE_LIMITED.value
    if "javascript" in text or "js required" in text:
        return CrawlSkipReason.JS_REQUIRED.value
    if "spa" in text:
        return CrawlSkipReason.SPA_SHELL.value
    return CrawlSkipReason.FETCH_FAILED.value


def _skipped_item(
    *,
    url: str,
    source_url: str,
    depth: int,
    reason: str,
    error: Optional[str] = None,
) -> CrawlResultItem:
    return CrawlResultItem(
        url=url,
        kind=CrawlItemKind.SKIPPED.value,
        depth=depth,
        success=False,
        source_url=source_url,
        error=error,
        skip_reason=reason,
    )


def _take_fetch_wave(
    items: List[CrawlFrontierItem],
) -> Tuple[List[CrawlFrontierItem], List[CrawlFrontierItem]]:
    wave: List[CrawlFrontierItem] = []
    remaining: List[CrawlFrontierItem] = []
    hosts: Set[str] = set()

    for item in items:
        host = _host_of(item.url)
        if len(wave) < _FETCH_WAVE_LIMIT and host not in hosts:
            wave.append(item)
            hosts.add(host)
        else:
            remaining.append(item)

    return wave, remaining


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


__all__ = [
    "WebCrawlService",
    "_rank_link_candidates",
]
