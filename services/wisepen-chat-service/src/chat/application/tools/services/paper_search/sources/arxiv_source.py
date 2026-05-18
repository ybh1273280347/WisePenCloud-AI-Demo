from __future__ import annotations

import asyncio
import re
import threading
from typing import List

import arxiv

from chat.application.tools.services.paper_search.config import ARXIV_CLIENT_REQUEST_TIMEOUT_SECONDS, ARXIV_MAX_RESULTS, ARXIV_MIN_INTERVAL_SECONDS
from chat.application.tools.services.paper_search.models import PaperSearchResult, PaperSourceResponse


_ARXIV_LOCK = threading.Lock()


class ArxivSource:
    name = "arxiv"

    def __init__(self) -> None:
        self._client = arxiv.Client(
            page_size=ARXIV_MAX_RESULTS,
            delay_seconds=ARXIV_MIN_INTERVAL_SECONDS,
            num_retries=1,
        )
        _install_default_request_timeout(self._client)

    async def search(self, query: str, *, rows: int) -> PaperSourceResponse:
        try:
            results = await asyncio.to_thread(self._search_sync, query, rows)
        except Exception as e:
            return PaperSourceResponse(self.name, [], [f"arxiv failed: {e}"], failed=True)

        return PaperSourceResponse(source_name=self.name, results=results)

    def _search_sync(self, query: str, rows: int) -> List[PaperSearchResult]:
        arxiv_id = _extract_arxiv_id(query)
        search = arxiv.Search(
            query="" if arxiv_id else f"all:{query}",
            id_list=[arxiv_id] if arxiv_id else [],
            max_results=min(rows, ARXIV_MAX_RESULTS),
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        results: List[PaperSearchResult] = []
        with _ARXIV_LOCK:
            for item in self._client.results(search):
                published = item.published
                entry_id = item.entry_id
                pdf_url = item.pdf_url
                results.append(
                    PaperSearchResult(
                        title=item.title or "",
                        authors=[author.name for author in item.authors],
                        year=published.year if published else None,
                        abstract=item.summary,
                        arxiv_id=entry_id.rsplit("/", 1)[-1] if entry_id else None,
                        url=entry_id,
                        pdf_url=pdf_url,
                        source_urls=[url for url in [entry_id, pdf_url] if url],
                        source_names=["arxiv"],
                        publication_date=published.date().isoformat() if published else None,
                        result_type="preprint",
                        authority_score=0.55,
                        relevance_score=0.7,
                    )
                )
        return results


def _install_default_request_timeout(client: arxiv.Client) -> None:
    session = getattr(client, "_session", None)
    if session is None or getattr(session, "_wisepen_arxiv_timeout_installed", False):
        return

    original_get = session.get

    def get_with_timeout(url, **kwargs):
        kwargs.setdefault("timeout", ARXIV_CLIENT_REQUEST_TIMEOUT_SECONDS)
        return original_get(url, **kwargs)

    session.get = get_with_timeout
    session._wisepen_arxiv_timeout_installed = True


def _extract_arxiv_id(value: str) -> str | None:
    text = value.strip()
    match = re.search(r"(?:arxiv:\s*)?(\d{4}\.\d{4,5}(?:v\d+)?)\b", text, re.IGNORECASE)
    if match:
        return match.group(1)
    old_match = re.search(r"(?:arxiv:\s*)?([a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)\b", text, re.IGNORECASE)
    return old_match.group(1) if old_match else None
