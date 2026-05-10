"""
Focused web_search unit tests.

Usage:
    uv run python test/test_web_search_unit.py
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from chat.application.web_search.cache import SearchCache, make_search_cache_key
from chat.application.web_search.models import ImageResult, SearchResponse, SearchResult
from chat.application.web_search.models.helpers import has_response_content
from chat.application.web_search.search_coordinator import (
    DEFAULT_FINAL_RESULTS,
    DEFAULT_MAX_PER_DOMAIN,
    PAID_FALLBACK_LIMIT,
    SearchCoordinator,
    _merge_many_search_responses,
    _normalize_queries,
    _normalize_url_for_dedup,
)
from chat.application.web_search.utils import add_note, extract_domain, has_site_operator, normalize_bool, normalize_int
from chat.application.web_search.utils.domains import _filter_results_by_domains
from chat.core.config.app_settings import settings


def _load_web_search_tool_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "chat"
        / "application"
        / "tools"
        / "web_search_tool.py"
    )
    spec = importlib.util.spec_from_file_location("web_search_tool_under_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load web_search_tool module from {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WEB_SEARCH_TOOL_MODULE = _load_web_search_tool_module()
WebSearchTool = WEB_SEARCH_TOOL_MODULE.WebSearchTool


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def make_result(
    query: str,
    index: int,
    *,
    domain: str = "example.com",
    path: Optional[str] = None,
) -> SearchResult:
    path = path or f"/{query.replace(' ', '-')}/{index}"
    return SearchResult(
        title=f"{query} result {index}",
        url=f"https://{domain}{path}",
        snippet=f"snippet {index}",
    )


class CountingSearcher:
    def __init__(
        self,
        *,
        result_count: int = 1,
        empty: bool = False,
        raise_queries: Optional[Set[str]] = None,
        domain: str = "example.com",
        images: bool = False,
    ) -> None:
        self.result_count = result_count
        self.empty = empty
        self.raise_queries = raise_queries or set()
        self.domain = domain
        self.images = images
        self.calls: List[str] = []

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        self.calls.append(query)

        if query in self.raise_queries:
            raise RuntimeError(f"forced failure for {query}")

        if self.empty:
            return SearchResponse(query=query)

        results = tuple(
            make_result(query, index, domain=self.domain)
            for index in range(min(self.result_count, max_results))
        )
        images: Tuple[ImageResult, ...] = ()
        if self.images or with_images:
            images = (
                ImageResult(url=f"https://img.example.com/{query}-1.png"),
                ImageResult(url=f"https://img.example.com/{query}-1.png"),
            )

        return SearchResponse(query=query, results=results, images=images)


def make_coordinator(
    *,
    cache: Optional[SearchCache] = None,
    searxng: Optional[CountingSearcher] = None,
    duckduckgo: Optional[CountingSearcher] = None,
    tavily: Optional[CountingSearcher] = None,
    continue_on_empty: bool = True,
) -> SearchCoordinator:
    return SearchCoordinator(
        cache=cache or SearchCache(fresh_ttl=60, stale_ttl=3600, maxsize=64),
        searxng_searcher=searxng or CountingSearcher(domain="searx.example"),
        duckduckgo_searcher=duckduckgo or CountingSearcher(domain="ddg.example"),
        tavily_searcher=tavily or CountingSearcher(domain="tavily.example"),
        continue_on_empty=continue_on_empty,
    )


def test_normalize_params() -> None:
    assert_true(normalize_int("bad", default=5, minimum=1, maximum=10) == 5, "invalid int should use default")
    assert_true(normalize_int(0, default=5, minimum=1, maximum=10) == 1, "int should clamp minimum")
    assert_true(normalize_int(99, default=5, minimum=1, maximum=10) == 10, "int should clamp maximum")
    assert_true(normalize_int(7, default=5, minimum=1, maximum=10) == 7, "valid int should pass through")
    assert_true(normalize_int(None, default=5, minimum=1, maximum=10) == 5, "None should use default")

    for value in ("true", "1", "yes", "y", "True", "YES", True):
        assert_true(normalize_bool(value) is True, f"{value!r} should be true")

    for value in ("false", "0", "no", "n", "False", "NO", "maybe", "", False):
        assert_true(normalize_bool(value) is False, f"{value!r} should be false")


def test_normalize_queries() -> None:
    assert_true(_normalize_queries(["", "   ", "Python"], limit=4) == ["Python"], "empty queries should be removed")
    assert_true(
        _normalize_queries(["Python asyncio", "python   asyncio", "PYTHON ASYNCIO"], limit=4)
        == ["Python asyncio"],
        "queries should dedupe case-insensitively after whitespace normalization",
    )
    assert_true(
        _normalize_queries(["q1", "q2", "q3", "q4", "q5"], limit=4) == ["q1", "q2", "q3", "q4"],
        "queries should be limited",
    )
    assert_true(
        _normalize_queries(["  Python    asyncio   "], limit=4) == ["Python asyncio"],
        "query whitespace should collapse",
    )
    assert_true(_normalize_queries(["", "   "], limit=4) == [], "all invalid queries should return empty list")


def test_normalize_url_preserves_path_case() -> None:
    normalized = _normalize_url_for_dedup("HTTPS://WWW.Example.com/Some/Path/")
    assert_true(
        normalized == "https://example.com/Some/Path",
        f"path case should be preserved, got {normalized}",
    )


def test_normalize_url_removes_tracking_params() -> None:
    normalized = _normalize_url_for_dedup(
        "https://example.com/path?utm_source=x&fbclid=1&a=keep&utm_content=y"
    )
    assert_true(
        normalized == "https://example.com/path?a=keep",
        f"tracking params should be removed, got {normalized}",
    )


def test_normalize_url_sorts_remaining_query_params() -> None:
    normalized = _normalize_url_for_dedup("https://example.com/path?b=2&a=1&blank=")
    assert_true(
        normalized == "https://example.com/path?a=1&b=2&blank=",
        f"remaining query params should be sorted, got {normalized}",
    )


def test_normalize_url_removes_default_port() -> None:
    assert_true(
        _normalize_url_for_dedup("http://www.example.com:80/Path")
        == "http://example.com/Path",
        "http default port should be removed",
    )
    assert_true(
        _normalize_url_for_dedup("https://www.example.com:443/Path")
        == "https://example.com/Path",
        "https default port should be removed",
    )


def test_normalize_url_keeps_non_default_port() -> None:
    normalized = _normalize_url_for_dedup("https://www.example.com:8443/Path")
    assert_true(
        normalized == "https://example.com:8443/Path",
        f"non-default port should be kept, got {normalized}",
    )


def test_normalize_queries_removes_empty_values() -> None:
    assert_true(
        _normalize_queries(["", "   ", "Python"], limit=4) == ["Python"],
        "empty values should be removed",
    )


def test_normalize_queries_deduplicates_case_insensitively() -> None:
    assert_true(
        _normalize_queries(["Python asyncio", "python   asyncio"], limit=4)
        == ["Python asyncio"],
        "queries should deduplicate case-insensitively",
    )


def test_normalize_queries_limits_count() -> None:
    notes: List[str] = []
    normalized = _normalize_queries(["q1", "q2", "q3", "q4", "q5"], limit=4, notes=notes)
    assert_true(normalized == ["q1", "q2", "q3", "q4"], "queries should be limited")
    assert_true("Search queries were limited to 4 focused queries." in notes, "limit should add a note")


def test_normalize_queries_notes_duplicate_queries() -> None:
    notes: List[str] = []
    normalized = _normalize_queries(
        ["Python asyncio", "python   asyncio", "PYTHON ASYNCIO"],
        limit=4,
        notes=notes,
    )

    assert_true(normalized == ["Python asyncio"], "duplicate queries should be removed")
    assert_true(
        "2 duplicate search queries were removed." in notes,
        "duplicate queries should add a note",
    )


def test_normalize_queries_collapses_whitespace() -> None:
    assert_true(
        _normalize_queries(["  Python    asyncio   "], limit=4) == ["Python asyncio"],
        "query whitespace should collapse",
    )


def test_truncate_query_adds_note() -> None:
    notes: List[str] = []
    long_query = ("alpha " * 100).strip()
    normalized = _normalize_queries([long_query], limit=4, notes=notes)

    assert_true(len(normalized[0]) <= 400, "long query should be truncated")
    assert_true(
        "Query truncated to 400 characters." in notes,
        "query truncation should add a concise note",
    )
    assert_true(long_query not in " ".join(notes), "notes should not contain the full original query")


def test_time_range_year_appends_current_year() -> None:
    assert_true(
        _normalize_queries(
            ["openai model release"],
            limit=4,
            time_range="year",
            current_year=2026,
        )
        == ["openai model release 2026"],
        "year time_range should append current year when no time clue exists",
    )


def test_time_range_day_does_not_append_year() -> None:
    assert_true(
        _normalize_queries(
            ["openai model release"],
            limit=4,
            time_range="day",
            current_year=2026,
        )
        == ["openai model release"],
        "day time_range should not append current year",
    )


def test_query_with_existing_year_does_not_append_year() -> None:
    assert_true(
        _normalize_queries(
            ["openai model release 2025"],
            limit=4,
            time_range="year",
            current_year=2026,
        )
        == ["openai model release 2025"],
        "query with existing year should not append another year",
    )


def test_query_with_yesterday_does_not_append_year() -> None:
    assert_true(
        _normalize_queries(
            ["openai release yesterday"],
            limit=4,
            time_range="month",
            current_year=2026,
        )
        == ["openai release yesterday"],
        "query with yesterday should not append current year",
    )


async def test_single_query_search_returns_results() -> None:
    coordinator = make_coordinator()
    response = await coordinator.search("Python asyncio gather", max_results=5)
    assert_true(response is not None, "single query should return a response")
    assert_true(len(response.results) > 0, "single query should return results")
    assert_true(response.source == "searxng", f"expected searxng source, got {response.source}")


async def test_search_many_returns_merged_results() -> None:
    coordinator = make_coordinator(searxng=CountingSearcher(result_count=3, domain="docs.example"))
    response = await coordinator.search_many(
        queries=[
            "Python asyncio gather vs wait",
            "site:docs.python.org asyncio",
            "asyncio return_when FIRST_COMPLETED",
        ],
        max_results_per_query=5,
        final_max_results=DEFAULT_FINAL_RESULTS,
    )

    assert_true(response.source is not None and response.source.startswith("multi"), "source should be multi")
    assert_true(0 < len(response.results) <= DEFAULT_FINAL_RESULTS, "merged result count should respect final limit")


async def test_one_query_failure_does_not_break_others() -> None:
    class PartlyFailingCoordinator(SearchCoordinator):
        async def search(self, query: str, **kwargs: Any) -> SearchResponse:  # type: ignore[override]
            if "fail" in query:
                raise RuntimeError("forced gather failure")
            return SearchResponse(
                query=query,
                results=(make_result(query, 1, domain="parallel.example"),),
                source="fake",
            )

    coordinator = PartlyFailingCoordinator(
        cache=SearchCache(fresh_ttl=60, stale_ttl=3600, maxsize=64),
        searxng_searcher=CountingSearcher(),
        duckduckgo_searcher=CountingSearcher(),
        tavily_searcher=CountingSearcher(),
    )

    response = await coordinator.search_many(
        queries=["succeed one", "fail this", "succeed two"],
        allow_paid_fallback=False,
    )

    assert_true(len(response.results) == 2, "failed query should not block successful query results")


def test_merge_deduplicates_urls_domains_and_images() -> None:
    response = _merge_many_search_responses(
        query="merged",
        responses=[
            SearchResponse(
                query="q1",
                results=(
                    make_result("q1", 1, domain="same.example", path="/a"),
                    make_result("q1", 2, domain="same.example", path="/b"),
                    make_result("q1", 3, domain="same.example", path="/c"),
                ),
                images=(
                    ImageResult(url="https://img.example/a.png"),
                    ImageResult(url="https://img.example/a.png"),
                ),
                source="searxng",
            ),
            SearchResponse(
                query="q2",
                results=(make_result("q2", 1, domain="other.example", path="/a"),),
                images=(ImageResult(url="https://img.example/b.png"),),
                source="duckduckgo",
            ),
        ],
        final_max_results=10,
        dedupe_domains=True,
        max_per_domain=DEFAULT_MAX_PER_DOMAIN,
    )

    urls = [result.url for result in response.results]
    assert_true(len(urls) == len(set(urls)), "merged results should have unique URLs")

    domain_counts: Dict[str, int] = {}
    for result in response.results:
        domain = extract_domain(result.url)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
    assert_true(all(count <= DEFAULT_MAX_PER_DOMAIN for count in domain_counts.values()), "domain counts should be bounded")

    image_urls = [image.url for image in response.images]
    assert_true(len(image_urls) == len(set(image_urls)), "merged images should have unique URLs")
    assert_true(response.source == "multi:duckduckgo,searxng", f"unexpected source {response.source}")


def test_merge_reports_deduplication_notes() -> None:
    notes: List[str] = []
    response = _merge_many_search_responses(
        query="dedupe",
        responses=[
            SearchResponse(
                query="q1",
                results=(
                    SearchResult(
                        title="A",
                        url="https://www.example.com/Path?utm_source=x&a=1",
                        snippet="one",
                    ),
                    SearchResult(
                        title="B",
                        url="https://example.com/Path?a=1",
                        snippet="two",
                    ),
                ),
                images=(
                    ImageResult(url="https://www.img.example.com/p.png?utm_source=x"),
                    ImageResult(url="https://img.example.com/p.png"),
                ),
            )
        ],
        final_max_results=10,
        dedupe_domains=False,
        max_per_domain=2,
        notes=notes,
    )

    assert_true(len(response.results) == 1, "normalized duplicate URLs should be merged")
    assert_true(len(response.images) == 1, "normalized duplicate image URLs should be merged")
    assert_true("1 duplicate URLs were removed." in notes, "URL dedupe should report count")
    assert_true("1 duplicate image URLs were removed." in notes, "image dedupe should report count")


def test_merge_can_disable_domain_deduplication() -> None:
    response = _merge_many_search_responses(
        query="site specific",
        responses=[
            SearchResponse(
                query="q1",
                results=(
                    make_result("q1", 1, domain="same.example", path="/a"),
                    make_result("q1", 2, domain="same.example", path="/b"),
                    make_result("q1", 3, domain="same.example", path="/c"),
                ),
            )
        ],
        final_max_results=10,
        dedupe_domains=False,
        max_per_domain=2,
    )

    assert_true(len(response.results) == 3, "dedupe_domains=False should allow more same-domain results")


async def test_site_operator_disables_domain_dedupe_when_not_explicit() -> None:
    coordinator = make_coordinator(searxng=CountingSearcher(result_count=3, domain="docs.python.org"))
    response = await coordinator.search_many(
        queries=["site:docs.python.org asyncio"],
        dedupe_domains=False,
        final_max_results=10,
    )

    assert_true(len(response.results) == 3, "site: with dedupe_domains=False should keep all results")


async def test_site_operator_does_not_match_website_or_offsite() -> None:
    coordinator = make_coordinator(searxng=CountingSearcher(result_count=3, domain="docs.python.org"))
    website_response = await coordinator.search_many(
        queries=["website:docs.python.org asyncio"],
        final_max_results=10,
    )
    offsite_response = await coordinator.search_many(
        queries=["offsite:docs.python.org asyncio"],
        final_max_results=10,
    )

    assert_true(has_site_operator(["website:docs.python.org"]) is False, "website: should not match site:")
    assert_true(has_site_operator(["offsite:docs.python.org"]) is False, "offsite: should not match site:")
    assert_true(len(website_response.results) == 2, "website: should keep default domain dedupe")
    assert_true(len(offsite_response.results) == 2, "offsite: should keep default domain dedupe")


async def test_explicit_dedupe_domains_true_is_respected() -> None:
    coordinator = make_coordinator(searxng=CountingSearcher(result_count=3, domain="docs.python.org"))
    response = await coordinator.search_many(
        queries=["site:docs.python.org asyncio"],
        dedupe_domains=True,
        final_max_results=10,
    )

    assert_true(len(response.results) == 2, "explicit dedupe_domains=True should be respected")


def test_include_domains_filters_by_hostname() -> None:
    results = (
        make_result("q", 1, domain="example.com"),
        make_result("q", 2, domain="docs.example.com"),
        make_result("q", 3, domain="notexample.com"),
    )

    filtered = _filter_results_by_domains(
        results,
        include_domains=["example.com"],
    )

    urls = [result.url for result in filtered]
    assert_true("https://example.com/q/1" in urls, "include should keep exact hostname")
    assert_true("https://docs.example.com/q/2" in urls, "include should keep subdomain")
    assert_true("https://notexample.com/q/3" not in urls, "include should not use string contains")


def test_exclude_domains_filters_by_hostname() -> None:
    results = (
        make_result("q", 1, domain="example.com"),
        make_result("q", 2, domain="docs.example.com"),
        make_result("q", 3, domain="notexample.com"),
    )

    filtered = _filter_results_by_domains(
        results,
        exclude_domains=["example.com"],
    )

    urls = [result.url for result in filtered]
    assert_true("https://example.com/q/1" not in urls, "exclude should remove exact hostname")
    assert_true("https://docs.example.com/q/2" not in urls, "exclude should remove subdomain")
    assert_true("https://notexample.com/q/3" in urls, "exclude should not use string contains")


def test_merge_include_domains_adds_note() -> None:
    notes: List[str] = []
    _merge_many_search_responses(
        query="include",
        responses=[
            SearchResponse(
                query="q",
                results=(
                    make_result("q", 1, domain="example.com"),
                    make_result("q", 2, domain="docs.example.com"),
                    make_result("q", 3, domain="notexample.com"),
                ),
            )
        ],
        final_max_results=10,
        dedupe_domains=False,
        max_per_domain=2,
        notes=notes,
        include_domains=["example.com"],
    )

    assert_true(
        "include_domains filter reduced results from 3 to 2." in notes,
        "include_domains filtering should add a count note",
    )


def test_merge_exclude_domains_adds_note() -> None:
    notes: List[str] = []
    _merge_many_search_responses(
        query="exclude",
        responses=[
            SearchResponse(
                query="q",
                results=(
                    make_result("q", 1, domain="example.com"),
                    make_result("q", 2, domain="docs.example.com"),
                    make_result("q", 3, domain="notexample.com"),
                ),
            )
        ],
        final_max_results=10,
        dedupe_domains=False,
        max_per_domain=2,
        notes=notes,
        exclude_domains=["example.com"],
    )

    assert_true(
        "exclude_domains filter removed 2 results." in notes,
        "exclude_domains filtering should add a count note",
    )


async def test_search_many_does_not_call_tavily_by_default() -> None:
    tavily = CountingSearcher(domain="tavily.example")
    coordinator = make_coordinator(
        searxng=CountingSearcher(empty=True),
        duckduckgo=CountingSearcher(empty=True),
        tavily=tavily,
    )

    response = await coordinator.search_many(
        queries=["query one", "query two"],
        allow_paid_fallback=False,
    )

    assert_true(len(tavily.calls) == 0, "multi-query search should not call Tavily by default")
    assert_true(len(response.results) == 0, "empty free results should return an empty merged response")


async def test_search_many_calls_tavily_at_most_once_when_allowed() -> None:
    tavily = CountingSearcher(result_count=2, domain="tavily.example")
    coordinator = make_coordinator(
        searxng=CountingSearcher(empty=True),
        duckduckgo=CountingSearcher(empty=True),
        tavily=tavily,
    )

    notes: List[str] = []
    response = await coordinator.search_many(
        queries=["query one", "query two"],
        allow_paid_fallback=True,
        notes=notes,
    )

    assert_true(len(tavily.calls) <= PAID_FALLBACK_LIMIT, "paid fallback should be capped at one call")
    assert_true(response.source == "multi:tavily", f"unexpected source {response.source}")
    assert_true("Tavily paid fallback was used once." in notes, "paid fallback should add a note")


async def test_empty_tavily_paid_fallback_does_not_add_note() -> None:
    tavily = CountingSearcher(empty=True, domain="tavily.example")
    coordinator = make_coordinator(
        searxng=CountingSearcher(empty=True),
        duckduckgo=CountingSearcher(empty=True),
        tavily=tavily,
    )
    notes: List[str] = []

    response = await coordinator.search_many(
        queries=["query one", "query two"],
        allow_paid_fallback=True,
        notes=notes,
    )

    assert_true(len(tavily.calls) <= PAID_FALLBACK_LIMIT, "paid fallback should still be capped")
    assert_true(len(response.results) == 0, "empty paid fallback should not add results")
    assert_true(
        "Tavily paid fallback was used once." not in notes,
        "empty paid fallback should not add a model-facing note",
    )


async def test_tavily_paid_fallback_does_not_pollute_query_cache() -> None:
    cache = SearchCache(fresh_ttl=60, stale_ttl=3600, maxsize=64)
    tavily = CountingSearcher(result_count=1, domain="tavily.example")
    coordinator = make_coordinator(
        cache=cache,
        searxng=CountingSearcher(empty=True),
        duckduckgo=CountingSearcher(empty=True),
        tavily=tavily,
    )

    await coordinator.search_many(
        queries=["unique query for cache test", "another query"],
        allow_paid_fallback=True,
    )

    key = make_search_cache_key(
        query="unique query for cache test",
        max_results=5,
        with_images=False,
    )
    cached = await cache.get_fresh(key)
    assert_true(cached is None, "paid fallback result should not be written to the ordinary query cache")


async def test_freshness_required_skips_stale_cache() -> None:
    cache = SearchCache(fresh_ttl=0.01, stale_ttl=60, maxsize=64)
    key = make_search_cache_key(
        query="latest query one",
        max_results=5,
        with_images=False,
    )
    await cache.set(
        key,
        SearchResponse(
            query="latest query one",
            results=(make_result("latest query one", 1, domain="stale.example"),),
            source="searxng",
        ),
    )
    await asyncio.sleep(0.02)

    coordinator = make_coordinator(
        cache=cache,
        searxng=CountingSearcher(empty=True),
        duckduckgo=CountingSearcher(empty=True),
        tavily=CountingSearcher(empty=True),
    )
    response = await coordinator.search_many(
        queries=["latest query one"],
        freshness_required=True,
        allow_paid_fallback=False,
    )

    assert_true("stale_cache" not in (response.source or ""), "freshness_required should not return stale_cache source")
    assert_true(len(response.results) == 0, "stale result should be skipped when freshness is required")


async def test_all_queries_fail_returns_empty_response() -> None:
    coordinator = make_coordinator(
        searxng=CountingSearcher(raise_queries={"fail one", "fail two"}),
        duckduckgo=CountingSearcher(raise_queries={"fail one", "fail two"}),
        tavily=CountingSearcher(raise_queries={"fail one", "fail two"}),
    )

    response = await coordinator.search_many(
        queries=["fail one", "fail two"],
        allow_paid_fallback=False,
    )

    assert_true(response.source == "multi", f"all-fail response should keep source=multi, got {response.source}")
    assert_true(len(response.results) == 0, "all failed queries should return empty results")


class FakeFetcher:
    def __init__(self, value: Any) -> None:
        self.value = value
        self.calls: List[str] = []

    async def fetch(self, url: str) -> Any:
        self.calls.append(url)
        return self.value(url) if callable(self.value) else self.value


async def test_fetch_top_pages_respects_limit() -> None:
    fetcher = FakeFetcher(lambda url: f"# Page\n\n{url}\n" + ("x" * 1000))
    tool = WebSearchTool(coordinator=object(), fetcher=fetcher)  # type: ignore[arg-type]
    response = SearchResponse(
        query="fetch",
        results=(
            make_result("fetch", 1, domain="pages.example"),
            make_result("fetch", 2, domain="pages.example"),
            make_result("fetch", 3, domain="pages.example"),
        ),
    )

    contents = await tool._fetch_top_pages(
        response,
        session_id="session-fetch-limit",
        limit=2,
        max_chars_per_page=500,
    )

    assert_true(len(fetcher.calls) == 2, "fetch_top_pages should only fetch the requested top pages")
    assert_true(len(contents) == 2, "fetch_top_pages should return two fetched page bodies")


async def test_fetch_top_pages_binds_result_index() -> None:
    fetcher = FakeFetcher(lambda url: f"# Page\n\n{url}")
    tool = WebSearchTool(coordinator=object(), fetcher=fetcher)  # type: ignore[arg-type]
    response = SearchResponse(
        query="fetch",
        results=(
            make_result("fetch", 1, domain="pages.example"),
            make_result("fetch", 2, domain="pages.example"),
        ),
    )

    contents = await tool._fetch_top_pages(
        response,
        session_id="session-result-index",
        limit=2,
        max_chars_per_page=500,
    )

    assert_true("--- Fetched page for result #1 ---" in contents[0], "first page should bind result index")
    assert_true("--- Fetched page for result #2 ---" in contents[1], "second page should bind result index")
    assert_true("Title:" in contents[0] and "URL:" in contents[0] and "Content:" in contents[0], "page output should include metadata")


async def test_fetch_top_pages_skips_non_text_result() -> None:
    fetcher = FakeFetcher(object())
    tool = WebSearchTool(coordinator=object(), fetcher=fetcher)  # type: ignore[arg-type]
    response = SearchResponse(
        query="fetch",
        results=(make_result("fetch", 1, domain="pages.example"),),
    )
    notes: List[str] = []

    contents = await tool._fetch_top_pages(
        response,
        session_id="session-non-str",
        limit=1,
        max_chars_per_page=500,
        notes=notes,
    )

    assert_true(contents == [], "non-string fetched content should be skipped")
    assert_true(len(fetcher.calls) == 1, "fetcher should still have been called once")
    assert_true(
        "Fetched page for result #1 was skipped because it returned non-text content." in notes,
        "non-text fetch should add a concise note",
    )


async def test_fetch_top_pages_timeout_adds_note() -> None:
    class SlowFetcher:
        async def fetch(self, url: str) -> str:
            await asyncio.sleep(0.05)
            return "too late"

    tool = WebSearchTool(coordinator=object(), fetcher=SlowFetcher())  # type: ignore[arg-type]
    response = SearchResponse(
        query="fetch",
        results=(make_result("fetch", 1, domain="pages.example"),),
    )
    notes: List[str] = []

    contents = await tool._fetch_top_pages(
        response,
        session_id="session-timeout",
        limit=1,
        max_chars_per_page=500,
        timeout_seconds=0.01,
        notes=notes,
    )

    assert_true(contents == [], "timed out page should be skipped")
    assert_true(
        "Fetched page for result #1 was skipped because it timed out." in notes,
        "timeout should add a concise note",
    )


async def test_fetch_top_pages_respects_max_chars() -> None:
    fetcher = FakeFetcher("x" * 1000 + "TAIL")
    tool = WebSearchTool(coordinator=object(), fetcher=fetcher)  # type: ignore[arg-type]
    response = SearchResponse(
        query="fetch",
        results=(make_result("fetch", 1, domain="pages.example"),),
    )

    contents = await tool._fetch_top_pages(
        response,
        session_id="session-max-chars",
        limit=1,
        max_chars_per_page=500,
    )

    assert_true(len(contents) == 1, "one page should be fetched")
    assert_true("TAIL" not in contents[0], "fetched page content should respect max_chars_per_page")


async def test_tool_execute_modes() -> None:
    class FakeCoordinator:
        async def search(self, query: str, **kwargs: Any) -> SearchResponse:
            return SearchResponse(
                query=query,
                results=(make_result(query, 1, domain="precise.example"),),
                source="searxng",
            )

        async def search_many(self, queries: List[str], **kwargs: Any) -> SearchResponse:
            return SearchResponse(
                query=" | ".join(queries),
                results=tuple(make_result(query, 1, domain="broad.example") for query in queries),
                source="multi:searxng",
            )

    tool = WebSearchTool(
        coordinator=FakeCoordinator(),  # type: ignore[arg-type]
        fetcher=FakeFetcher(lambda url: "# fetched\n\nbody"),
    )

    precise = await tool.execute({"session_id": "mode-precise"}, query="one")
    broad = await tool.execute({"session_id": "mode-broad"}, queries=["one", "two"])
    deep = await tool.execute(
        {"session_id": "mode-deep"},
        queries=["one", "two"],
        fetch_top_pages=True,
        fetch_top_pages_limit=1,
        fetched_page_max_chars=500,
    )

    assert_true("Mode: precise" in precise, "single query should format as precise mode")
    assert_true("Mode: broad" in broad, "multi-query should format as broad mode")
    assert_true("Mode: deep" in deep, "fetch_top_pages should format as deep mode")
    assert_true("Fetched top pages:" in deep, "deep mode should include fetched top pages")


def test_format_response_contains_evidence_pack_sections() -> None:
    response = SearchResponse(
        query="evidence",
        results=(
            SearchResult(
                title="Evidence title",
                url="https://example.com/evidence",
                snippet="Evidence snippet",
                images=(
                    ImageResult(url="https://example.com/result-image-1.png"),
                    ImageResult(url="https://example.com/result-image-2.png"),
                    ImageResult(url="https://example.com/result-image-3.png"),
                ),
            ),
        ),
        images=(
            ImageResult(url="https://example.com/query-image-1.png"),
            ImageResult(url="https://example.com/query-image-2.png"),
        ),
        source="multi:searxng",
    )

    formatted = WEB_SEARCH_TOOL_MODULE._format_response(
        response,
        mode="deep",
        queries=["evidence"],
        notes=["A concise note."],
        extra_contents=[
            "--- Fetched page for result #1 ---\nTitle: Evidence title\nURL: https://example.com/evidence\nContent:\nbody"
        ],
    )

    assert_true("[Tool Result] Web search evidence pack" in formatted, "title should identify evidence pack")
    assert_true("Mode: deep" in formatted, "mode should be included")
    assert_true("Queries:" in formatted and "- evidence" in formatted, "queries should be listed")
    assert_true("Source: multi:searxng" in formatted, "source should be included")
    assert_true("Summary:" in formatted, "summary should be included")
    assert_true("Notes:" in formatted and "- A concise note." in formatted, "notes should be included after summary")
    assert_true("Results:" in formatted, "results section should be included")
    assert_true("Title: Evidence title" in formatted, "result title should be included")
    assert_true("Domain: example.com" in formatted, "result domain should be included")
    assert_true("URL: https://example.com/evidence" in formatted, "result URL should be included")
    assert_true("Snippet: Evidence snippet" in formatted, "result snippet should be included")
    assert_true("Query-level images:" in formatted, "query-level images should be included")
    assert_true("Fetched top pages:" in formatted, "fetched pages section should be included")


def test_format_response_deduplicates_notes() -> None:
    response = SearchResponse(
        query="notes",
        results=(make_result("notes", 1),),
        source="stale_cache",
    )

    formatted = WEB_SEARCH_TOOL_MODULE._format_response(
        response,
        mode="precise",
        queries=["notes"],
        notes=[
            "Some results came from stale cache and may be outdated.",
            "Some results came from stale cache and may be outdated.",
        ],
    )

    assert_true(
        formatted.count("Some results came from stale cache and may be outdated.") == 1,
        "duplicate notes should be removed",
    )


def test_tool_schema_includes_optimization_params() -> None:
    schema = WEB_SEARCH_TOOL_MODULE._TOOL_SCHEMA
    properties = schema["properties"]

    for name in (
        "include_domains",
        "exclude_domains",
        "time_range",
        "fetched_page_max_chars",
        "fetch_page_timeout_seconds",
    ):
        assert_true(name in properties, f"{name} should be exposed in tool schema")

    assert_true(
        properties["time_range"]["enum"] == ["day", "week", "month", "year"],
        "time_range should expose the supported freshness windows",
    )


def test_tool_description_contains_query_generation_guidance() -> None:
    description = WEB_SEARCH_TOOL_MODULE._TOOL_DESCRIPTION

    assert_true("Query generation guidance:" in description, "description should guide query generation")
    assert_true("Generate 2-4 concise focused search-engine-style queries." in description, "description should cap query count")
    assert_true("exact error message keywords" in description, "description should include debugging query pattern")


def test_format_response_truncates_long_output() -> None:
    response = SearchResponse(
        query="long",
        results=(
            SearchResult(
                title="Long result",
                url="https://example.com/long",
                snippet="x" * (settings.TOOL_RESULT_MAX_CHARS + 1000),
            ),
        ),
        source="searxng",
    )

    formatted = WEB_SEARCH_TOOL_MODULE._format_response(
        response,
        mode="precise",
        queries=["long"],
    )

    assert_true(
        len(formatted) <= settings.TOOL_RESULT_MAX_CHARS,
        "formatted output should be capped by TOOL_RESULT_MAX_CHARS",
    )
    assert_true("...(Search result truncated due to length)" in formatted, "truncation marker should be included")


def test_add_note_strips_whitespace() -> None:
    notes: List[str] = []
    add_note(notes, "  hello  ")
    assert_true(notes == ["hello"], f"add_note should strip whitespace, got {notes}")


def test_add_note_skips_empty() -> None:
    notes: List[str] = []
    add_note(notes, "")
    add_note(notes, "   ")
    assert_true(notes == [], "add_note should skip empty notes")


def test_add_note_skips_none_list() -> None:
    add_note(None, "test")
    assert_true(True, "add_note should not raise when notes is None")


def test_has_response_content_with_answer() -> None:
    response = SearchResponse(query="q", answer="yes")
    assert_true(has_response_content(response) is True, "answer should count as content")


def test_has_response_content_with_results() -> None:
    response = SearchResponse(
        query="q",
        results=(SearchResult(title="t", url="https://example.com", snippet="s"),),
    )
    assert_true(has_response_content(response) is True, "results should count as content")


def test_has_response_content_with_images() -> None:
    response = SearchResponse(query="q", images=(ImageResult(url="https://img.example.com/a.png"),))
    assert_true(has_response_content(response) is True, "images should count as content")


def test_has_response_content_empty() -> None:
    response = SearchResponse(query="q")
    assert_true(has_response_content(response) is False, "empty response should have no content")


def test_has_site_operator_matches_site() -> None:
    assert_true(has_site_operator(["site:docs.python.org asyncio"]) is True, "site: should match")


def test_has_site_operator_no_false_positive() -> None:
    assert_true(has_site_operator(["website:docs.python.org"]) is False, "website: should not match site:")
    assert_true(has_site_operator(["offsite:docs.python.org"]) is False, "offsite: should not match site:")
    assert_true(has_site_operator(["awesome site: great"]) is True, "site: as standalone word should match")
    assert_true(has_site_operator(["no operator here"]) is False, "no site: operator should not match")


def test_normalize_url_preserves_path_case_extended() -> None:
    assert_true(
        _normalize_url_for_dedup("https://example.com/API/v2/Users") == "https://example.com/API/v2/Users",
        "path case should be preserved exactly",
    )


async def main() -> int:
    sync_tests = [
        test_normalize_params,
        test_normalize_queries,
        test_normalize_url_preserves_path_case,
        test_normalize_url_removes_tracking_params,
        test_normalize_url_sorts_remaining_query_params,
        test_normalize_url_removes_default_port,
        test_normalize_url_keeps_non_default_port,
        test_normalize_queries_removes_empty_values,
        test_normalize_queries_deduplicates_case_insensitively,
        test_normalize_queries_limits_count,
        test_normalize_queries_collapses_whitespace,
        test_truncate_query_adds_note,
        test_time_range_year_appends_current_year,
        test_time_range_day_does_not_append_year,
        test_query_with_existing_year_does_not_append_year,
        test_query_with_yesterday_does_not_append_year,
        test_merge_deduplicates_urls_domains_and_images,
        test_merge_reports_deduplication_notes,
        test_merge_can_disable_domain_deduplication,
        test_include_domains_filters_by_hostname,
        test_exclude_domains_filters_by_hostname,
        test_merge_include_domains_adds_note,
        test_merge_exclude_domains_adds_note,
        test_format_response_contains_evidence_pack_sections,
        test_format_response_deduplicates_notes,
        test_tool_schema_includes_optimization_params,
        test_tool_description_contains_query_generation_guidance,
        test_format_response_truncates_long_output,
        test_add_note_strips_whitespace,
        test_add_note_skips_empty,
        test_add_note_skips_none_list,
        test_has_response_content_with_answer,
        test_has_response_content_with_results,
        test_has_response_content_with_images,
        test_has_response_content_empty,
        test_has_site_operator_matches_site,
        test_has_site_operator_no_false_positive,
        test_normalize_url_preserves_path_case_extended,
    ]

    async_tests = [
        test_single_query_search_returns_results,
        test_search_many_returns_merged_results,
        test_one_query_failure_does_not_break_others,
        test_site_operator_disables_domain_dedupe_when_not_explicit,
        test_site_operator_does_not_match_website_or_offsite,
        test_explicit_dedupe_domains_true_is_respected,
        test_search_many_does_not_call_tavily_by_default,
        test_search_many_calls_tavily_at_most_once_when_allowed,
        test_empty_tavily_paid_fallback_does_not_add_note,
        test_tavily_paid_fallback_does_not_pollute_query_cache,
        test_freshness_required_skips_stale_cache,
        test_all_queries_fail_returns_empty_response,
        test_fetch_top_pages_respects_limit,
        test_fetch_top_pages_binds_result_index,
        test_fetch_top_pages_skips_non_text_result,
        test_fetch_top_pages_timeout_adds_note,
        test_fetch_top_pages_respects_max_chars,
        test_tool_execute_modes,
    ]

    passed = 0
    failed = 0

    for test in sync_tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {test.__name__}: {exc}")
            failed += 1

    for test in async_tests:
        try:
            await test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {test.__name__}: {type(exc).__name__}: {exc}")
            failed += 1

    print(f"\n{passed}/{passed + failed} tests passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
