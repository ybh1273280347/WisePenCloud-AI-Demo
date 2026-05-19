from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Optional

from ..confidence import compute_doi_metadata_confidence
from ..identifiers import normalize_doi
from ..models import DOIMetadataRecord
from ..resource_type import normalize_resource_type
from .common import date_parts_to_iso, first_string, strip_markup


def parse_csl_json(data: Mapping[str, Any]) -> Optional[DOIMetadataRecord]:
    doi = normalize_doi(str(data.get("DOI") or data.get("doi") or ""))
    if not doi:
        return None

    publication_date, year = date_parts_to_iso(data.get("issued"))
    record = DOIMetadataRecord(
        doi=doi,
        title=first_string(data.get("title")),
        authors=_authors(data.get("author")),
        abstract=strip_markup(first_string(data.get("abstract"))),
        year=year,
        publication_date=publication_date,
        venue=first_string(data.get("container-title")),
        publisher=first_string(data.get("publisher")),
        resource_type=normalize_resource_type(first_string(data.get("type"))),
        url=first_string(data.get("URL") or data.get("url")),
        pdf_url=None,
        source_name="Content Negotiation",
        raw_source="csl_json",
        metadata_confidence=0.0,
    )
    return replace(record, metadata_confidence=compute_doi_metadata_confidence(record))


def _authors(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        literal = first_string(item.get("literal"))
        if literal:
            authors.append(literal)
            continue
        given = first_string(item.get("given"))
        family = first_string(item.get("family"))
        name = " ".join(part for part in [given, family] if part)
        if name:
            authors.append(name)
    return authors
