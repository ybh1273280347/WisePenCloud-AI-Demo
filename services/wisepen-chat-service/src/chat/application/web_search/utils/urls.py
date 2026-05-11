from typing import FrozenSet
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


_TRACKING_QUERY_PARAMS: FrozenSet[str] = frozenset({
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "msclkid",
})


def normalize_url_for_dedup(url: str) -> str:
    value = url.strip()
    if not value:
        return ""

    parsed = urlparse(value)

    if not parsed.scheme or not parsed.hostname:
        return value

    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower().removeprefix("www.")
    port = parsed.port

    include_port = port is not None and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    )
    netloc = host if not include_port else f"{host}:{port}"

    path = parsed.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")

    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_QUERY_PARAMS
    ]
    query = urlencode(sorted(query_items))

    return urlunparse((scheme, netloc, path, "", query, ""))