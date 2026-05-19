from __future__ import annotations

from typing import Dict, List, Optional

from .confidence import compute_candidate_metadata_confidence
from .identifiers import (
    extract_arxiv_id_from_arxiv_doi,
    extract_arxiv_id_from_url,
    extract_doi_from_url,
)
from .models import HydrationStatus, PaperEntity, PaperPointer, PaperResultType
from .source_confidence import compute_source_confidence
from .title_fingerprint import title_fingerprint
from .utils import stable_hash


def join_highlights(highlights: List[str]) -> Optional[str]:
    cleaned = [" ".join(item.split()).strip() for item in highlights if item.strip()]
    if not cleaned:
        return None
    return " ... ".join(cleaned)


def build_entity_from_pointer(pointer: PaperPointer) -> PaperEntity:
    external_ids: Dict[str, str] = {}

    url_doi = pointer.extracted_doi or extract_doi_from_url(pointer.url)
    if url_doi:
        arxiv_id_from_doi = extract_arxiv_id_from_arxiv_doi(url_doi)
        if arxiv_id_from_doi:
            external_ids["arxiv"] = arxiv_id_from_doi
        else:
            external_ids["doi"] = url_doi

    arxiv_id = pointer.extracted_arxiv_id or extract_arxiv_id_from_url(pointer.url)
    if arxiv_id:
        external_ids["arxiv"] = arxiv_id

    abstract = join_highlights(pointer.highlights)

    metadata_confidence = compute_candidate_metadata_confidence(
        has_doi="doi" in external_ids,
        has_published_date=bool(pointer.published_date),
        has_highlights=bool(abstract),
        has_title=bool(pointer.title),
        has_url=bool(pointer.url),
    )

    canonical_id = (
        f"arxiv:{external_ids['arxiv']}"
        if "arxiv" in external_ids
        else f"doi:{external_ids['doi']}"
        if "doi" in external_ids
        else f"url:{stable_hash(pointer.url)}"
    )

    return PaperEntity(
        canonical_id=canonical_id,
        title=pointer.title,
        abstract=abstract,
        abstract_source="exa_highlights" if abstract else None,
        publication_date=pointer.published_date,
        url=pointer.url,
        external_ids=external_ids,
        source_urls=[pointer.url],
        evidence_sources=[pointer.source_name],
        hydration_status=HydrationStatus.DISCOVERED_ONLY,
        result_type=PaperResultType.RESEARCH_PAPER_CANDIDATE,
        metadata_confidence=metadata_confidence,
        source_confidence=compute_source_confidence(pointer.url),
        discovery_score=pointer.discovery_score,
    )


def pointer_title_fingerprint(pointer: PaperPointer) -> str:
    return pointer.title_fingerprint or title_fingerprint(pointer.title)
