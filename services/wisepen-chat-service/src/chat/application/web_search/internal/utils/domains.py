from typing import Dict, List, Tuple

from chat.application.web_search.models import SearchResult
from chat.application.web_search.utils.domains import extract_domain


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
