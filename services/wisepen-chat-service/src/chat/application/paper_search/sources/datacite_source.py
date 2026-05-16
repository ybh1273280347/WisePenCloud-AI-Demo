from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote

import httpx

from chat.application.paper_search.http import get_json_with_retry
from chat.application.paper_search.models import PaperSearchResult, PaperSourceResponse
from chat.application.paper_search.rate_limit import SourceRateGate
from chat.core.config.app_settings import settings


class DataCiteSource:
    name = "datacite"

    def __init__(self, client: httpx.AsyncClient, gate: SourceRateGate) -> None:
        self._client = client
        self._gate = gate

    async def search(self, query: str, *, rows: int) -> PaperSourceResponse:
        await self._gate.wait()
        doi = _extract_doi(query)
        if doi:
            return await self._get_by_doi(doi)

        params = {
            "query": query,
            "page[size]": min(rows, 5),
        }
        try:
            data = await get_json_with_retry(
                self._client, f"{settings.DATACITE_BASE_URL}/dois", params=params
            )
        except Exception as e:
            return PaperSourceResponse(self.name, [], [f"datacite failed: {e}"], failed=True)

        items = data.get("data", [])
        if not isinstance(items, list):
            items = []
        return PaperSourceResponse(
            source_name=self.name,
            results=[_map_datacite_item(item) for item in items if isinstance(item, dict)],
        )

    async def _get_by_doi(self, doi: str) -> PaperSourceResponse:
        try:
            data = await get_json_with_retry(
                self._client,
                f"{settings.DATACITE_BASE_URL}/dois/{quote(doi, safe='')}",
                params={},
            )
        except Exception as e:
            return PaperSourceResponse(self.name, [], [f"datacite failed: {e}"], failed=True)

        item = data.get("data")
        results = [_map_datacite_item(item)] if isinstance(item, dict) else []
        return PaperSourceResponse(source_name=self.name, results=results)


def _map_datacite_item(item: dict) -> PaperSearchResult:
    attrs = item.get("attributes") or {}
    if not isinstance(attrs, dict):
        attrs = {}

    title = ""
    titles = attrs.get("titles") or []
    if titles and isinstance(titles[0], dict):
        title = titles[0].get("title") or ""

    descriptions = attrs.get("descriptions") or []
    abstract = None
    if descriptions and isinstance(descriptions[0], dict):
        abstract = descriptions[0].get("description")

    doi = attrs.get("doi")
    url = attrs.get("url")
    types = attrs.get("types") or {}
    result_type = types.get("resourceTypeGeneral") if isinstance(types, dict) else None

    return PaperSearchResult(
        title=title,
        authors=[
            creator.get("name")
            for creator in attrs.get("creators") or []
            if isinstance(creator, dict) and creator.get("name")
        ],
        year=_safe_int(attrs.get("publicationYear")),
        abstract=abstract,
        doi=doi.lower() if isinstance(doi, str) and doi else None,
        url=url if isinstance(url, str) else None,
        source_urls=[url] if isinstance(url, str) and url else [],
        source_names=["datacite"],
        result_type=result_type or "research_object",
        authority_score=0.75 if doi else 0.5,
        relevance_score=0.6,
    )


def _safe_int(value: object) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_doi(value: str) -> str | None:
    match = re.search(r"\b10\.\d{4,9}/\S+\b", value.strip(), re.IGNORECASE)
    if not match:
        return None
    return match.group(0).rstrip(".,;)")
