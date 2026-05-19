from __future__ import annotations

from dataclasses import replace
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional

from .models import (
    DOIMetadataRecord,
    HydrationStatus,
    PaperEntity,
    ScholarlyResourceType,
    WorkVersionRef,
    WorkVersionType,
)
from .resource_type import result_type_from_resource
from .source_confidence import compute_source_confidence
from .title_fingerprint import title_fingerprint
from .utils import canonicalize_url, merge_unique


def fuse_entities(entities: Iterable[PaperEntity]) -> List[PaperEntity]:
    fused: List[PaperEntity] = []

    for entity in entities:
        match_index = _find_match_index(fused, entity)
        if match_index is None:
            fused.append(_finalize_versions(entity))
            continue
        fused[match_index] = _merge_entities(fused[match_index], entity)

    return [_finalize_versions(entity) for entity in fused]


def merge_doi_records_into_entities(
    *,
    entities: List[PaperEntity],
    records: Dict[str, DOIMetadataRecord],
    failures: Dict[str, str],
) -> List[PaperEntity]:
    merged: List[PaperEntity] = []
    for entity in entities:
        doi = entity.external_ids.get("doi")
        if doi and doi in records:
            merged.append(merge_doi_record(entity, records[doi]))
            continue
        if doi and doi in failures:
            merged.append(
                replace(
                    entity,
                    hydration_status=(
                        HydrationStatus.FAILED
                        if entity.hydration_status == HydrationStatus.DISCOVERED_ONLY
                        else entity.hydration_status
                    ),
                    failed_hydration_sources=merge_unique(
                        entity.failed_hydration_sources,
                        [f"doi:{doi}"],
                    ),
                    hydration_error_codes=merge_unique(
                        entity.hydration_error_codes,
                        [failures[doi]],
                    ),
                )
            )
            continue
        merged.append(entity)
    return merged


def merge_doi_record(entity: PaperEntity, record: DOIMetadataRecord) -> PaperEntity:
    hydration_source = f"doi:{record.source_name}"

    external_ids = dict(entity.external_ids)
    external_ids["doi"] = record.doi

    abstract = entity.abstract
    abstract_source = entity.abstract_source

    if not abstract and record.abstract:
        abstract = record.abstract
        abstract_source = hydration_source

    updated = replace(
        entity,
        title=record.title or entity.title,
        authors=record.authors or entity.authors,
        abstract=abstract,
        abstract_source=abstract_source,
        year=record.year or entity.year,
        publication_date=record.publication_date or entity.publication_date,
        venue=record.venue or entity.venue,
        publisher=record.publisher or entity.publisher,
        url=_preferred_url(entity, record),
        pdf_url=entity.pdf_url or record.pdf_url,
        external_ids=external_ids,
        source_urls=merge_unique(
            entity.source_urls,
            [url for url in [record.url, record.pdf_url] if url],
        ),
        hydration_sources=merge_unique(entity.hydration_sources, [hydration_source]),
        hydration_status=HydrationStatus.HYDRATED,
        resource_type=record.resource_type,
        result_type=result_type_from_resource(record.resource_type),
        versions=merge_versions(entity.versions, [version_ref_from_doi(record)]),
        metadata_confidence=max(entity.metadata_confidence, record.metadata_confidence),
        source_confidence=compute_source_confidence(record.url or entity.url or ""),
    )

    return _finalize_versions(updated)


def compute_preferred_version(entity: PaperEntity) -> Optional[str]:
    arxiv_id = entity.external_ids.get("arxiv")
    if arxiv_id:
        return f"arxiv:{arxiv_id}"

    doi = entity.external_ids.get("doi")
    if doi:
        return f"doi:{doi}"

    return None


def compute_authoritative_version(entity: PaperEntity) -> Optional[str]:
    doi = entity.external_ids.get("doi")
    if doi:
        return f"doi:{doi}"

    arxiv_id = entity.external_ids.get("arxiv")
    if arxiv_id:
        return f"arxiv:{arxiv_id}"

    return None


def version_ref_from_doi(record: DOIMetadataRecord) -> WorkVersionRef:
    if record.resource_type in {
        ScholarlyResourceType.JOURNAL_ARTICLE,
        ScholarlyResourceType.PROCEEDINGS_ARTICLE,
        ScholarlyResourceType.BOOK_CHAPTER,
    }:
        version_type = WorkVersionType.PUBLISHED
    elif record.resource_type == ScholarlyResourceType.PREPRINT:
        version_type = WorkVersionType.PREPRINT
    else:
        version_type = WorkVersionType.UNKNOWN

    return WorkVersionRef(
        source=record.source_name,
        external_id=record.doi,
        url=record.url,
        version_type=version_type,
    )


def version_ref_from_arxiv(entity: PaperEntity) -> Optional[WorkVersionRef]:
    arxiv_id = entity.external_ids.get("arxiv")
    if not arxiv_id:
        return None
    return WorkVersionRef(
        source="arxiv",
        external_id=arxiv_id,
        url=entity.url,
        version_type=WorkVersionType.PREPRINT,
    )


