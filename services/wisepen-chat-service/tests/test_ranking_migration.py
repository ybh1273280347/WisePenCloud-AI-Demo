from __future__ import annotations

import asyncio
from dataclasses import replace

from chat.application.algorithms.ranking import (
    FieldedDocument,
    RankedList,
    rank_documents_by_bm25,
    rank_fielded_bm25,
    score_fielded_bm25,
    tokenize_for_bm25,
    weighted_rrf,
)
from chat.application.algorithms.url import canonicalize_url, stable_hash
from chat.application.tools.services.code_search.github_search import service as github_service_module
from chat.application.tools.services.code_search.github_search.models import (
    GitHubIssueResult,
    GitHubReleaseResult,
    GitHubRepositoryResult,
)
from chat.application.tools.services.code_search.github_search.ranking import (
    rank_issues,
    rank_repositories,
)
from chat.application.tools.services.code_search.github_search.service import GitHubSearchService
from chat.application.web_search.internal.ranking.models import SearchUrlCandidate
from chat.application.web_search.internal.ranking.url_ranker import (
    deduplicate_by_canonical_url,
)


def test_tokenize_for_bm25_english_and_chinese() -> None:
    assert "python" in tokenize_for_bm25("The Python async guide")
    chinese_tokens = tokenize_for_bm25("中文搜索排序算法")
    assert any(token in chinese_tokens for token in ["中文", "搜索", "排序"])


def test_rank_documents_by_bm25_edges_and_scores() -> None:
    assert rank_documents_by_bm25("x", []).ranked == ()

    empty_query = rank_documents_by_bm25("", [("a", "alpha"), ("b", "beta")])
    assert [item.id for item in empty_query.ranked] == ["a", "b"]
    assert all(item.score == 0.0 for item in empty_query.ranked)

    single = rank_documents_by_bm25("alpha", [("a", "alpha")])
    assert single.ranked[0].score == 1.0

    ranked = rank_documents_by_bm25(
        "python",
        [("a", "python async"), ("b", "java stream"), ("c", "rust borrow")],
    )
    assert ranked.ranked[0].id == "a"


def test_rank_documents_by_bm25_cache_hit_and_fingerprint_rebuild() -> None:
    key = "ranking-migration-script-cache"
    docs = [("a", "python async"), ("b", "java stream"), ("c", "rust borrow")]

    first = rank_documents_by_bm25("python", docs, cache_key=key)
    second = rank_documents_by_bm25("python", docs, cache_key=key)
    changed = rank_documents_by_bm25(
        "python",
        [("a", "python async"), ("b", "python ranking"), ("c", "rust borrow")],
        cache_key=key,
    )

    assert not first.cache_hit
    assert second.cache_hit
    assert not changed.cache_hit


def test_fielded_bm25_weights_and_stable_order() -> None:
    docs = [
        FieldedDocument(id="title", fields={"title": "python", "abstract": ""}),
        FieldedDocument(id="abstract", fields={"title": "", "abstract": "python python"}),
        FieldedDocument(id="none", fields={"title": "", "abstract": ""}),
    ]
    scores = score_fielded_bm25("python", docs, {"title": 4.0, "abstract": 0.1})
    assert scores["title"] > scores["abstract"]
    assert rank_fielded_bm25("", docs, {"title": 1.0}) == ["title", "abstract", "none"]


def test_weighted_rrf_and_url_canonicalization() -> None:
    fused = weighted_rrf(
        [
            RankedList(name="source", ids=["a", "b"], weight=1.0),
            RankedList(name="metadata", ids=["b", "a"], weight=3.0),
        ]
    )
    assert fused[0].id == "b"
    assert (
        canonicalize_url("HTTP://www.Example.com//docs/?utm_source=x&b=2&a=1")
        == "http://example.com/docs?a=1&b=2"
    )
    assert stable_hash("same") == stable_hash("same")


def test_web_search_url_dedup_uses_canonical_url() -> None:
    older = _web_candidate(
        "old",
        "https://www.example.com/docs/?utm_source=x",
        original_rank=5,
    )
    better = _web_candidate(
        "new",
        "https://example.com/docs/",
        original_rank=1,
    )
    deduped = deduplicate_by_canonical_url([older, better])
    assert [item.id for item in deduped] == ["new"]


