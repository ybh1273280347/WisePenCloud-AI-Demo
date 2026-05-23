from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class CrawlItemKind(str, Enum):
    PAGE = "page"
    DOCUMENT = "document"
    SKIPPED = "skipped"
    ERROR = "error"


class CrawlSkipReason(str, Enum):
    NON_URL_REFERENCE = "non_url_reference"
    URL_SECURITY_REJECTED = "url_security_rejected"
    ROBOTS_DISALLOWED = "robots_disallowed"
    ROBOTS_UNAVAILABLE = "robots_unavailable"
    DEPTH_LIMIT = "depth_limit"
    PAGE_LIMIT = "page_limit"
    EXTERNAL_BUDGET_EXCEEDED = "external_budget_exceeded"
    EXTERNAL_HOST_BUDGET_EXCEEDED = "external_host_budget_exceeded"
    EXTERNAL_DEPTH_LIMIT = "external_depth_limit"
    UNSUPPORTED_SCHEME = "unsupported_scheme"
    UNSUPPORTED_MEDIA = "unsupported_media"
    BLOCKED_PATH = "blocked_path"
    LOGIN_REQUIRED = "login_required"
    PERMISSION_DENIED = "permission_denied"
    PAYWALL_DETECTED = "paywall_detected"
    CAPTCHA_DETECTED = "captcha_detected"
    BOT_CHALLENGE = "bot_challenge"
    JS_REQUIRED = "js_required"
    SPA_SHELL = "spa_shell"
    RATE_LIMITED = "rate_limited"
    LOW_RELEVANCE = "low_relevance"
    DUPLICATE_URL = "duplicate_url"
    FETCH_FAILED = "fetch_failed"


@dataclass(frozen=True, slots=True)
class CrawlRequest:
    user_id: str
    session_id: str
    seed_urls: List[str]
    objective: str
    max_depth: int = 1
    max_pages: int = 8


@dataclass(frozen=True, slots=True)
class CrawlFrontierItem:
    url: str
    depth: int
    origin_host: str
    current_host: str
    source_url: Optional[str] = None
    anchor_text: Optional[str] = None
    surrounding_text: Optional[str] = None
    score: float = 0.0
    is_external: bool = False
    external_depth: int = 0


@dataclass(frozen=True, slots=True)
class ExtractedLink:
    url: str
    anchor_text: str
    surrounding_text: str
    source: str = "markdown"


@dataclass(frozen=True, slots=True)
class LinkCandidate:
    id: str
    url: str
    anchor_text: str
    surrounding_text: str
    source_title: str
    source_url: str
    depth: int
    origin_host: str
    current_host: str
    is_external: bool
    external_depth: int


@dataclass(frozen=True, slots=True)
class RankedLinkCandidate:
    candidate: LinkCandidate
    score: float
    accepted: bool
    reject_reason: Optional[str] = None


@dataclass(frozen=True, slots=True)
class CrawlResultItem:
    url: str
    kind: str
    depth: int
    success: bool
    content_block: Optional[str] = None
    file_ref: Optional[str] = None
    source_url: Optional[str] = None
    error: Optional[str] = None
    skip_reason: Optional[str] = None


@dataclass(frozen=True, slots=True)
class CrawlResult:
    objective: str
    seed_urls: List[str]
    items: List[CrawlResultItem]
    fetched_pages: int
    documents_found: int
    skipped_count: int
    max_depth: int
    max_pages: int
    crawl_budget_exhausted: bool = False
