from __future__ import annotations

from collections import defaultdict
from urllib.parse import urlparse
from typing import List, Dict, Tuple, Set

from chat.application.algorithms.ranking import (
    FieldedDocument,
    RankedList,
    RRF_K,
    rank_fielded_bm25,
    score_fielded_bm25,
    weighted_rrf,
)
from chat.application.algorithms.url import canonicalize_url, stable_hash
from chat.application.web_search.models import SearchResponse
from chat.application.web_search.planning import VariantSearchResponse
from chat.application.web_search.planning.planner import QUERY_VARIANT_WEIGHTS
from chat.application.web_search.ranking.bm25 import (
    METADATA_FIELD_WEIGHTS,
    extract_url_path_terms,
)
from chat.application.web_search.ranking.models import (
    RankedUrlCandidate,
    SearchUrlCandidate,
)
from common.logger import log_event


URL_RRF_WEIGHTS = {
    "source_original": 1.0,
    "metadata_bm25": 1.0,
}


def build_url_candidates_from_response(
    *,
    response: SearchResponse,
    provider: str,
    source_query: str,
    query_language: str,
    query_role: str,
) -> List[SearchUrlCandidate]:
    candidates: List[SearchUrlCandidate] = []

    for rank, result in enumerate(response.results):
        canonical_url = canonicalize_url(result.url)

        candidates.append(
            SearchUrlCandidate(
                id=stable_hash(canonical_url),
                url=result.url,
                canonical_url=canonical_url,
                title=result.title,
                snippet=result.snippet,
                provider=provider,
                source_query=source_query,
                query_language=query_language,
                query_role=query_role,
                original_rank=rank,
            )
        )

    return candidates


def deduplicate_by_canonical_url(
    candidates: List[SearchUrlCandidate],
) -> List[SearchUrlCandidate]:
    by_url: Dict[str, SearchUrlCandidate] = {}

    for candidate in candidates:
        existing = by_url.get(candidate.canonical_url)

        if existing is None:
            by_url[candidate.canonical_url] = candidate
            continue

        if candidate.original_rank < existing.original_rank:
            by_url[candidate.canonical_url] = candidate

    return list(by_url.values())


def rank_by_source_original(
    candidates: List[SearchUrlCandidate],
) -> List[str]:
    return [
        candidate.id
        for candidate in sorted(candidates, key=lambda item: item.original_rank)
    ]


def _metadata_documents(candidates: List[SearchUrlCandidate]) -> List[FieldedDocument]:
    return [
        FieldedDocument(
            id=candidate.id,
            fields={
                "title": candidate.title,
                "snippet": candidate.snippet,
                "url_path": extract_url_path_terms(candidate.url),
            },
        )
        for candidate in candidates
    ]


def rerank_urls_for_single_query(
    *,
    query: str,
    candidates: List[SearchUrlCandidate],
) -> List[RankedUrlCandidate]:
    deduped = deduplicate_by_canonical_url(candidates)

    duplicates = len(candidates) - len(deduped)
    if duplicates > 0:
        log_event(
            "ranking 去重",
            query=query,
            before=len(candidates),
            after=len(deduped),
            duplicates=duplicates,
        )

    source_ranked_ids = rank_by_source_original(deduped)
    metadata_documents = _metadata_documents(deduped)
    bm25_ranked_ids = rank_fielded_bm25(query, metadata_documents, METADATA_FIELD_WEIGHTS)

    raw_scores = score_fielded_bm25(query, metadata_documents, METADATA_FIELD_WEIGHTS)
    if raw_scores:
        scored = [s for s in raw_scores.values() if s > 0]
        log_event(
            "ranking BM25",
            query=query,
            candidates=len(deduped),
            nonzero=len(scored),
            top_score=f"{max(raw_scores.values()):.4f}" if raw_scores else "0",
        )

    rrf_items = weighted_rrf(
        [
            RankedList(
                name="source_original",
                ids=source_ranked_ids,
                weight=URL_RRF_WEIGHTS["source_original"],
            ),
            RankedList(
                name="metadata_bm25",
                ids=bm25_ranked_ids,
                weight=URL_RRF_WEIGHTS["metadata_bm25"],
            ),
        ],
        k=RRF_K,
    )

    by_id = {candidate.id: candidate for candidate in deduped}

    result = [
        RankedUrlCandidate(
            candidate=by_id[item.id],
            rrf_score=item.score,
            rank=item.rank,
            rrf_sources=item.sources,
        )
        for item in rrf_items
        if item.id in by_id
    ]

    if result:
        log_event(
            "ranking RRF",
            query=query,
            output=len(result),
            top_score=f"{result[0].rrf_score:.4f}",
            top_url=result[0].candidate.url,
        )

    return result


