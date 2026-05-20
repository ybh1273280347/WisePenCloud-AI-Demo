from __future__ import annotations

import math
from typing import List
from urllib.parse import urlparse

from chat.application.algorithms.ranking import (
    FieldedDocument,
    RankedList,
    rank_fielded_bm25,
    tokenize_for_bm25,
    weighted_rrf,
)
from chat.application.algorithms.url import canonicalize_url, stable_hash
from chat.application.tools.services.software_ecosystem.common.scoring import (
    iso_datetime_recency_score,
    parse_iso_datetime,
)

from .models import GitHubIssueResult, GitHubRepositoryResult

_REPOSITORY_FIELD_WEIGHTS = {
    "full_name": 3.0,
    "description": 2.0,
    "language": 0.7,
    "license": 0.4,
    "url_path": 0.8,
}

_ISSUE_FIELD_WEIGHTS = {
    "title": 3.0,
    "body_preview": 1.6,
    "repository_url": 0.6,
}


def rank_repositories(
    query: str,
    repositories: List[GitHubRepositoryResult],
) -> List[GitHubRepositoryResult]:
    if len(repositories) < 2:
        return repositories

    ids = [_repo_id(index, item) for index, item in enumerate(repositories)]
    by_id = dict(zip(ids, repositories))
    position = {item_id: index for index, item_id in enumerate(ids)}

    fielded_docs = [
        FieldedDocument(
            id=item_id,
            fields={
                "full_name": repo.full_name,
                "description": repo.description or "",
                "language": repo.language or "",
                "license": repo.license_name or "",
                "url_path": _url_path_terms(repo.html_url),
            },
        )
        for item_id, repo in zip(ids, repositories)
    ]
    text_overlap_scores = {
        item_id: _repository_text_overlap_score(query, repo)
        for item_id, repo in zip(ids, repositories)
    }

    fused = weighted_rrf(
        [
            RankedList(name="source_original", ids=ids, weight=0.5),
            RankedList(
                name="metadata_bm25",
                ids=rank_fielded_bm25(query, fielded_docs, _REPOSITORY_FIELD_WEIGHTS),
                weight=2.6,
            ),
            RankedList(
                name="metadata_overlap",
                ids=sorted(ids, key=lambda item_id: (-text_overlap_scores[item_id], position[item_id])),
                weight=2.0,
            ),
            RankedList(
                name="maintenance",
                ids=_rank_ids_by_score(ids, repositories, _repository_maintenance_score),
                weight=1.0,
            ),
            RankedList(
                name="popularity",
                ids=_rank_ids_by_score(ids, repositories, _repository_popularity_score),
                weight=0.5,
            ),
            RankedList(
                name="official_hint",
                ids=_rank_ids_by_score(
                    ids,
                    repositories,
                    lambda repo: _repository_official_hint_score(query, repo),
                ),
                weight=0.7,
            ),
        ]
    )

    rrf_rank = {item.id: item.rank for item in fused}
    rrf_score = {item.id: item.score for item in fused}
    ordered_ids = sorted(
        [item.id for item in fused],
        key=lambda item_id: (
            by_id[item_id].archived,
            not bool(by_id[item_id].description),
            _is_stale_repository(by_id[item_id]),
            -text_overlap_scores[item_id],
            -rrf_score[item_id],
            rrf_rank[item_id],
        ),
    )
    return [by_id[item_id] for item_id in ordered_ids]


def rank_issues(
    query: str,
    issues: List[GitHubIssueResult],
) -> List[GitHubIssueResult]:
    if len(issues) < 2:
        return issues

    ids = [_issue_id(index, item) for index, item in enumerate(issues)]
    by_id = dict(zip(ids, issues))
    position = {item_id: index for index, item_id in enumerate(ids)}

    fielded_docs = [
        FieldedDocument(
            id=item_id,
            fields={
                "title": issue.title,
                "body_preview": issue.body_preview or "",
                "repository_url": _url_path_terms(issue.repository_url),
            },
        )
        for item_id, issue in zip(ids, issues)
    ]
    text_overlap_scores = {
        item_id: _issue_text_overlap_score(query, issue)
        for item_id, issue in zip(ids, issues)
    }

    fused = weighted_rrf(
        [
            RankedList(name="source_original", ids=ids, weight=0.4),
            RankedList(
                name="metadata_bm25",
                ids=rank_fielded_bm25(query, fielded_docs, _ISSUE_FIELD_WEIGHTS),
                weight=3.0,
            ),
            RankedList(
                name="metadata_overlap",
                ids=sorted(ids, key=lambda item_id: (-text_overlap_scores[item_id], position[item_id])),
                weight=1.8,
            ),
            RankedList(
                name="discussion_strength",
                ids=_rank_ids_by_score(ids, issues, lambda issue: math.log10(issue.comments + 1)),
                weight=0.9,
            ),
            RankedList(
                name="recency",
                ids=_rank_ids_by_score(ids, issues, lambda issue: iso_datetime_recency_score(issue.updated_at)),
                weight=0.9,
            ),
            RankedList(
                name="type_signal",
                ids=_rank_ids_by_score(ids, issues, lambda issue: _issue_type_score(query, issue)),
                weight=0.25,
            ),
        ]
    )

    rrf_rank = {item.id: item.rank for item in fused}
    rrf_score = {item.id: item.score for item in fused}
    ordered_ids = sorted(
        [item.id for item in fused],
        key=lambda item_id: (
            -text_overlap_scores[item_id],
            -iso_datetime_recency_score(by_id[item_id].updated_at),
            -math.log10(by_id[item_id].comments + 1),
            -rrf_score[item_id],
            rrf_rank[item_id],
        ),
    )
    return [by_id[item_id] for item_id in ordered_ids]


