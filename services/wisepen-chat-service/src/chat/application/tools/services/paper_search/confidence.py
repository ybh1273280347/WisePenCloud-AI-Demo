from __future__ import annotations

from .models import DOIMetadataRecord


def compute_doi_metadata_confidence(record: DOIMetadataRecord) -> float:
    has_title = bool(record.title)
    has_authors = bool(record.authors)
    has_date = bool(record.publication_date or record.year)
    has_venue = bool(record.venue)
    has_abstract = bool(record.abstract)

    major_count = sum([has_title, has_authors, has_date, has_venue])

    if major_count == 4 and has_abstract:
        return 0.92

    if major_count >= 3:
        return 0.86

    if has_title and (has_date or record.publisher):
        return 0.78

    return 0.70


def compute_candidate_metadata_confidence(
    *,
    has_doi: bool,
    has_published_date: bool,
    has_highlights: bool,
    has_title: bool,
    has_url: bool,
) -> float:
    if has_doi and has_published_date and has_highlights:
        return 0.75

    if has_doi:
        return 0.70

    if has_published_date and has_highlights:
        return 0.58

    if has_title and has_url and has_highlights:
        return 0.48

    if has_title and has_url:
        return 0.32

    return 0.20
