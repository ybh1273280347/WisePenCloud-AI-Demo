from __future__ import annotations

import re
from typing import Iterable, List, Optional
from urllib.parse import unquote, urlparse

from .models import DOIExtractionConfidence, ExtractedDOI, PaperPointer

_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)
_VALID_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)

_MODERN_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(?:v\d+)?$")
_LEGACY_ARXIV_ID_RE = re.compile(r"^[a-z-]+(?:\.[A-Za-z]{2})?/\d{7}(?:v\d+)?$")
_ARXIV_DOI_PREFIX = "10.48550/arxiv."


def normalize_doi(value: str) -> Optional[str]:
    if not isinstance(value, str):
        raise TypeError("value must be str.")

    text = value.strip().lower()
    prefixes = (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "doi:",
    )

    for prefix in prefixes:
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break

    text = text.rstrip(".,;)]}")
    if not _VALID_DOI_RE.match(text):
        return None

    return text


def extract_dois_from_text(value: str) -> List[str]:
    normalized: List[str] = []
    for match in _DOI_RE.findall(value):
        doi = normalize_doi(match)
        if doi and doi not in normalized:
            normalized.append(doi)
    return normalized


def extract_doi_from_url(url: str) -> Optional[str]:
    parsed = urlparse(url)
    raw = unquote(url)
    host = parsed.hostname or ""

    if "doi.org" in host:
        return normalize_doi(parsed.path.lstrip("/"))

    return next(iter(extract_dois_from_text(raw)), None)


def is_valid_arxiv_id(value: str) -> bool:
    return bool(_MODERN_ARXIV_ID_RE.match(value) or _LEGACY_ARXIV_ID_RE.match(value))


def normalize_arxiv_id(value: str) -> Optional[str]:
    text = value.strip()
    text = re.sub(r"^(?:arxiv:)", "", text, flags=re.IGNORECASE).strip()
    text = text.removesuffix(".pdf")
    return text if is_valid_arxiv_id(text) else None


def extract_arxiv_id_from_arxiv_doi(doi: str) -> Optional[str]:
    normalized = normalize_doi(doi)
    if not normalized:
        return None

    lower = normalized.lower()
    if not lower.startswith(_ARXIV_DOI_PREFIX):
        return None

    suffix = normalized[len(_ARXIV_DOI_PREFIX) :]

    if _MODERN_ARXIV_ID_RE.match(suffix):
        return suffix

    legacy_dot = re.match(
        r"^([a-z-]+(?:\.[a-z]{2})?)\.(\d{7})(?:v\d+)?$",
        suffix,
        re.IGNORECASE,
    )
    if legacy_dot:
        return f"{legacy_dot.group(1)}/{legacy_dot.group(2)}"

    return None


def extract_arxiv_id_from_url(url: str) -> Optional[str]:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.strip("/")

    if not host.endswith("arxiv.org"):
        return None

    for prefix in ("abs/", "pdf/", "html/"):
        if path.startswith(prefix):
            raw = path[len(prefix) :]
            raw = raw.removesuffix(".pdf")
            if is_valid_arxiv_id(raw):
                return raw

    return None


def collect_candidate_dois(pointer: PaperPointer) -> List[ExtractedDOI]:
    results: List[ExtractedDOI] = []

    url_doi = extract_doi_from_url(pointer.url)
    if url_doi:
        results.append(
            ExtractedDOI(
                doi=url_doi,
                source="url",
                confidence=DOIExtractionConfidence.HIGH,
            )
        )

    text = " ".join(
        item for item in [pointer.title, pointer.snippet or "", *pointer.highlights] if item
    )

    for doi in extract_dois_from_text(text):
        if doi == url_doi:
            continue
        results.append(
            ExtractedDOI(
                doi=doi,
                source="text",
                confidence=DOIExtractionConfidence.MEDIUM,
            )
        )

    return _deduplicate_extracted_dois(results)


def apply_identifier_extraction(pointer: PaperPointer) -> PaperPointer:
    from dataclasses import replace

    arxiv_id = pointer.extracted_arxiv_id or extract_arxiv_id_from_url(pointer.url)
    doi = pointer.extracted_doi or _best_candidate_doi(pointer)
    return replace(pointer, extracted_arxiv_id=arxiv_id, extracted_doi=doi)


def _best_candidate_doi(pointer: PaperPointer) -> Optional[str]:
    candidates = collect_candidate_dois(pointer)
    return candidates[0].doi if candidates else None


def _deduplicate_extracted_dois(values: Iterable[ExtractedDOI]) -> List[ExtractedDOI]:
    seen: set[str] = set()
    result: List[ExtractedDOI] = []

    for value in values:
        if value.doi in seen:
            continue
        result.append(value)
        seen.add(value.doi)

    return result