def test_repository_ranking_relevance_archived_and_recency() -> None:
    unrelated = _repo(
        "other/project",
        "generic framework",
        stars=10000,
        pushed_at="2026-01-01T00:00:00Z",
    )
    relevant = _repo(
        "acme/async-python-client",
        "Python async API client",
        stars=10,
        pushed_at="2025-12-01T00:00:00Z",
    )
    archived = replace(relevant, full_name="acme/archived-client", archived=True)
    old = _repo(
        "acme/old-client",
        "Python async API client",
        pushed_at="2018-01-01T00:00:00Z",
    )

    ranked = rank_repositories("python async client", [unrelated, relevant, archived])
    assert ranked[0].full_name == "acme/async-python-client"
    assert ranked[-1].full_name == "acme/archived-client"

    recency_ranked = rank_repositories("python async client", [old, relevant])
    assert recency_ranked[0].full_name == "acme/async-python-client"


def test_issue_ranking_relevance_comments_and_updated_at() -> None:
    unrelated = _issue(
        "documentation typo",
        "minor docs issue",
        comments=0,
        updated_at="2021-01-01T00:00:00Z",
    )
    relevant = _issue(
        "async timeout bug",
        "client request timeout with async transport",
        comments=8,
        updated_at="2026-01-01T00:00:00Z",
    )

    ranked = rank_issues("async timeout bug", [unrelated, relevant])
    assert ranked[0].title == "async timeout bug"


def test_search_operations_call_ranking() -> None:
    asyncio.run(_test_search_operations_call_ranking())


async def _test_search_operations_call_ranking() -> None:
    service = GitHubSearchService(_FakeGitHubClient())
    original_repo_ranker = github_service_module.ranking.rank_repositories
    original_issue_ranker = github_service_module.ranking.rank_issues
    calls = {"repos": 0, "issues": 0}

    def repo_ranker(query, repos):
        calls["repos"] += 1
        return list(reversed(repos))

    def issue_ranker(query, issues):
        calls["issues"] += 1
        return list(reversed(issues))

    try:
        github_service_module.ranking.rank_repositories = repo_ranker
        _total, _incomplete, repos = await service.search_repositories(
            query="python",
            sort=None,
            order="desc",
            limit=2,
        )
        assert calls["repos"] == 1
        assert repos[0].full_name == "owner/b"

        github_service_module.ranking.rank_issues = issue_ranker
        _total, _incomplete, issues = await service.search_issues(
            query="bug",
            sort=None,
            order="desc",
            limit=2,
        )
        assert calls["issues"] == 1
        assert issues[0].title == "second"
    finally:
        github_service_module.ranking.rank_repositories = original_repo_ranker
        github_service_module.ranking.rank_issues = original_issue_ranker


def test_get_operations_do_not_call_ranking() -> None:
    asyncio.run(_test_get_operations_do_not_call_ranking())


async def _test_get_operations_do_not_call_ranking() -> None:
    service = GitHubSearchService(_FakeGitHubClient())
    original_repo_ranker = github_service_module.ranking.rank_repositories
    original_issue_ranker = github_service_module.ranking.rank_issues

    def forbidden_repo_ranker(query, repos):
        raise AssertionError("get_repository must not call repository ranking")

    def forbidden_issue_ranker(query, issues):
        raise AssertionError("get operations must not call issue ranking")

    try:
        github_service_module.ranking.rank_repositories = forbidden_repo_ranker
        github_service_module.ranking.rank_issues = forbidden_issue_ranker

        repo = await service.get_repository(owner="owner", repo="a")
        readme = await service.get_readme(owner="owner", repo="a")
        releases = await service.get_releases(owner="owner", repo="a", limit=1)

        assert repo.full_name == "owner/a"
        assert readme["repo"] == "owner/a"
        assert isinstance(releases[0], GitHubReleaseResult)
    finally:
        github_service_module.ranking.rank_repositories = original_repo_ranker
        github_service_module.ranking.rank_issues = original_issue_ranker


