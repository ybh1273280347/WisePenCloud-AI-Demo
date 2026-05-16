from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from chat.application.web_search.models import SearchResponse


@dataclass(frozen=True, slots=True)
class QueryVariant:
    id: str
    text: str
    role: str
    language: Optional[str]
    engines: Optional[Tuple[str, ...]]
    serial: bool
    max_results: int
    weight: float


@dataclass(frozen=True, slots=True)
class WikipediaKeyword:
    text: str
    language: Optional[str] = None
    role: str = "grounding"


@dataclass(frozen=True, slots=True)
class SearchPlan:
    mode: str
    query_variants: Tuple[QueryVariant, ...]
    wikipedia_keywords: Tuple[WikipediaKeyword, ...]


@dataclass(frozen=True, slots=True)
class VariantSearchResponse:
    variant: QueryVariant
    response: SearchResponse
    cache_hit: bool = False


@dataclass(frozen=True, slots=True)
class WikipediaGroundingResult:
    keyword: WikipediaKeyword
    title: str
    extract: str
    url: str
    language: str
    cache_hit: bool = False