def merge_versions(
    left: List[WorkVersionRef],
    right: List[WorkVersionRef],
) -> List[WorkVersionRef]:
    merged: List[WorkVersionRef] = []
    keys: set[tuple[str, str]] = set()
    for item in [*left, *right]:
        key = (item.source, item.external_id.lower())
        if key in keys:
            continue
        merged.append(item)
        keys.add(key)
    return merged


def _find_match_index(
    entities: List[PaperEntity],
    incoming: PaperEntity,
) -> Optional[int]:
    for index, current in enumerate(entities):
        if _matches(current, incoming):
            return index
    return None


def _matches(left: PaperEntity, right: PaperEntity) -> bool:
    left_doi = left.external_ids.get("doi")
    right_doi = right.external_ids.get("doi")
    if left_doi and right_doi and left_doi.lower() == right_doi.lower():
        return True

    left_arxiv = left.external_ids.get("arxiv")
    right_arxiv = right.external_ids.get("arxiv")
    if left_arxiv and right_arxiv and left_arxiv.lower() == right_arxiv.lower():
        return True

    left_url = canonicalize_url(left.url)
    right_url = canonicalize_url(right.url)
    if left_url and right_url and left_url == right_url:
        return True

    return _strict_title_match(left, right)


def _strict_title_match(left: PaperEntity, right: PaperEntity) -> bool:
    left_fp = title_fingerprint(left.title)
    right_fp = title_fingerprint(right.title)
    if not left_fp or not right_fp:
        return False

    if SequenceMatcher(None, left_fp, right_fp).ratio() < 0.96:
        return False

    if left.year is not None and right.year is not None and abs(left.year - right.year) > 1:
        return False

    return (
        _first_author(left) is not None
        and _first_author(left) == _first_author(right)
    )


def _first_author(entity: PaperEntity) -> Optional[str]:
    if not entity.authors:
        return None
    return " ".join(entity.authors[0].lower().split())


def _merge_entities(left: PaperEntity, right: PaperEntity) -> PaperEntity:
    external_ids = dict(left.external_ids)
    external_ids.update({key: value for key, value in right.external_ids.items() if value})

    abstract = left.abstract
    abstract_source = left.abstract_source
    if not abstract and right.abstract:
        abstract = right.abstract
        abstract_source = right.abstract_source

    resource_type = (
        right.resource_type
        if left.resource_type == ScholarlyResourceType.UNKNOWN
        else left.resource_type
    )
    result_type = result_type_from_resource(resource_type)

    merged = replace(
        left,
        canonical_id=_best_canonical_id(left, right, external_ids),
        title=_better_title(left.title, right.title),
        authors=left.authors or right.authors,
        abstract=abstract,
        abstract_source=abstract_source,
        year=left.year or right.year,
        publication_date=left.publication_date or right.publication_date,
        venue=left.venue or right.venue,
        publisher=left.publisher or right.publisher,
        url=left.url or right.url,
        pdf_url=left.pdf_url or right.pdf_url,
        external_ids=external_ids,
        source_urls=merge_unique(left.source_urls, right.source_urls),
        evidence_sources=merge_unique(left.evidence_sources, right.evidence_sources),
        hydration_sources=merge_unique(left.hydration_sources, right.hydration_sources),
        failed_hydration_sources=merge_unique(
            left.failed_hydration_sources,
            right.failed_hydration_sources,
        ),
        hydration_error_codes=merge_unique(
            left.hydration_error_codes,
            right.hydration_error_codes,
        ),
        hydration_status=_merge_hydration_status(left, right),
        resource_type=resource_type,
        result_type=result_type,
        versions=merge_versions(left.versions, right.versions),
        metadata_confidence=max(left.metadata_confidence, right.metadata_confidence),
        source_confidence=max(left.source_confidence, right.source_confidence),
        discovery_score=max(left.discovery_score, right.discovery_score),
    )
    return _finalize_versions(merged)


def _best_canonical_id(
    left: PaperEntity,
    right: PaperEntity,
    external_ids: Dict[str, str],
) -> str:
    if doi := external_ids.get("doi"):
        return f"doi:{doi}"
    if arxiv_id := external_ids.get("arxiv"):
        return f"arxiv:{arxiv_id}"
    return left.canonical_id or right.canonical_id


def _better_title(left: str, right: str) -> str:
    return right if len(right or "") > len(left or "") else left


def _merge_hydration_status(left: PaperEntity, right: PaperEntity) -> HydrationStatus:
    if HydrationStatus.HYDRATED in {left.hydration_status, right.hydration_status}:
        return HydrationStatus.HYDRATED
    if HydrationStatus.FAILED in {left.hydration_status, right.hydration_status}:
        return HydrationStatus.FAILED
    return HydrationStatus.DISCOVERED_ONLY


def _preferred_url(entity: PaperEntity, record: DOIMetadataRecord) -> Optional[str]:
    return record.url or entity.url


def _finalize_versions(entity: PaperEntity) -> PaperEntity:
    versions = entity.versions
    arxiv_version = version_ref_from_arxiv(entity)
    if arxiv_version is not None:
        versions = merge_versions(versions, [arxiv_version])
    return replace(
        entity,
        versions=versions,
        preferred_version=compute_preferred_version(entity),
        authoritative_version=compute_authoritative_version(entity),
    )
