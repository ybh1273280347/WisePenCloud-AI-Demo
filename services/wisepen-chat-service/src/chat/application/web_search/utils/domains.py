from typing import Tuple
from urllib.parse import urlparse

from chat.application.web_search.models import SearchResult


def extract_domain(url: str) -> str:
    parsed = urlparse(url)

    domain = parsed.hostname
    if not domain:
        return ""

    return domain.lower().removeprefix("www.")


def count_unique_domains(results: Tuple[SearchResult, ...]) -> int:
    domains = {extract_domain(result.url) for result in results}
    domains.discard("")
    return len(domains)
