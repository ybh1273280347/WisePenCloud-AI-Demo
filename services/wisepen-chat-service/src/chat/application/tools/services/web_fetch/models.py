from dataclasses import dataclass
from typing import List


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    url: str
    media_type: str
    filename: str
    content: bytes


@dataclass(frozen=True, slots=True)
class FetchedLink:
    url: str
    anchor_text: str = ""
    surrounding_text: str = ""


@dataclass(frozen=True, slots=True)
class FetchedPage:
    markdown: str
    links: List[FetchedLink]
    final_url: str = ""
    status_code: int | None = None
