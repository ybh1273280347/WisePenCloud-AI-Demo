from __future__ import annotations

from typing import List, Optional
from xml.etree import ElementTree
from xml.etree.ElementTree import Element

from ..identifiers import normalize_doi
from ..models import (
    HydrationStatus,
    PaperEntity,
    PaperResultType,
    ScholarlyResourceType,
    WorkVersionRef,
    WorkVersionType,
)
from ..source_confidence import compute_source_confidence

ARXIV_NS = "{http://arxiv.org/schemas/atom}"
ATOM_NS = "{http://www.w3.org/2005/Atom}"


def parse_arxiv_atom_entries(xml_text: str) -> List[PaperEntity]:
    root = ElementTree.fromstring(xml_text)
    return [
        entity
        for entry in root.findall(f"{ATOM_NS}entry")
        for entity in [_parse_entry(entry)]
        if entity is not None
    ]


def extract_arxiv_doi(entry: Element) -> Optional[str]:
    doi_element = entry.find(f"{ARXIV_NS}doi")
    if doi_element is not None and doi_element.text and doi_element.text.strip():
        return doi_element.text.strip()

    for link in entry.findall(f"{ATOM_NS}link"):
        title = link.attrib.get("title")
        href = link.attrib.get("href")
        if title == "doi" and href:
            return href

    return None


def _parse_entry(entry: Element) -> Optional[PaperEntity]:
    entry_url = _text(entry.find(f"{ATOM_NS}id"))
    if not entry_url:
        return None
    arxiv_id = entry_url.rstrip("/").rsplit("/", 1)[-1]
    if not arxiv_id:
        return None

    title = _text(entry.find(f"{ATOM_NS}title"))
    if not title:
        return None

    doi = normalize_doi(extract_arxiv_doi(entry) or "")
    external_ids = {"arxiv": arxiv_id}
    if doi:
        external_ids["doi"] = doi

    pdf_url = _pdf_url(entry) or _pdf_url_from_abs_url(entry_url)
    authors = [
        name
        for author in entry.findall(f"{ATOM_NS}author")
        for name in [_text(author.find(f"{ATOM_NS}name"))]
        if name
    ]

    return PaperEntity(
        canonical_id=f"arxiv:{arxiv_id}",
        title=title,
        authors=authors,
        abstract=_text(entry.find(f"{ATOM_NS}summary")),
        abstract_source="arxiv",
        year=_year_from_iso(_text(entry.find(f"{ATOM_NS}published"))),
        publication_date=_text(entry.find(f"{ATOM_NS}published")),
        url=entry_url,
        pdf_url=pdf_url,
        external_ids=external_ids,
        source_urls=[url for url in [entry_url, pdf_url] if url],
        evidence_sources=["arxiv"],
        hydration_sources=["arxiv"],
        hydration_status=HydrationStatus.HYDRATED,
        result_type=PaperResultType.PAPER,
        resource_type=ScholarlyResourceType.PREPRINT,
        versions=[
            WorkVersionRef(
                source="arxiv",
                external_id=arxiv_id,
                url=entry_url,
                version_type=WorkVersionType.PREPRINT,
            )
        ],
        preferred_version=f"arxiv:{arxiv_id}",
        authoritative_version=f"doi:{doi}" if doi else f"arxiv:{arxiv_id}",
        metadata_confidence=0.92,
        source_confidence=compute_source_confidence(entry_url),
        discovery_score=0.0,
    )


def _pdf_url(entry: Element) -> Optional[str]:
    for link in entry.findall(f"{ATOM_NS}link"):
        if link.attrib.get("title") == "pdf" and link.attrib.get("href"):
            return link.attrib["href"]
    return None


def _text(element: Element | None) -> Optional[str]:
    if element is None or not element.text:
        return None
    text = " ".join(element.text.split()).strip()
    return text or None


def _year_from_iso(value: Optional[str]) -> Optional[int]:
    if not value or len(value) < 4:
        return None
    try:
        return int(value[:4])
    except ValueError:
        return None


def _pdf_url_from_abs_url(abs_url: str) -> str:
    if "/abs/" in abs_url:
        return abs_url.replace("/abs/", "/pdf/", 1)
    return abs_url.rstrip("/") + ".pdf"
