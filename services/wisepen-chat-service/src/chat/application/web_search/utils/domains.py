import re
from typing import Dict, List, Tuple
from urllib.parse import urlparse

from common.logger import log_fail
from chat.application.web_search.models import SearchResult

_SITE_OPERATOR_RE = re.compile(r"\bsite:", re.IGNORECASE)


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


def deduplicate_results_by_domain(
    results: Tuple[SearchResult, ...],
    *,
    max_per_domain: int = 2,
) -> Tuple[SearchResult, ...]:
    domain_counts: Dict[str, int] = {}
    deduped: List[SearchResult] = []

    for result in results:
        domain = extract_domain(result.url)

        if not domain:
            deduped.append(result)
            continue

        count = domain_counts.get(domain, 0)
        if count >= max_per_domain:
            continue

        domain_counts[domain] = count + 1
        deduped.append(result)

    return tuple(deduped)


def has_site_operator(queries: List[str]) -> bool:
    return any(_SITE_OPERATOR_RE.search(query) is not None for query in queries)
