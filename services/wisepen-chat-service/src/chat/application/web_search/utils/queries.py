from typing import List, Optional, Set, Tuple

from chat.application.web_search.utils.notes import add_note

MAX_SEARCH_QUERY_CHARS = 400


def normalize_queries(
    queries: List[str],
    *,
    limit: int,
    notes: Optional[List[str]] = None,
) -> List[str]:
    normalized: List[str] = []
    seen: Set[str] = set()
    skipped_duplicates = 0
    limit_reached = False

    for query in queries:
        if not isinstance(query, str):
            continue

        value = " ".join(query.strip().split())
        if not value:
            continue

        value, was_truncated = _truncate_query(value)
        if was_truncated:
            add_note(notes, "Query truncated to 400 characters.")

        key = value.lower()
        if key in seen:
            skipped_duplicates += 1
            continue

        if len(normalized) >= limit:
            limit_reached = True
            break

        seen.add(key)
        normalized.append(value)

    if skipped_duplicates:
        add_note(notes, f"{skipped_duplicates} duplicate search queries were removed.")

    if limit_reached:
        add_note(notes, f"Search queries were limited to {limit} focused queries.")

    return normalized


def _truncate_query(query: str) -> Tuple[str, bool]:
    if len(query) <= MAX_SEARCH_QUERY_CHARS:
        return query, False

    candidate = query[:MAX_SEARCH_QUERY_CHARS].rstrip()
    space_index = candidate.rfind(" ")

    if space_index >= MAX_SEARCH_QUERY_CHARS // 2:
        candidate = candidate[:space_index].rstrip()

    if not candidate:
        candidate = query[:MAX_SEARCH_QUERY_CHARS].rstrip()

    return candidate, True
