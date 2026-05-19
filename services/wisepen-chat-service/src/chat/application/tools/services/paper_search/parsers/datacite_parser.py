from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Optional

from ..confidence import compute_doi_metadata_confidence
from ..identifiers import normalize_doi
from ..models import DOIMetadataRecord
from ..resource_type import normalize_resource_type
from .common import first_string, strip_markup


def parse_datacite_doi(data: Mapping[str, Any]) -> Optional[DOIMetadataRecord]:
    item = data.get("data")
    if not isinstance(item, Mapping):
        return None
    attributes = item.get("attributes")
    if not isinstance(attributes, Mapping):
        return None

    doi = normalize_doi(str(attributes.get("doi") or ""))
    if not doi:
        return None

    year = _year(attributes.get("publicationYear"))
    record = DOIMetadataRecord(
        doi=doi,
        title=_title(attributes.get("titles")),
        authors=_creators(attributes.get("creators")),
        abstract=_description(attributes.get("descriptions")),
        year=year,
        publication_date=str(year) if year else None,
        venue=None,
        publisher=first_string(attributes.get("publisher")),
        resource_type=normalize_resource_type(_resource_type(attributes.get("types"))),
        url=first_string(attributes.get("url")),
        pdf_url=None,
        source_name="DataCite",
        raw_source="datacite",
        metadata_confidence=0.0,
    )
    return replace(record, metadata_confidence=compute_doi_metadata_confidence(record))


def _title(value: object) -> Optional[str]:
    if not isinstance(value, list) or not value:
        return None
    first = value[0]
    if isinstance(first, Mapping):
        return first_string(first.get("title"))
    return None


def _creators(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        name = first_string(item.get("name"))
        if name:
            authors.append(name)
    return authors


def _description(value: object) -> Optional[str]:
    if not isinstance(value, list):
        return None
    for item in value:
        if isinstance(item, Mapping):
            text = strip_markup(first_string(item.get("description")))
            if text:
                return text
    return None


def _resource_type(value: object) -> Optional[str]:
    if isinstance(value, Mapping):
        return first_string(value.get("resourceTypeGeneral")) or first_string(value.get("resourceType"))
    return None


def _year(value: object) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
