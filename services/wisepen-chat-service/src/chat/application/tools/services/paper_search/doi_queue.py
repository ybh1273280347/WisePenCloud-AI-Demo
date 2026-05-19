from __future__ import annotations

from typing import List

from .config import DOI_HYDRATION_LIMIT_DEEP, DOI_HYDRATION_LIMIT_FAST
from .identifiers import extract_arxiv_id_from_arxiv_doi
from .models import PaperEntity, PaperSearchDepth


def collect_dois_for_hydration(entities: List[PaperEntity]) -> List[str]:
    dois: List[str] = []

    for entity in entities:
        doi = entity.external_ids.get("doi")
        if not doi:
            continue

        if extract_arxiv_id_from_arxiv_doi(doi):
            continue

        already_doi_hydrated = any(
            source.startswith("doi:") for source in entity.hydration_sources
        )
        if already_doi_hydrated:
            continue

        dois.append(doi)

    return sorted(set(dois))


def select_dois_for_hydration(
    *,
    entities: List[PaperEntity],
    dois: List[str],
    limit: int,
) -> List[str]:
    by_doi: dict[str, float] = {}
    allowed = set(dois)

    for entity in entities:
        doi = entity.external_ids.get("doi")
        if doi not in allowed:
            continue
        by_doi[doi] = max(by_doi.get(doi, 0.0), entity.relevance_score + entity.discovery_score)

    ranked = sorted(dois, key=lambda doi: (-by_doi.get(doi, 0.0), doi))
    return ranked[:limit]


def doi_hydration_limit(depth: PaperSearchDepth) -> int:
    if depth == PaperSearchDepth.DEEP:
        return DOI_HYDRATION_LIMIT_DEEP
    return DOI_HYDRATION_LIMIT_FAST