def fuse_query_variant_rankings(
    *,
    variant_rankings: List[Tuple[str, str, List[RankedUrlCandidate]]],
    mode: str,
) -> List[RankedUrlCandidate]:
    weights = QUERY_VARIANT_WEIGHTS.get(mode, QUERY_VARIANT_WEIGHTS["normal"])

    before = sum(len(urls) for _, _, urls in variant_rankings)

    ranked_lists: List[RankedList] = []

    for variant_id, role, ranked_urls in variant_rankings:
        ranked_lists.append(
            RankedList(
                name=f"variant:{variant_id}:{role}",
                ids=[item.candidate.id for item in ranked_urls],
                weight=weights.get(role, 0.5),
            )
        )

    rrf_items = weighted_rrf(ranked_lists, k=RRF_K)

    by_id: Dict[str, SearchUrlCandidate] = {}
    for _variant_id, _role, ranked_urls in variant_rankings:
        for item in ranked_urls:
            if item.candidate.id not in by_id:
                by_id[item.candidate.id] = item.candidate

    result = [
        RankedUrlCandidate(
            candidate=by_id[item.id],
            rrf_score=item.score,
            rank=item.rank,
            rrf_sources=item.sources,
        )
        for item in rrf_items
        if item.id in by_id
    ]

    log_event(
        "ranking variants 融合",
        groups=len(variant_rankings),
        before=before,
        after=len(result),
    )

    return result


def apply_domain_diversity(
    ranked_candidates: List[RankedUrlCandidate],
    *,
    max_urls_per_domain: int,
    limit: int,
) -> List[RankedUrlCandidate]:
    counts: Dict[str, int] = defaultdict(int)
    selected: List[RankedUrlCandidate] = []

    for item in ranked_candidates:
        domain = urlparse(item.candidate.canonical_url).netloc

        if counts[domain] >= max_urls_per_domain:
            continue

        counts[domain] += 1
        selected.append(item)

        if len(selected) >= limit:
            break

    dropped = len(ranked_candidates) - len(selected)
    if dropped > 0:
        log_event(
            "ranking domain diversity",
            before=len(ranked_candidates),
            after=len(selected),
            dropped=dropped,
            domains=len(counts),
            max_per_domain=max_urls_per_domain,
            limit=limit,
        )

    return selected


def build_url_candidates_from_variants(
    variant_responses: List[VariantSearchResponse],
) -> List[SearchUrlCandidate]:
    all_candidates: List[SearchUrlCandidate] = []

    for vr in variant_responses:
        provider = vr.response.source or "searxng"
        candidates = build_url_candidates_from_response(
            response=vr.response,
            provider=provider,
            source_query=vr.variant.text,
            query_language=vr.variant.language or "",
            query_role=vr.variant.role,
        )
        all_candidates.extend(candidates)

    return all_candidates


def _dedup_ranked_by_canonical_url(
    ranked: List[RankedUrlCandidate],
) -> List[RankedUrlCandidate]:
    seen: Set[str] = set()
    deduped: List[RankedUrlCandidate] = []

    for item in ranked:
        if item.candidate.canonical_url in seen:
            continue
        seen.add(item.candidate.canonical_url)
        deduped.append(item)

    return deduped


def rank_urls_pipeline(
    *,
    variant_responses: List[VariantSearchResponse],
    mode: str,
    merged_limit: int,
    max_urls_per_domain: int = 2,
) -> List[RankedUrlCandidate]:
    all_candidates = build_url_candidates_from_variants(variant_responses)
    n_variants = len(variant_responses)

    log_event(
        "ranking pipeline",
        variants=n_variants,
        candidates=len(all_candidates),
        mode=mode,
        merged_limit=merged_limit,
    )

    if not all_candidates:
        return []

    query_groups: Dict[str, List[SearchUrlCandidate]] = {}
    for candidate in all_candidates:
        key = f"{candidate.source_query}:{candidate.query_role}"
        query_groups.setdefault(key, []).append(candidate)

    variant_rankings: List[Tuple[str, str, List[RankedUrlCandidate]]] = []

    for group_key, group_candidates in query_groups.items():
        parts = group_key.split(":", 1)
        query_text = parts[0]
        role = parts[1] if len(parts) > 1 else "primary"

        ranked = rerank_urls_for_single_query(
            query=query_text,
            candidates=group_candidates,
        )
        variant_rankings.append((group_key, role, ranked))

    if len(variant_rankings) == 1:
        ranked_urls = variant_rankings[0][2]
    else:
        ranked_urls = fuse_query_variant_rankings(
            variant_rankings=variant_rankings,
            mode=mode,
        )

    before_dedup = len(ranked_urls)
    ranked_urls = _dedup_ranked_by_canonical_url(ranked_urls)
    dedup_hit = before_dedup - len(ranked_urls)
    if dedup_hit > 0:
        log_event(
            "ranking dedup",
            before=before_dedup,
            after=len(ranked_urls),
            duplicates=dedup_hit,
        )

    before_domain = len(ranked_urls)
    ranked_urls = apply_domain_diversity(
        ranked_urls,
        max_urls_per_domain=max_urls_per_domain,
        limit=merged_limit,
    )

    log_event(
        "ranking 完成",
        mode=mode,
        input_candidates=len(all_candidates),
        after_fusion=before_dedup,
        after_dedup=before_domain,
        domain_dropped=before_domain - len(ranked_urls),
        final=len(ranked_urls),
    )

    return ranked_urls
