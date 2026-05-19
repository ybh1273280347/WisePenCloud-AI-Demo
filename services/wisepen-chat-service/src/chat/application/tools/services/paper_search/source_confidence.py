from __future__ import annotations

from urllib.parse import urlparse

_SOURCE_CONFIDENCE_MAP = {
    "arxiv.org": 0.90,
    "pubmed.ncbi.nlm.nih.gov": 0.88,
    "pmc.ncbi.nlm.nih.gov": 0.88,
    "ncbi.nlm.nih.gov": 0.88,
    "openreview.net": 0.85,
    "aclanthology.org": 0.88,
    "proceedings.mlr.press": 0.88,
    "neurips.cc": 0.88,
    "iclr.cc": 0.88,
    "nature.com": 0.85,
    "science.org": 0.85,
    "cell.com": 0.85,
    "thelancet.com": 0.85,
    "nejm.org": 0.85,
    "acm.org": 0.78,
    "ieee.org": 0.78,
    "springer.com": 0.78,
    "springerlink.com": 0.78,
    "sciencedirect.com": 0.78,
    "elsevier.com": 0.78,
    "wiley.com": 0.78,
    "oxfordacademic.com": 0.78,
    "cambridge.org": 0.78,
    "ssrn.com": 0.70,
    "doi.org": 0.75,
}

_DEFAULT_SOURCE_CONFIDENCE = 0.45
_PDF_SOURCE_CONFIDENCE = 0.55
_EDU_SOURCE_CONFIDENCE = 0.65


def compute_source_confidence(url: str) -> float:
    if not url:
        return _DEFAULT_SOURCE_CONFIDENCE

    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        host = host.lower().removeprefix("www.")
    except ValueError:
        return _DEFAULT_SOURCE_CONFIDENCE

    if not host:
        return _DEFAULT_SOURCE_CONFIDENCE

    if host in _SOURCE_CONFIDENCE_MAP:
        return _SOURCE_CONFIDENCE_MAP[host]

    for domain, confidence in _SOURCE_CONFIDENCE_MAP.items():
        if host.endswith(f".{domain}"):
            return confidence

    if host.endswith(".edu"):
        return _EDU_SOURCE_CONFIDENCE

    if parsed.path.lower().endswith(".pdf"):
        return _PDF_SOURCE_CONFIDENCE

    return _DEFAULT_SOURCE_CONFIDENCE