def _web_candidate(item_id: str, url: str, *, original_rank: int) -> SearchUrlCandidate:
    return SearchUrlCandidate(
        id=item_id,
        url=url,
        canonical_url=canonicalize_url(url),
        title="Title",
        snippet="Snippet",
        provider="unit",
        source_query="query",
        query_language="en",
        query_role="primary",
        original_rank=original_rank,
    )


def _repo(
    full_name: str,
    description: str,
    *,
    stars: int = 1,
    pushed_at: str = "2026-01-01T00:00:00Z",
) -> GitHubRepositoryResult:
    return GitHubRepositoryResult(
        full_name=full_name,
        html_url=f"https://github.com/{full_name}",
        description=description,
        language="Python",
        stars=stars,
        forks=1,
        open_issues=0,
        default_branch="main",
        updated_at=pushed_at,
        pushed_at=pushed_at,
        license_name="MIT",
        archived=False,
    )


def _issue(
    title: str,
    body: str,
    *,
    comments: int,
    updated_at: str,
) -> GitHubIssueResult:
    return GitHubIssueResult(
        title=title,
        html_url=f"https://github.com/owner/repo/issues/{stable_hash(title)}",
        repository_url="https://api.github.com/repos/owner/repo",
        state="open",
        comments=comments,
        created_at="2020-01-01T00:00:00Z",
        updated_at=updated_at,
        is_pull_request=False,
        body_preview=body,
    )


class _FakeGitHubClient:
    async def search_repositories(self, **kwargs):
        return {
            "total_count": 2,
            "incomplete_results": False,
            "items": [
                _repo_payload("owner/a"),
                _repo_payload("owner/b"),
            ],
        }

    async def search_issues(self, **kwargs):
        return {
            "total_count": 2,
            "incomplete_results": False,
            "items": [
                _issue_payload("first"),
                _issue_payload("second"),
            ],
        }

    async def get_repository(self, *, owner: str, repo: str):
        return _repo_payload(f"{owner}/{repo}")

    async def get_readme(self, *, owner: str, repo: str):
        return {
            "encoding": "base64",
            "size": 12,
            "download_url": "https://example.com/readme",
            "content": "SGVsbG8=",
        }

    async def get_releases(self, **kwargs):
        return [
            {
                "name": "v1",
                "tag_name": "v1.0.0",
                "html_url": "https://github.com/owner/a/releases/v1",
                "published_at": "2026-01-01T00:00:00Z",
            }
        ]

    async def close(self) -> None:
        return None


def _repo_payload(full_name: str) -> dict:
    return {
        "full_name": full_name,
        "html_url": f"https://github.com/{full_name}",
        "description": "Python package",
        "language": "Python",
        "stargazers_count": 1,
        "forks_count": 1,
        "open_issues_count": 0,
        "default_branch": "main",
        "updated_at": "2026-01-01T00:00:00Z",
        "pushed_at": "2026-01-01T00:00:00Z",
        "license": {"spdx_id": "MIT"},
        "archived": False,
    }


def _issue_payload(title: str) -> dict:
    return {
        "title": title,
        "html_url": f"https://github.com/owner/a/issues/{stable_hash(title)}",
        "repository_url": "https://api.github.com/repos/owner/a",
        "state": "open",
        "comments": 0,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "body": title,
    }


async def _run_async_tests() -> None:
    await _test_search_operations_call_ranking()
    await _test_get_operations_do_not_call_ranking()


def main() -> None:
    test_tokenize_for_bm25_english_and_chinese()
    test_rank_documents_by_bm25_edges_and_scores()
    test_rank_documents_by_bm25_cache_hit_and_fingerprint_rebuild()
    test_fielded_bm25_weights_and_stable_order()
    test_weighted_rrf_and_url_canonicalization()
    test_web_search_url_dedup_uses_canonical_url()
    test_repository_ranking_relevance_archived_and_recency()
    test_issue_ranking_relevance_comments_and_updated_at()
    asyncio.run(_run_async_tests())
    print("ranking migration tests passed")


if __name__ == "__main__":
    main()
