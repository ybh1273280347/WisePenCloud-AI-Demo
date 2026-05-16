from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True, slots=True)
class PaperSearchRequest:
    query: str
    max_results: int = 8


@dataclass(frozen=True, slots=True)
class PaperSearchResult:
    title: str
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    abstract: Optional[str] = None
    venue: Optional[str] = None
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    url: Optional[str] = None
    pdf_url: Optional[str] = None
    source_urls: List[str] = field(default_factory=list)
    source_names: List[str] = field(default_factory=list)
    publication_date: Optional[str] = None
    is_open_access: Optional[bool] = None
    result_type: Optional[str] = None
    relevance_score: float = 0.0
    authority_score: float = 0.0


@dataclass(frozen=True, slots=True)
class PaperSourceResponse:
    source_name: str
    results: List[PaperSearchResult]
    warnings: List[str] = field(default_factory=list)
    failed: bool = False


@dataclass(frozen=True, slots=True)
class PaperSearchResponse:
    query: str
    results: List[PaperSearchResult]
    searched_sources: List[str]
    skipped_sources: List[str]
    failed_sources: List[str]
    warnings: List[str]
