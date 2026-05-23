from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from chat.application.web_search.internal.models.helpers import normalize_optional_str


@dataclass(frozen=True, slots=True)
class ImageResult:
    """通用图片搜索结果"""

    url: str
    desc: Optional[str] = None
    source_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    resolution: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", self.url.strip())
        object.__setattr__(self, "desc", normalize_optional_str(self.desc))
        object.__setattr__(self, "source_url", normalize_optional_str(self.source_url))
        object.__setattr__(
            self, "thumbnail_url", normalize_optional_str(self.thumbnail_url)
        )
        object.__setattr__(self, "resolution", normalize_optional_str(self.resolution))


@dataclass(frozen=True, slots=True)
class SearchResult:
    """通用网页搜索结果"""

    title: str
    url: str
    snippet: str
    images: Tuple[ImageResult, ...] = field(default_factory=tuple)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", self.title.strip())
        object.__setattr__(self, "url", self.url.strip())
        object.__setattr__(self, "snippet", self.snippet.strip())
        object.__setattr__(self, "images", tuple(self.images))
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True, slots=True)
class SearchResponse:
    """通用搜索响应"""

    query: str
    results: Tuple[SearchResult, ...] = field(default_factory=tuple)
    images: Tuple[ImageResult, ...] = field(default_factory=tuple)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # searxng / tavily / wikipedia:{lang} / multi:{providers} / empty
    source: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", self.query.strip())
        object.__setattr__(self, "results", tuple(self.results))
        object.__setattr__(self, "images", tuple(self.images))
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "source", normalize_optional_str(self.source))

    def with_source(self, source: str) -> "SearchResponse":
        return SearchResponse(
            query=self.query,
            results=self.results,
            images=self.images,
            metadata=self.metadata,
            source=source,
        )
