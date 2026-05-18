from __future__ import annotations

from dataclasses import replace
from typing import List, Optional, Set, Tuple

from chat.application.algorithms.text import fuzzy_title_match, normalize_title_key

from .models import PaperSearchResult
from .ranking import PaperRankingCandidate, normalize_paper_candidates


def deduplicate_papers(results: List[PaperSearchResult]) -> List[PaperSearchResult]:
    return [
        candidate.paper
        for candidate in deduplicate_paper_candidates(normalize_paper_candidates(results))
    ]


def deduplicate_paper_candidates(
    candidates: List[PaperRankingCandidate],
) -> List[PaperRankingCandidate]:
    merged: List[PaperRankingCandidate] = []
    for candidate in candidates:
        existing_index = _find_duplicate_index(candidate, merged)
        if existing_index is None:
            merged.append(candidate)
            continue

        existing = merged[existing_index]
        merged_paper = merge_paper(existing.paper, candidate.paper)
        source_rank = min(existing.source_rank, candidate.source_rank)
        merged[existing_index] = _candidate_from_merged_paper(
            merged_paper,
            source_rank=source_rank,
            fallback_id=existing.id,
        )
    return merged


def paper_key(result: PaperSearchResult) -> str:
    if result.doi:
        return f"doi:{result.doi.lower()}"
    if result.arxiv_id:
        return f"arxiv:{result.arxiv_id.lower()}"
    title = normalize_title(result.title)
    return f"title:{title}"


def normalize_title(title: str) -> str:
    return normalize_title_key(title)


def merge_paper(left: PaperSearchResult, right: PaperSearchResult) -> PaperSearchResult:
    preferred, secondary = _prefer_paper(left, right)
    return replace(
        preferred,
        title=preferred.title or secondary.title,
        authors=preferred.authors or secondary.authors,
        year=preferred.year or secondary.year,
        abstract=preferred.abstract or secondary.abstract,
        venue=preferred.venue or secondary.venue,
        doi=preferred.doi or secondary.doi,
        arxiv_id=preferred.arxiv_id or secondary.arxiv_id,
        url=preferred.url or secondary.url,
        pdf_url=preferred.pdf_url or secondary.pdf_url,
        source_urls=_merge_unique(preferred.source_urls, secondary.source_urls),
        source_names=_merge_unique(preferred.source_names, secondary.source_names),
        publication_date=preferred.publication_date or secondary.publication_date,
        is_open_access=_merge_open_access(preferred.is_open_access, secondary.is_open_access),
        result_type=preferred.result_type or secondary.result_type,
        authority_score=max(preferred.authority_score, secondary.authority_score),
        relevance_score=max(preferred.relevance_score, secondary.relevance_score),
    )


def _find_duplicate_index(
    candidate: PaperRankingCandidate,
    existing: List[PaperRankingCandidate],
) -> Optional[int]:
    for index, current in enumerate(existing):
        if candidate.doi and current.doi and candidate.doi == current.doi:
            return index
        if candidate.arxiv_id and current.arxiv_id and candidate.arxiv_id == current.arxiv_id:
            return index
        if (
            normalize_title(candidate.title)
            and normalize_title(candidate.title) == normalize_title(current.title)
        ):
            return index
        if _year_close(candidate.year, current.year) and fuzzy_title_match(candidate.title, current.title):
            return index
    return None


def _candidate_from_merged_paper(
    paper: PaperSearchResult,
    *,
    source_rank: int,
    fallback_id: str,
) -> PaperRankingCandidate:
    candidate = normalize_paper_candidates([paper])[0]
    return replace(
        candidate,
        id=fallback_id,
        source_rank=source_rank,
    )


def _prefer_paper(
    left: PaperSearchResult,
    right: PaperSearchResult,
) -> Tuple[PaperSearchResult, PaperSearchResult]:
    left_score = _publication_preference_score(left)
    right_score = _publication_preference_score(right)
    if right_score > left_score:
        return right, left
    return left, right


def _publication_preference_score(result: PaperSearchResult) -> float:
    result_type = (result.result_type or "").lower()
    is_preprint = "preprint" in result_type or (
        "arxiv" in {source.lower() for source in result.source_names} and not result.doi
    )
    score = 0.0
    if result.doi and not is_preprint:
        score += 4.0
    elif result.doi:
        score += 3.0
    elif not is_preprint:
        score += 1.0
    if "crossref" in result.source_names:
        score += 0.4
    if "datacite" in result.source_names:
        score += 0.3
    if result.venue:
        score += 0.2
    return score


def _year_close(left: Optional[int], right: Optional[int]) -> bool:
    if left is None or right is None:
        return False
    return abs(left - right) <= 1


def _merge_open_access(left: Optional[bool], right: Optional[bool]) -> Optional[bool]:
    if left is True or right is True:
        return True
    if left is False and right is False:
        return False
    return left if left is not None else right


def _merge_unique(left: List[str], right: List[str]) -> List[str]:
    result: List[str] = []
    seen: Set[str] = set()
    for item in [*left, *right]:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result
