from __future__ import annotations

from typing import List

import httpx

from chat.core.config.app_settings import build_tool_user_agent, settings

from ..config import ARXIV_API_MIN_INTERVAL_SECONDS
from ..identifiers import extract_arxiv_id_from_url, is_valid_arxiv_id
from ..models import PaperEntity, PaperPointer
from ..parsers.arxiv_atom_parser import parse_arxiv_atom_entries
from ..rate_limit import SourceRateGate

_arxiv_api_gate = SourceRateGate(ARXIV_API_MIN_INTERVAL_SECONDS)


class ArxivHydrator:
    name = "arxiv"

    def __init__(
        self,
        client: httpx.AsyncClient,
        gate: SourceRateGate | None = None,
    ) -> None:
        self._client = client
        self._gate = gate or _arxiv_api_gate

    async def hydrate(
        self,
        pointers: List[PaperPointer],
    ) -> tuple[List[PaperEntity], List[str]]:
        arxiv_ids = _collect_arxiv_ids(pointers)
        if not arxiv_ids:
            return [], []

        await self._gate.wait()
        try:
            response = await self._client.get(
                settings.ARXIV_API_BASE_URL,
                headers={"User-Agent": build_tool_user_agent()},
                params={"id_list": ",".join(arxiv_ids)},
            )
            response.raise_for_status()
        except Exception as e:
            return [], [f"arxiv hydration failed: {e}"]

        try:
            return parse_arxiv_atom_entries(response.text), []
        except Exception as e:
            return [], [f"arxiv hydration parse failed: {e}"]


def _collect_arxiv_ids(pointers: List[PaperPointer]) -> List[str]:
    values: List[str] = []
    for pointer in pointers:
        candidate = pointer.extracted_arxiv_id or extract_arxiv_id_from_url(pointer.url)
        if candidate and is_valid_arxiv_id(candidate) and candidate not in values:
            values.append(candidate)
    return values
