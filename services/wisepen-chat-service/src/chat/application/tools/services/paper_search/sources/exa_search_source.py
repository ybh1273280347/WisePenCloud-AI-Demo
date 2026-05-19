from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional

import httpx

from chat.core.config.app_settings import settings

from ..config import EXA_DEEP_NUM_RESULTS_PER_REWRITE, EXA_FAST_NUM_RESULTS_PER_REWRITE
from ..models import PaperPointer, PaperSearchDepth, PaperSearchFreshness


class ExaSearchSource:
    name = "exa"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def search(
        self,
        *,
        query: str,
        rewrite_query: str,
        depth: PaperSearchDepth,
        freshness: PaperSearchFreshness,
    ) -> tuple[List[PaperPointer], List[str]]:
        api_key = getattr(settings, "EXA_API_KEY", None)
        if not api_key:
            return [], ["exa skipped: EXA_API_KEY is missing"]

        payload = {
            "query": rewrite_query,
            "category": "research paper",
            "type": _exa_search_type(depth),
            "numResults": _exa_num_results(depth),
            "contents": {"highlights": True},
        }

        if freshness == PaperSearchFreshness.LATEST:
            payload["startPublishedDate"] = _latest_start_date_iso()

        try:
            response = await self._client.post(
                f"{settings.EXA_BASE_URL.rstrip('/')}/search",
                headers={
                    "x-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if response.status_code in (401, 403):
                return [], ["exa search failed: authentication failed"]
            if response.status_code == 429:
                return [], ["exa search failed: quota or rate limit reached"]
            response.raise_for_status()
        except Exception as e:
            return [], [f"exa search failed: {e}"]

        data = response.json()
        raw_results = data.get("results")
        if not isinstance(raw_results, list):
            return [], ["exa response results is not a list"]

        pointers: List[PaperPointer] = []

        for rank, item in enumerate(raw_results):
            pointer = map_exa_item_to_pointer(
                item=item,
                rank=rank,
                rewrite_query=rewrite_query,
                source_name=self.name,
            )
            if pointer is not None:
                pointers.append(pointer)

        return pointers, []


def _exa_search_type(depth: PaperSearchDepth) -> str:
    if depth == PaperSearchDepth.DEEP:
        return "deep-lite"
    return "auto"


def _exa_num_results(depth: PaperSearchDepth) -> int:
    if depth == PaperSearchDepth.DEEP:
        return EXA_DEEP_NUM_RESULTS_PER_REWRITE
    return EXA_FAST_NUM_RESULTS_PER_REWRITE


def _latest_start_date_iso() -> str:
    return (date.today() - timedelta(days=365)).isoformat()


def map_exa_item_to_pointer(
    *,
    item: object,
    rank: int,
    rewrite_query: str,
    source_name: str,
) -> Optional[PaperPointer]:
    if not isinstance(item, dict):
        return None

    title = _text(item.get("title"))
    url = _text(item.get("url"))
    if not title or not url:
        return None

    highlights = item.get("highlights")
    if not isinstance(highlights, list):
        highlights = []

    return PaperPointer(
        title=title,
        url=url,
        source_name=source_name,
        rank=rank,
        rewrite_query=rewrite_query,
        pointer_type=_pointer_type(url),
        snippet=_text(item.get("text") or item.get("snippet")),
        published_date=_text(item.get("publishedDate") or item.get("published_date")),
        highlights=[h.strip() for h in highlights if isinstance(h, str) and h.strip()],
        discovery_score=1.0 / (rank + 1.0),
    )


def _text(value: object) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _pointer_type(url: str) -> str:
    lower = url.lower()
    if "arxiv.org/abs/" in lower or "arxiv.org/pdf/" in lower:
        return "arxiv"
    if lower.endswith(".pdf") or "/pdf/" in lower:
        return "pdf"
    if "doi.org/" in lower:
        return "doi"
    return "research_paper_candidate"
