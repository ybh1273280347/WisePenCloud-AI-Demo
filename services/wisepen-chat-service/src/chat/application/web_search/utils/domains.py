from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from chat.application.web_search.models import SearchResult


def extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
    except Exception:
        return ""

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


def _filter_results_by_domains(
    results: Tuple[SearchResult, ...],
    *,
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
) -> Tuple[SearchResult, ...]:
    include_set = _normalize_domain_filters(include_domains)
    exclude_set = _normalize_domain_filters(exclude_domains)

    if not include_set and not exclude_set:
        return results

    filtered: List[SearchResult] = []

    for result in results:
        hostname = extract_domain(result.url)
        if not hostname:
            continue

        if include_set and not _hostname_matches_any_domain(hostname, include_set):
            continue

        if exclude_set and _hostname_matches_any_domain(hostname, exclude_set):
            continue

        filtered.append(result)

    return tuple(filtered)


def _normalize_domain_filters(domains: Optional[List[str]]) -> Set[str]:
    normalized: Set[str] = set()

    if not domains:
        return normalized

    for domain in domains:
        if not isinstance(domain, str):
            continue

        value = domain.strip().lower()
        if not value:
            continue

        parsed = urlparse(value if "://" in value else f"//{value}")
        hostname = parsed.hostname or value.split("/")[0].split(":")[0]
        hostname = hostname.lower().removeprefix("www.")
        if hostname:
            normalized.add(hostname)

    return normalized


def _hostname_matches_any_domain(hostname: str, domains: Set[str]) -> bool:
    normalized_hostname = hostname.lower().removeprefix("www.")
    return any(
        normalized_hostname == domain
        or normalized_hostname.endswith(f".{domain}")
        for domain in domains
    )
