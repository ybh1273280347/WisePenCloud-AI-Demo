from __future__ import annotations

import re
from typing import List, Optional, Tuple

from chat.application.web_search.internal.planning.models import (
    QueryVariant,
    SearchPlan,
    WikipediaKeyword,
)

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

_CJK_RATIO_THRESHOLD = 0.3

_EN_PROFILE_ENGINES: Tuple[str, ...] = ("aol",)
_ZH_PROFILE_ENGINES: Tuple[str, ...] = ("bing",)

QUERY_VARIANT_MAX_RESULTS = {
    "fast": {
        "primary": 10,
    },
    "normal": {
        "primary": 12,
        "secondary": 6,
    },
    "deep": {
        "primary": 30,
        "secondary": 15,
        "extra": 10,
        "extra_2": 10,
    },
}

QUERY_VARIANT_WEIGHTS = {
    "normal": {
        "primary": 1.0,
        "secondary": 0.6,
    },
    "deep": {
        "primary": 1.0,
        "secondary": 0.8,
        "extra": 0.5,
        "extra_2": 0.5,
    },
}

GROUNDING_BUDGET = {
    "fast": {
        "max_keywords": 0,
        "max_extract_chars_per_keyword": 0,
    },
    "normal": {
        "max_keywords": 1,
        "max_extract_chars_per_keyword": 600,
    },
    "deep": {
        "max_keywords": 3,
        "max_extract_chars_per_keyword": 800,
    },
}

MERGED_CANDIDATE_LIMIT = {
    "fast": 10,
    "normal": 16,
    "deep": 40,
}


def detect_query_language(query: str) -> str:
    compact = "".join(query.split())
    if not compact:
        return "en"

    cjk_chars = len(_CJK_RE.findall(compact))
    ratio = cjk_chars / len(compact)

    return "zh" if ratio > _CJK_RATIO_THRESHOLD else "en"


_VALID_MODES = frozenset(QUERY_VARIANT_MAX_RESULTS.keys())


class InvalidSearchModeError(ValueError):
    pass


def build_search_plan(
    *,
    mode: str,
    queries: List[str],
    wikipedia_keywords: Optional[List[str]] = None,
) -> SearchPlan:
    if mode not in _VALID_MODES:
        raise InvalidSearchModeError(
            f"Invalid search mode: {mode!r}. Must be one of {sorted(_VALID_MODES)}."
        )

    variant_budget = QUERY_VARIANT_MAX_RESULTS[mode]
    variant_weights = QUERY_VARIANT_WEIGHTS.get(mode, {})

    roles = list(variant_budget.keys())
    truncated_queries = queries[: len(roles)]

    variants: List[QueryVariant] = []

    for i, query in enumerate(truncated_queries):
        role = roles[i]
        language = detect_query_language(query)

        if language == "zh":
            engines = _ZH_PROFILE_ENGINES
            serial = True  # stability guard for current SearXNG + Bing profile, not inherent to Chinese
        else:
            engines = _EN_PROFILE_ENGINES
            serial = False

        max_results = variant_budget[role]
        weight = variant_weights.get(role, 0.5)

        variants.append(
            QueryVariant(
                id=f"v{i}",
                text=query,
                role=role,
                language=language,
                engines=engines,
                serial=serial,
                max_results=max_results,
                weight=weight,
            )
        )

    selected_keywords: List[WikipediaKeyword] = []
    raw_keywords = wikipedia_keywords or []
    grounding_budget = GROUNDING_BUDGET[mode]
    max_kw = grounding_budget["max_keywords"]

    for raw_kw in raw_keywords:
        keyword = raw_kw.strip()

        if not keyword:
            continue

        kw_language = detect_query_language(keyword)
        selected_keywords.append(
            WikipediaKeyword(
                text=keyword,
                language=kw_language,
            )
        )

        if len(selected_keywords) >= max_kw:
            break

    return SearchPlan(
        mode=mode,
        query_variants=tuple(variants),
        wikipedia_keywords=tuple(selected_keywords),
    )
