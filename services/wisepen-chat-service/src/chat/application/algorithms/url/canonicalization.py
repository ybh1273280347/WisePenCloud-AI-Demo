from __future__ import annotations

import hashlib
import re
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "fbclid",
    "gclid",
    "msclkid",
    "ref",
    "spm",
}


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def canonicalize_url(url: str, *, base_url: Optional[str] = None) -> str:
    raw_url = (url or "").strip()
    if base_url:
        raw_url = urljoin(base_url, raw_url)

    parsed = urlparse(raw_url)

    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()

    if netloc.startswith("www."):
        netloc = netloc[4:]

    path = re.sub(r"/+", "/", parsed.path or "/")
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    query_pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        if key.lower() in _TRACKING_PARAMS:
            continue
        query_pairs.append((key, value))

    query_pairs.sort()
    query = urlencode(query_pairs, doseq=True)

    return urlunparse((scheme, netloc, path, "", query, ""))
