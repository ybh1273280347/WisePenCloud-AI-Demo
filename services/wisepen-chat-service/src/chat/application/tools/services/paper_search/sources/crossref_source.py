from __future__ import annotations

import re
from typing import Dict, List, Optional
from urllib.parse import quote

import httpx
from chat.application.tools.services.paper_search.http import get_json_with_retry
from chat.application.tools.services.paper_search.models import PaperSearchResult, PaperSourceResponse
from chat.application.tools.services.paper_search.rate_limit import SourceRateGate
from chat.core.config.app_settings import settings


class CrossrefSource:
    name = "crossref"

    def __init__(self, client: httpx.AsyncClient, gate: SourceRateGate) -> None:
        self._client = client
        self._gate = gate

    async def search(self, query: str, *, rows: int) -> PaperSourceResponse:
        await self._gate.wait()
        doi = _extract_doi(query)
        if doi:
            return await self._get_by_doi(doi)

        params = {
            "query.bibliographic": query,
            "rows": min(rows, 5),
            "select": "DOI,title,author,issued,container-title,URL,abstract,published-print,published-online",
        }
        if settings.TOOL_CONTACT_EMAIL:
            params["mailto"] = settings.TOOL_CONTACT_EMAIL

        try:
            data = await get_json_with_retry(
                self._client, f"{settings.CROSSREF_BASE_URL}/works", params=params
            )
        except Exception as e:
            return PaperSourceResponse(
                self.name, [], [f"crossref failed: {e}"], failed=True
            )

        items = data.get("message", {}).get("items", [])
        if not isinstance(items, list):
            items = []
        return PaperSourceResponse(
            source_name=self.name,
            results=[
                _map_crossref_item(item) for item in items if isinstance(item, dict)
            ],
        )

    async def _get_by_doi(self, doi: str) -> PaperSourceResponse:
        try:
            data = await get_json_with_retry(
                self._client,
                f"{settings.CROSSREF_BASE_URL}/works/{quote(doi, safe='')}",
                params={},
            )
        except Exception as e:
            return PaperSourceResponse(
                self.name, [], [f"crossref failed: {e}"], failed=True
            )

        item = data.get("message")
        results = [_map_crossref_item(item)] if isinstance(item, dict) else []
        return PaperSourceResponse(source_name=self.name, results=results)


def _map_crossref_item(item: dict) -> PaperSearchResult:
    title = _first(item.get("title")) or ""
    venue = _first(item.get("container-title"))
    doi = item.get("DOI")
    url = item.get("URL")
    return PaperSearchResult(
        title=title,
        authors=_map_crossref_authors(item.get("author") or []),
        year=_extract_crossref_year(item),
        abstract=_strip_crossref_markup(item.get("abstract")),
        venue=venue,
        doi=doi.lower() if isinstance(doi, str) and doi else None,
        url=url if isinstance(url, str) else None,
        source_urls=[url] if isinstance(url, str) and url else [],
        source_names=["crossref"],
        result_type="publisher_metadata",
        authority_score=0.9 if doi else 0.6,
        relevance_score=0.7,
    )


def _first(value: object) -> Optional[str]:
    if isinstance(value, list) and value:
        return str(value[0]) if value[0] else None
    if isinstance(value, str):
        return value
    return None


def _extract_crossref_year(item: dict) -> Optional[int]:
    for key in ("issued", "published-print", "published-online"):
        container = item.get(key)
        if not isinstance(container, dict):
            continue
        date_parts = container.get("date-parts")
        if date_parts and date_parts[0]:
            try:
                return int(date_parts[0][0])
            except (TypeError, ValueError):
                return None
    return None


def _map_crossref_authors(authors: List[Dict]) -> List[str]:
    result: List[str] = []
    for author in authors:
        if not isinstance(author, dict):
            continue
        name = " ".join(
            part for part in [author.get("given"), author.get("family")] if part
        )
        if name:
            result.append(name)
    return result


def _strip_crossref_markup(value: object) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    return re.sub(r"<[^>]+>", " ", value)


def _extract_doi(value: str) -> Optional[str]:
    match = re.search(r"\b10\.\d{4,9}/\S+\b", value.strip(), re.IGNORECASE)
    if not match:
        return None
    return match.group(0).rstrip(".,;)")
