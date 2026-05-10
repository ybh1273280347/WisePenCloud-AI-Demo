from __future__ import annotations

import re
from typing import Any, List


_SITE_OPERATOR_RE = re.compile(r"\bsite:", re.IGNORECASE)


def normalize_bool(value: Any) -> bool:
    """Normalize common tool input values to bool."""
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
        return False

    return bool(value)


def normalize_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Normalize a value to a bounded integer."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default

    return max(minimum, min(number, maximum))


def has_site_operator(queries: List[str]) -> bool:
    return any(_SITE_OPERATOR_RE.search(query) is not None for query in queries)
