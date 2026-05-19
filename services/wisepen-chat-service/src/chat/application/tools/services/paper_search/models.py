from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class PaperSearchFreshness(str, Enum):
    LATEST = "latest"
    BALANCED = "balanced"
    STABLE = "stable"


class PaperSearchDepth(str, Enum):
    FAST = "fast"
    DEEP = "deep"


class HydrationStatus(str, Enum):
    HYDRATED = "hydrated"
    DISCOVERED_ONLY = "discovered_only"
    FAILED = "failed"


class PaperResultType(str, Enum):
    PAPER = "paper"
    RESEARCH_PAPER_CANDIDATE = "research_paper_candidate"
    SCHOLARLY_RESOURCE = "scholarly_resource"


class WorkVersionType(str, Enum):
    PREPRINT = "preprint"
    PUBLISHED = "published"
    ACCEPTED_MANUSCRIPT = "accepted_manuscript"
    UNKNOWN = "unknown"


class ScholarlyResourceType(str, Enum):
    JOURNAL_ARTICLE = "journal_article"
    PROCEEDINGS_ARTICLE = "proceedings_article"
    PREPRINT = "preprint"
    BOOK_CHAPTER = "book_chapter"
    DATASET = "dataset"
    SOFTWARE = "software"
    REPORT = "report"
    THESIS = "thesis"
    UNKNOWN = "unknown"


class DOIExtractionConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"


@dataclass(frozen=True, slots=True)
class PaperSearchRequest:
    query: str
    max_results: int = 8
    freshness: PaperSearchFreshness = PaperSearchFreshness.BALANCED
    depth: PaperSearchDepth = PaperSearchDepth.DEEP
    query_variants: List[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PaperPointer:
    title: str
    url: str
    source_name: str
    rank: int
    rewrite_query: str
    pointer_type: str

    snippet: Optional[str] = None
    published_date: Optional[str] = None
    highlights: List[str] = field(default_factory=list)

    extracted_arxiv_id: Optional[str] = None
    extracted_doi: Optional[str] = None
    extracted_openreview_id: Optional[str] = None
    title_fingerprint: Optional[str] = None

    discovery_score: float = 0.0


@dataclass(frozen=True, slots=True)
class ExtractedDOI:
    doi: str
    source: str
    confidence: DOIExtractionConfidence


@dataclass(frozen=True, slots=True)
class WorkVersionRef:
    source: str
    external_id: str
    url: Optional[str]
    version_type: WorkVersionType


@dataclass(frozen=True, slots=True)
class PaperEntity:
    canonical_id: str
    title: str

    authors: List[str] = field(default_factory=list)
    abstract: Optional[str] = None
    abstract_source: Optional[str] = None

    year: Optional[int] = None
    publication_date: Optional[str] = None
    venue: Optional[str] = None
    publisher: Optional[str] = None

    url: Optional[str] = None
    pdf_url: Optional[str] = None

    external_ids: Dict[str, str] = field(default_factory=dict)
    source_urls: List[str] = field(default_factory=list)
    evidence_sources: List[str] = field(default_factory=list)

    hydration_sources: List[str] = field(default_factory=list)
    failed_hydration_sources: List[str] = field(default_factory=list)
    hydration_error_codes: List[str] = field(default_factory=list)

    result_type: PaperResultType = PaperResultType.RESEARCH_PAPER_CANDIDATE
    resource_type: ScholarlyResourceType = ScholarlyResourceType.UNKNOWN
    hydration_status: HydrationStatus = HydrationStatus.DISCOVERED_ONLY

    versions: List[WorkVersionRef] = field(default_factory=list)
    preferred_version: Optional[str] = None
    authoritative_version: Optional[str] = None

    metadata_confidence: float = 0.0
    source_confidence: float = 0.0
    discovery_score: float = 0.0
    relevance_score: float = 0.0
    recency_score: float = 0.0


@dataclass(frozen=True, slots=True)
class DOIMetadataRecord:
    doi: str
    title: Optional[str]
    authors: List[str]
    abstract: Optional[str]
    year: Optional[int]
    publication_date: Optional[str]
    venue: Optional[str]
    publisher: Optional[str]
    resource_type: ScholarlyResourceType
    url: Optional[str]
    pdf_url: Optional[str]
    source_name: str
    raw_source: str
    metadata_confidence: float


@dataclass(frozen=True, slots=True)
class ArxivDeltaRecord:
    arxiv_id: str
    title: str
    abstract: Optional[str]
    authors: List[str]
    published_date: Optional[str]
    updated_date: Optional[str]
    categories: List[str]
    abs_url: str
    pdf_url: str
    source_feed: str


@dataclass(frozen=True, slots=True)
class PaperSearchResponse:
    query: str
    results: List[PaperEntity]
    searched_sources: List[str]
    skipped_sources: List[str]
    failed_sources: List[str]
    warnings: List[str]
