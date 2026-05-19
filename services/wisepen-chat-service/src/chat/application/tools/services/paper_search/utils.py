from __future__ import annotations

import hashlib
from typing import Iterable, List, TypeVar
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

T = TypeVar("T")


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def merge_unique(left: Iterable[T], right: Iterable[T]) -> List[T]:
    merged: List[T] = []
    for item in [*left, *right]:
        if item and item not in merged:
            merged.append(item)
    return merged


def canonicalize_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if not host:
        return url
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
    ]
    return urlunparse(
        (
            parsed.scheme.lower() or "https",
            host,
            path,
            "",
            urlencode(sorted(query_items)),
            "",
        )
    )