def _repo_id(index: int, repo: GitHubRepositoryResult) -> str:
    key = repo.full_name or repo.html_url or str(index)
    return f"repo:{stable_hash(key)}:{index}"


def _issue_id(index: int, issue: GitHubIssueResult) -> str:
    key = issue.html_url or f"{issue.repository_url}:{issue.title}:{index}"
    return f"issue:{stable_hash(canonicalize_url(key))}:{index}"


def _url_path_terms(url: str) -> str:
    parsed = urlparse(url or "")
    text = " ".join(part for part in [parsed.netloc, parsed.path] if part)
    return (
        text.replace("/", " ")
        .replace("-", " ")
        .replace("_", " ")
        .replace(".", " ")
    )


def _rank_ids_by_score(items_ids: List[str], items: List, scorer) -> List[str]:
    scored = [
        (index, item_id, float(scorer(item)))
        for index, (item_id, item) in enumerate(zip(items_ids, items))
    ]
    scored.sort(key=lambda item: (-item[2], item[0]))
    return [item_id for _, item_id, _ in scored]


def _repository_maintenance_score(repo: GitHubRepositoryResult) -> float:
    score = iso_datetime_recency_score(repo.pushed_at or repo.updated_at)
    if repo.updated_at and repo.pushed_at and repo.updated_at != repo.pushed_at:
        score += 0.15 * iso_datetime_recency_score(repo.updated_at)
    if repo.description:
        score += 0.2
    if repo.license_name:
        score += 0.1
    if repo.archived:
        score -= 3.0
    return score


def _is_stale_repository(repo: GitHubRepositoryResult) -> bool:
    parsed = parse_iso_datetime(repo.pushed_at or repo.updated_at)
    if parsed is None:
        return True
    return iso_datetime_recency_score(repo.pushed_at or repo.updated_at) < 0.25


def _repository_popularity_score(repo: GitHubRepositoryResult) -> float:
    return (
        math.log10(max(repo.stars, 0) + 1)
        + 0.45 * math.log10(max(repo.forks, 0) + 1)
        - 0.03 * math.log10(max(repo.open_issues, 0) + 1)
    )


def _repository_official_hint_score(query: str, repo: GitHubRepositoryResult) -> float:
    query_tokens = set(tokenize_for_bm25(query))
    name_tokens = set(tokenize_for_bm25(repo.full_name.replace("/", " ")))
    description = (repo.description or "").lower()

    score = 0.0
    if query_tokens and query_tokens & name_tokens:
        score += len(query_tokens & name_tokens) / len(query_tokens)
    if "official" in description or "official" in repo.full_name.lower():
        score += 0.5
    if repo.full_name and repo.full_name.split("/", 1)[0].lower() in query_tokens:
        score += 0.25
    return score


def _repository_text_overlap_score(query: str, repo: GitHubRepositoryResult) -> float:
    query_tokens = set(tokenize_for_bm25(query))
    if not query_tokens:
        return 0.0
    weighted_fields = {
        "full_name": repo.full_name,
        "description": repo.description or "",
        "language": repo.language or "",
        "license": repo.license_name or "",
        "url_path": _url_path_terms(repo.html_url),
    }
    score = 0.0
    for field, text in weighted_fields.items():
        tokens = set(tokenize_for_bm25(text))
        if tokens:
            score += _REPOSITORY_FIELD_WEIGHTS[field] * len(query_tokens & tokens)
    return score / max(1, len(query_tokens))


def _issue_text_overlap_score(query: str, issue: GitHubIssueResult) -> float:
    query_tokens = set(tokenize_for_bm25(query))
    if not query_tokens:
        return 0.0
    weighted_fields = {
        "title": issue.title,
        "body_preview": issue.body_preview or "",
        "repository_url": _url_path_terms(issue.repository_url),
    }
    score = 0.0
    for field, text in weighted_fields.items():
        tokens = set(tokenize_for_bm25(text))
        if tokens:
            score += _ISSUE_FIELD_WEIGHTS[field] * len(query_tokens & tokens)
    return score / max(1, len(query_tokens))


def _issue_type_score(query: str, issue: GitHubIssueResult) -> float:
    query_tokens = set(tokenize_for_bm25(query))
    wants_pr = bool(query_tokens & {"pr", "pull", "request", "pull-request"})
    wants_issue = bool(query_tokens & {"issue", "bug", "error", "question"})
    if issue.is_pull_request:
        return 1.0 if wants_pr else 0.35
    return 1.0 if wants_issue else 0.65
