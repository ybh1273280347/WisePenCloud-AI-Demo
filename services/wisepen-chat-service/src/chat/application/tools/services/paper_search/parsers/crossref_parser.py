from __future__ import annotations

from typing import Any, Mapping, Optional

from ..confidence import compute_doi_metadata_confidence
from ..identifiers import normalize_doi
from ..models import DOIMetadataRecord
from ..resource_type import normalize_resource_type
from .common import date_parts_to_iso, first_string, strip_markup


def parse_crossref_work(data: Mapping[str, Any]) -> Optional[DOIMetadataRecord]:
    message = data.get("message")
    if not isinstance(message, Mapping):
        return None

    doi = normalize_doi(str(message.get("DOI") or ""))
    if not doi:
        return None

    publication_date, year = _date_from_crossref(message)
    record = DOIMetadataRecord(
        doi=doi,
        title=first_string(message.get("title")),
        authors=_authors(message.get("author")),
        abstract=strip_markup(first_string(message.get("abstract"))),
        year=year,
        publication_date=publication_date,
        venue=first_string(message.get("container-title")),
        publisher=first_string(message.get("publisher")),
        resource_type=normalize_resource_type(first_string(message.get("type"))),
        url=first_string(message.get("URL")),
        pdf_url=_pdf_url(message.get("link")),
        source_name="Crossref",
        raw_source="crossref",
        metadata_confidence=0.0,
    )
    return _with_confidence(record)


def _date_from_crossref(message: Mapping[str, Any]) -> tuple[Optional[str], Optional[int]]:
    for key in ("published-print", "published-online", "published", "issued"):
        publication_date, year = date_parts_to_iso(message.get(key))
        if year is not None:
            return publication_date, year
    return None, None


def _authors(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        name = first_string(item.get("name"))
        if not name:
            given = first_string(item.get("given"))
            family = first_string(item.get("family"))
            name = " ".join(part for part in [given, family] if part)
        if name:
            authors.append(name)
    return authors


def _pdf_url(value: object) -> Optional[str]:
    if not isinstance(value, list):
        return None
    for item in value:
        if not isinstance(item, Mapping):
            continue
        url = first_string(item.get("URL"))
        content_type = first_string(item.get("content-type")) or ""
        if url and ("pdf" in content_type.lower() or url.lower().endswith(".pdf")):
            return url
    return None


def _with_confidence(record: DOIMetadataRecord) -> DOIMetadataRecord:
    from dataclasses import replace

    return replace(record, metadata_confidence=compute_doi_metadata_confidence(record))
