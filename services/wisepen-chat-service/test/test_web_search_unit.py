"""
Focused web_search unit tests.

Usage:
    uv run python test/test_web_search_unit.py
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from chat.application.web_search.cache import SearchCache
from chat.application.web_search.models import ImageResult, SearchResponse, SearchResult
from chat.application.web_search.models.helpers import has_response_content
from chat.application.web_search.search_coordinator import (
    DEFAULT_FINAL_RESULTS,
    DEFAULT_MAX_PER_DOMAIN,
    SearchCoordinator,
    merge_many_search_responses,
)
from chat.application.web_search.utils import (
    add_note,
    deduplicate_images,
    extract_domain,
    has_site_operator,
    normalize_queries,
    normalize_url_for_dedup,
)
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
            for index in range(1, min(self.result_count, max_results) + 1)
        )
        images: Tuple[ImageResult, ...] = ()
        if self.images or with_images:
            images = (
                ImageResult(url=f"https://img.example.com/{query}-1.png"),
                ImageResult(url=f"https://img.example.com/{query}-1.png"),
            )

        return SearchResponse(query=query, results=results, images=images)


class FakeFetcher:
    def __init__(
        self,
        content: Union[object, Callable[[str], object]],
        *,
        delay: float = 0,
    ) -> None:
        self.content = content
        self.delay = delay
        self.calls: List[str] = []
        self.active = 0
        self.max_active = 0

    async def fetch(self, url: str) -> object:
        self.calls.append(url)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if callable(self.content):
                return self.content(url)
            return self.content
        finally:
            self.active -= 1


class RecordingCoordinator:
    def __init__(self, response: SearchResponse) -> None:
        self.response = response
        self.search_many_calls: List[Tuple[List[str], Dict[str, Any]]] = []
        self.search_calls: List[Tuple[str, Dict[str, Any]]] = []

    async def search_many(self, queries: List[str], **kwargs: Any) -> SearchResponse:
        self.search_many_calls.append((queries, kwargs))
        return self.response

    async def search(self, query: str, **kwargs: Any) -> SearchResponse:
        self.search_calls.append((query, kwargs))
        raise AssertionError("WebSearchTool should always call search_many")


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


def test_normalize_queries() -> None:
    assert_true(normalize_queries(["", "   ", "Python"], limit=4) == ["Python"], "empty queries should be removed")
    assert_true(
        normalize_queries(["Python asyncio", "python   asyncio", "PYTHON ASYNCIO"], limit=4)
        == ["Python asyncio"],
        "queries should dedupe case-insensitively after whitespace normalization",
    )
    assert_true(
        normalize_queries(["q1", "q2", "q3", "q4", "q5"], limit=4) == ["q1", "q2", "q3", "q4"],
        "queries should be limited",
    )
    assert_true(
        normalize_queries(["  Python    asyncio   "], limit=4) == ["Python asyncio"],
        "query whitespace should collapse",
    )


def test_normalize_queries_notes() -> None:
    notes: List[str] = []
    long_query = ("alpha " * 100).strip()
    normalized = normalize_queries(
        [long_query, "Python asyncio", "python   asyncio", "q3", "q4", "q5"],
        limit=4,
        notes=notes,
    )

    assert_true(len(normalized[0]) <= 400, "long query should be truncated")
    assert_true("Query truncated to 400 characters." in notes, "truncation should add note")
    assert_true("1 duplicate search queries were removed." in notes, "duplicate should add note")
    assert_true("Search queries were limited to 4 focused queries." in notes, "limit should add note")


def test_normalize_url_for_dedup() -> None:
    assert_true(
        normalize_url_for_dedup("HTTPS://WWW.Example.com/Some/Path/") == "https://example.com/Some/Path",
        "path case should be preserved and www/default slash normalized",
    )
    assert_true(
        normalize_url_for_dedup("https://example.com/path?utm_source=x&fbclid=1&a=keep")
        == "https://example.com/path?a=keep",
        "tracking params should be removed",
    )
    assert_true(
        normalize_url_for_dedup("https://www.example.com:8443/Path") == "https://example.com:8443/Path",
        "non-default port should be kept",
    )


def test_has_site_operator() -> None:
    assert_true(has_site_operator(["site:docs.python.org asyncio"]) is True, "site: should match")
    assert_true(has_site_operator(["website:docs.python.org"]) is False, "website: should not match site:")
    assert_true(has_site_operator(["offsite:docs.python.org"]) is False, "offsite: should not match site:")
    assert_true(has_site_operator(["awesome site: great"]) is True, "standalone site: should match")


def test_merge_deduplicates_urls_domains_and_images() -> None:
    response = merge_many_search_responses(
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
    response = merge_many_search_responses(
        query="dedupe",
        responses=[
            SearchResponse(
                query="q1",
                results=(
                    SearchResult(title="A", url="https://www.example.com/Path?utm_source=x&a=1", snippet="one"),
                    SearchResult(title="B", url="https://example.com/Path?a=1", snippet="two"),
                    SearchResult(title="C", url="https://example.com/Other", snippet="three"),
                    SearchResult(title="D", url="https://example.com/Third", snippet="four"),
                ),
                images=(
                    ImageResult(url="https://www.img.example.com/p.png?utm_source=x"),
                    ImageResult(url="https://img.example.com/p.png"),
                ),
            )
        ],
        final_max_results=10,
        dedupe_domains=True,
        max_per_domain=2,
        notes=notes,
    )

    assert_true(len(response.results) == 2, "URL and domain dedupe should both apply")
    assert_true(len(response.images) == 1, "normalized duplicate image URLs should be merged")
    assert_true("1 duplicate URLs were removed." in notes, "URL dedupe should report count")
    assert_true("1 duplicate image URLs were removed." in notes, "image dedupe should report count")
    assert_true("1 same-domain results were removed by domain dedupe." in notes, "domain dedupe should report count")


def test_merge_can_disable_domain_deduplication() -> None:
    response = merge_many_search_responses(
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


async def test_search_many_returns_merged_results() -> None:
    coordinator = make_coordinator(searxng=CountingSearcher(result_count=3, domain="docs.example"))
    response = await coordinator.search_many(
        queries=[
            "Python asyncio gather vs wait",
            "site:docs.python.org asyncio",
            "asyncio return_when FIRST_COMPLETED",
        ],
        max_results_per_query=8,
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

    response = await coordinator.search_many(queries=["succeed one", "fail this", "succeed two"])

    assert_true(len(response.results) == 2, "failed query should not block successful query results")


async def test_search_many_with_images() -> None:
    coordinator = make_coordinator(searxng=CountingSearcher(result_count=1, images=True))
    response = await coordinator.search_many(queries=["image query"], with_images=True)

    assert_true(len(response.images) == 1, "query-level duplicate images should be deduplicated")


async def test_search_many_never_calls_tavily() -> None:
    searxng = CountingSearcher(empty=True)
    duckduckgo = CountingSearcher(empty=True)
    tavily = CountingSearcher(result_count=1, domain="tavily.example")
    coordinator = make_coordinator(searxng=searxng, duckduckgo=duckduckgo, tavily=tavily)

    response = await coordinator.search_many(queries=["needs fallback"])

    assert_true(len(tavily.calls) == 0, "search_many should not call Tavily")
    assert_true(len(response.results) == 0, "empty free-search results should stay empty")


async def test_search_many_passes_allow_paid_fallback_false() -> None:
    class RecordingSearchCoordinator(SearchCoordinator):
        def __init__(self) -> None:
            super().__init__(
                cache=SearchCache(fresh_ttl=60, stale_ttl=3600, maxsize=64),
                searxng_searcher=CountingSearcher(),
                duckduckgo_searcher=CountingSearcher(),
                tavily_searcher=CountingSearcher(),
            )
            self.search_kwargs: List[Dict[str, Any]] = []

        async def search(self, query: str, **kwargs: Any) -> SearchResponse:  # type: ignore[override]
            self.search_kwargs.append(kwargs)
            return SearchResponse(query=query, results=(make_result(query, 1),), source="fake")

    coordinator = RecordingSearchCoordinator()
    await coordinator.search_many(queries=["one", "two"])

    assert_true(
        all(kwargs.get("allow_paid_fallback") is False for kwargs in coordinator.search_kwargs),
        "search_many should force allow_paid_fallback=False on each search call",
    )


async def test_tool_normal_mode_uses_search_many_for_one_query() -> None:
    coordinator = RecordingCoordinator(
        SearchResponse(query="one", results=(make_result("one", 1, domain="normal.example"),), source="multi:searxng")
    )
    fetcher = FakeFetcher("# Page\n\nbody")
    tool = WebSearchTool(coordinator=coordinator, fetcher=fetcher)  # type: ignore[arg-type]

    result = await tool.execute({"session_id": "normal-one"}, queries=["one"], mode="normal", with_images=True)

    assert_true("Mode: normal" in result, "normal mode should be formatted")
    assert_true(len(coordinator.search_many_calls) == 1, "tool should use search_many even for one query")
    assert_true(len(coordinator.search_calls) == 0, "tool should not use single-query search")
    queries, kwargs = coordinator.search_many_calls[0]
    assert_true(queries == ["one"], "tool should pass normalized queries")
    assert_true(kwargs["max_results_per_query"] == 8, "tool should use fixed per-query result count")
    assert_true(kwargs["final_max_results"] == 20, "tool should use fixed final result count")
    assert_true(kwargs["with_images"] is True, "with_images should be forwarded")
    assert_true("allow_paid_fallback" not in kwargs, "tool should not pass allow_paid_fallback to search_many")
    assert_true(fetcher.calls == [], "normal mode should not fetch page contents")


async def test_tool_requires_queries() -> None:
    tool = WebSearchTool(
        coordinator=RecordingCoordinator(SearchResponse(query="")),
        fetcher=FakeFetcher("# Page"),
    )  # type: ignore[arg-type]

    result = await tool.execute({"session_id": "missing"}, query="old single query")

    assert_true(result == "[Tool Error] Missing required queries parameter.", "old query parameter should not be accepted")


async def test_tool_site_operator_disables_domain_dedupe() -> None:
    coordinator = RecordingCoordinator(SearchResponse(query="site", results=(make_result("site", 1),), source="multi:searxng"))
    tool = WebSearchTool(coordinator=coordinator, fetcher=FakeFetcher("# Page"))  # type: ignore[arg-type]

    result = await tool.execute({"session_id": "site"}, queries=["site:docs.python.org asyncio"])

    kwargs = coordinator.search_many_calls[0][1]
    assert_true(kwargs["dedupe_domains"] is False, "site: should disable domain dedupe")
    assert_true(
        "Domain dedupe disabled because a site: operator was detected." in result,
        "site: should add note",
    )


async def test_tool_website_and_offsite_do_not_disable_domain_dedupe() -> None:
    coordinator = RecordingCoordinator(SearchResponse(query="site", results=(make_result("site", 1),), source="multi:searxng"))
    tool = WebSearchTool(coordinator=coordinator, fetcher=FakeFetcher("# Page"))  # type: ignore[arg-type]

    await tool.execute({"session_id": "website"}, queries=["website:docs.python.org asyncio"])
    await tool.execute({"session_id": "offsite"}, queries=["offsite:docs.python.org asyncio"])

    assert_true(
        all(call[1]["dedupe_domains"] is True for call in coordinator.search_many_calls),
        "website:/offsite: should keep default domain dedupe",
    )


async def test_deep_mode_reads_top_eight_unique_pages() -> None:
    response = SearchResponse(
        query="deep",
        results=tuple(make_result("deep", index, domain="pages.example") for index in range(1, 13)),
        source="multi:searxng",
    )
    coordinator = RecordingCoordinator(response)
    fetcher = FakeFetcher(lambda url: f"# Page\n\n{url}")
    tool = WebSearchTool(coordinator=coordinator, fetcher=fetcher)  # type: ignore[arg-type]

    result = await tool.execute({"session_id": "deep"}, queries=["deep query"], mode="deep")

    assert_true(len(fetcher.calls) == 8, "deep mode should read at most eight unique result URLs")
    assert_true("Mode: deep" in result, "deep mode should be formatted")
    assert_true("Page contents:" in result, "deep mode should include page contents")
    assert_true("--- Page content for result #9 ---" not in result, "ninth result should not be read")


async def test_deep_mode_output_shows_internal_page_fetch() -> None:
    response = SearchResponse(
        query="deep",
        results=(
            SearchResult(
                title="Python docs",
                url="https://docs.python.org/3/library/asyncio-task.html",
                snippet="asyncio tasks",
            ),
        ),
        source="multi:searxng",
    )
    coordinator = RecordingCoordinator(response)
    fetcher = FakeFetcher("# asyncio TaskGroup\n\nTaskGroup runs related tasks.")
    tool = WebSearchTool(coordinator=coordinator, fetcher=fetcher)  # type: ignore[arg-type]

    result = await tool.execute({"session_id": "deep-fetch-signal"}, queries=["asyncio TaskGroup"], mode="deep")

    assert_true(
        fetcher.calls == ["https://docs.python.org/3/library/asyncio-task.html"],
        "deep mode should call the injected web fetch coordinator for page extraction",
    )
    assert_true("Page contents:" in result, "deep output should expose that page contents were read")
    assert_true("--- Page content for result #1 ---" in result, "deep output should include fetched page block")
    assert_true("tool_name: web_search" in result, "page content should be windowed under web_search")
    assert_true(
        "Deep search was requested but no page content was read." not in result,
        "successful page fetch should not include no-content note",
    )


async def test_read_page_contents_deduplicates_normalized_urls() -> None:
    fetcher = FakeFetcher(lambda url: f"# Page\n\n{url}")
    tool = WebSearchTool(coordinator=object(), fetcher=fetcher)  # type: ignore[arg-type]
    response = SearchResponse(
        query="fetch",
        results=(
            SearchResult(title="Page one", url="https://www.example.com/Page/?utm_source=x#section", snippet="one"),
            SearchResult(title="Page two", url="https://example.com/Page", snippet="two"),
            SearchResult(title="Page three", url="https://example.com/Other", snippet="three"),
        ),
    )

    contents = await tool._read_page_contents(response, session_id="session-url-dedupe", limit=8)

    assert_true(len(fetcher.calls) == 2, "normalized duplicate URLs should only be fetched once")
    assert_true(len(contents) == 2, "normalized duplicate URLs should only produce one page content block")


async def test_read_page_contents_concurrency_limit() -> None:
    fetcher = FakeFetcher(lambda url: f"# Page\n\n{url}", delay=0.02)
    tool = WebSearchTool(coordinator=object(), fetcher=fetcher)  # type: ignore[arg-type]
    response = SearchResponse(
        query="concurrency",
        results=tuple(make_result("concurrency", index, domain="pages.example") for index in range(1, 9)),
    )

    contents = await tool._read_page_contents(response, session_id="session-concurrency", limit=8)

    assert_true(len(contents) == 8, "all pages should be read")
    assert_true(fetcher.max_active <= 5, "page content concurrency should not exceed five")


async def test_read_page_contents_failure_does_not_block_others() -> None:
    def content_for_url(url: str) -> str:
        if url.endswith("/fail"):
            raise RuntimeError("forced fetch failure")
        return f"# Page\n\n{url}"

    fetcher = FakeFetcher(content_for_url)
    tool = WebSearchTool(coordinator=object(), fetcher=fetcher)  # type: ignore[arg-type]
    response = SearchResponse(
        query="fetch",
        results=(
            make_result("fetch", 1, domain="pages.example", path="/fail"),
            make_result("fetch", 2, domain="pages.example", path="/ok"),
        ),
    )
    notes: List[str] = []

    contents = await tool._read_page_contents(response, session_id="session-failure", limit=8, notes=notes)

    assert_true(len(fetcher.calls) == 2, "one fetch failure should not stop later fetches")
    assert_true(len(contents) == 1, "only successful page content should be returned")
    assert_true("--- Page content for result #2 ---" in contents[0], "successful page should keep original result index")
    assert_true(
        "Page content for result #1 failed and was skipped." in notes,
        "fetch failure should add note",
    )


async def test_read_page_contents_skips_none_and_non_text() -> None:
    for content, expected_note in (
        (None, "Page content for result #1 returned no content and was skipped."),
        (object(), "Page content for result #1 was skipped because it returned non-text content."),
    ):
        fetcher = FakeFetcher(content)
        tool = WebSearchTool(coordinator=object(), fetcher=fetcher)  # type: ignore[arg-type]
        response = SearchResponse(query="fetch", results=(make_result("fetch", 1, domain="pages.example"),))
        notes: List[str] = []

        contents = await tool._read_page_contents(response, session_id="session-skip", limit=8, notes=notes)

        assert_true(contents == [], "invalid page content should be skipped")
        assert_true(expected_note in notes, "invalid page content should add note")


async def test_read_page_contents_timeout_adds_note() -> None:
    original_timeout = WEB_SEARCH_TOOL_MODULE.PAGE_CONTENT_TIMEOUT_SECONDS
    WEB_SEARCH_TOOL_MODULE.PAGE_CONTENT_TIMEOUT_SECONDS = 0.01

    try:
        fetcher = FakeFetcher("too late", delay=0.05)
        tool = WebSearchTool(coordinator=object(), fetcher=fetcher)  # type: ignore[arg-type]
        response = SearchResponse(query="fetch", results=(make_result("fetch", 1, domain="pages.example"),))
        notes: List[str] = []

        contents = await tool._read_page_contents(response, session_id="session-timeout", limit=8, notes=notes)
    finally:
        WEB_SEARCH_TOOL_MODULE.PAGE_CONTENT_TIMEOUT_SECONDS = original_timeout

    assert_true(contents == [], "timed out page should be skipped")
    assert_true(
        "Page content for result #1 was skipped because it timed out." in notes,
        "timeout should add note",
    )


async def test_read_page_contents_cache_window_failure_skips_only_current_page() -> None:
    fetcher = FakeFetcher(lambda url: f"# Page\n\n{url}")
    tool = WebSearchTool(coordinator=object(), fetcher=fetcher)  # type: ignore[arg-type]
    response = SearchResponse(
        query="fetch",
        results=(
            make_result("fetch", 1, domain="pages.example", path="/fail"),
            make_result("fetch", 2, domain="pages.example", path="/ok"),
        ),
    )
    notes: List[str] = []

    original_cache_and_window = WEB_SEARCH_TOOL_MODULE.cache_and_window

    def failing_once_cache_and_window(**kwargs: Any) -> Any:
        if kwargs["source"].endswith("/fail"):
            raise RuntimeError("forced cache failure")
        return original_cache_and_window(**kwargs)

    WEB_SEARCH_TOOL_MODULE.cache_and_window = failing_once_cache_and_window
    try:
        contents = await tool._read_page_contents(response, session_id="session-cache-failure", limit=8, notes=notes)
    finally:
        WEB_SEARCH_TOOL_MODULE.cache_and_window = original_cache_and_window

    assert_true(len(fetcher.calls) == 2, "cache failure on one page should not stop later fetches")
    assert_true(len(contents) == 1, "only the successful page should be returned")
    assert_true("--- Page content for result #2 ---" in contents[0], "successful page should keep original result index")
    assert_true(
        "Page content for result #1 failed during content processing and was skipped." in notes,
        "cache/window failure should add note",
    )


async def test_read_page_contents_output_binds_result_index() -> None:
    fetcher = FakeFetcher(lambda url: f"# Page\n\n{url}")
    tool = WebSearchTool(coordinator=object(), fetcher=fetcher)  # type: ignore[arg-type]
    response = SearchResponse(
        query="fetch",
        results=(
            SearchResult(title="Empty URL", url="", snippet="skip"),
            make_result("fetch", 2, domain="pages.example", path="/second"),
        ),
    )

    contents = await tool._read_page_contents(response, session_id="session-result-index", limit=8)

    assert_true(len(contents) == 1, "one valid page should be returned")
    assert_true("--- Page content for result #2 ---" in contents[0], "page output should bind original result index")
    assert_true("Title:" in contents[0] and "URL:" in contents[0] and "Content:" in contents[0], "page output should include metadata")


async def test_tool_deep_without_content_adds_note() -> None:
    coordinator = RecordingCoordinator(
        SearchResponse(query="deep", results=(make_result("deep", 1, domain="empty-fetch.example"),), source="multi:searxng")
    )
    tool = WebSearchTool(coordinator=coordinator, fetcher=FakeFetcher(None))  # type: ignore[arg-type]

    result = await tool.execute({"session_id": "fetch-empty-note"}, queries=["needs fetch"], mode="deep")

    assert_true(
        "Deep search was requested but no page content was read." in result,
        "empty deep page content should add note",
    )
    assert_true("Page contents:" not in result, "empty deep page content should not add section")


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
        page_contents=[
            "--- Page content for result #1 ---\nTitle: Evidence title\nURL: https://example.com/evidence\nContent:\nbody"
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
    assert_true("Page contents:" in formatted, "page contents section should be included")
    assert_true("Fetched top pages:" not in formatted, "old fetched-page section name should be removed")


def test_format_response_deduplicates_notes() -> None:
    response = SearchResponse(
        query="notes",
        results=(make_result("notes", 1),),
        source="stale_cache",
    )

    formatted = WEB_SEARCH_TOOL_MODULE._format_response(
        response,
        mode="normal",
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


def test_tool_schema_only_exposes_final_params() -> None:
    schema = WEB_SEARCH_TOOL_MODULE._TOOL_SCHEMA
    properties = schema["properties"]

    assert_true(set(properties) == {"queries", "mode", "with_images"}, "schema should only expose final params")
    assert_true(schema["required"] == ["queries"], "queries should be required")
    assert_true(properties["queries"]["minItems"] == 1, "queries minItems should be one")
    assert_true(properties["queries"]["maxItems"] == 4, "queries maxItems should be four")
    assert_true(properties["mode"]["enum"] == ["normal", "deep"], "mode should only support normal/deep")


def test_tool_description_contains_new_guidance() -> None:
    description = WEB_SEARCH_TOOL_MODULE._TOOL_DESCRIPTION

    assert_true("concurrent multi-query search" in description, "description should describe concurrent multi-query search")
    assert_true("normal" in description and "deep" in description, "description should describe both modes")
    assert_true(
        "prefer one web_search call with 2-4 queries" in description,
        "description should discourage multiple web_search calls for one research task",
    )
    assert_true("Tavily paid fallback is disabled" in description, "description should state cost policy")
    assert_true("query" not in WEB_SEARCH_TOOL_MODULE._TOOL_SCHEMA["properties"], "old query param should be removed")


def test_removed_old_page_reader_name() -> None:
    assert_true(not hasattr(WebSearchTool, "_fetch_top_pages"), "old _fetch_top_pages name should be removed")


def test_format_response_does_not_truncate_long_output() -> None:
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
        mode="normal",
        queries=["long"],
    )

    assert_true(
        len(formatted) > settings.TOOL_RESULT_MAX_CHARS,
        "raw formatted output should remain complete before tool-content windowing",
    )
    assert_true("...(Search result truncated due to length)" not in formatted, "raw formatting should not use bare truncation")


async def test_tool_long_output_uses_tool_content_window() -> None:
    response = SearchResponse(
        query="long",
        results=tuple(
            SearchResult(
                title=f"Long result {index}",
                url=f"https://long.example/{index}",
                snippet="x" * 800,
            )
            for index in range(1, 9)
        ),
        source="multi:searxng",
    )
    coordinator = RecordingCoordinator(response)
    tool = WebSearchTool(coordinator=coordinator, fetcher=FakeFetcher("PAGE_BODY_SENTINEL " + ("body " * 900)))  # type: ignore[arg-type]

    result = await tool.execute({"session_id": "long-window"}, queries=["long"], mode="deep")

    assert_true("[ToolContent Metadata]" in result, "long web_search output should return ToolContent Metadata")
    assert_true("tool_name: web_search" in result, "windowed output should be readable through tool_content_read")
    assert_true("content_cached: true" in result, "long web_search output should be cached")
    assert_true("truncated: true" in result, "long web_search output should expose continuation state")
    assert_true("next_offset:" in result, "long web_search output should expose next_offset")
    assert_true("...(Search result truncated due to length)" not in result, "tool output should not use bare truncation")
    assert_true("Page contents:" in result, "deep page contents should appear in the first returned window")
    assert_true("PAGE_BODY_SENTINEL" in result, "deep fetched page content should be visible to the model")
    assert_true(
        "content_id: web_search:" in result,
        "first window should include a content_id for continuing the full deep search result",
    )
    assert_true(
        result.count("content_id: web_search:") >= 2,
        "first window should also include the fetched page content_id for tool_content_read",
    )
    assert_true(
        "Results:" not in result or result.index("Page contents:") < result.index("Results:"),
        "deep page contents should be prioritized before search result listings when both are in the first window",
    )


def test_add_note() -> None:
    notes: List[str] = []
    add_note(notes, "  hello  ")
    add_note(notes, "hello")
    add_note(notes, "hello   world")
    add_note(notes, "hello world")
    add_note(notes, "")
    add_note(None, "ignored")

    assert_true(notes == ["hello", "hello world"], f"add_note should normalize, dedupe, and skip empty notes, got {notes}")


def test_add_note_dedupes_pre_existing_unnormalized() -> None:
    notes: List[str] = ["Query   truncated to 400 characters."]
    add_note(notes, "Query truncated to 400 characters.")
    assert_true(
        len(notes) == 1,
        f"add_note should dedupe against pre-existing unnormalized note, got {notes}",
    )


def test_deduplicate_images_removes_tracking_params() -> None:
    images = (
        ImageResult(url="https://img.example.com/photo.jpg"),
        ImageResult(url="https://img.example.com/photo.jpg?utm_source=x&fbclid=1"),
        ImageResult(url="https://img.example.com/photo.jpg#fragment"),
        ImageResult(url="https://img.example.com/other.png"),
    )
    result = deduplicate_images(images)
    assert_true(
        len(result) == 2,
        f"deduplicate_images should remove tracking-param and fragment duplicates, got {len(result)}",
    )
    assert_true(
        result[0].url == "https://img.example.com/photo.jpg",
        f"first image should be the base url, got {result[0].url}",
    )
    assert_true(
        result[1].url == "https://img.example.com/other.png",
        f"second image should be the distinct url, got {result[1].url}",
    )


def test_normalize_url_preserves_path_case_and_nondefault_port() -> None:
    assert_true(
        normalize_url_for_dedup("https://example.com/API/Endpoint") == "https://example.com/API/Endpoint",
        "path case should be preserved",
    )
    assert_true(
        normalize_url_for_dedup("https://example.com:8443/Path") == "https://example.com:8443/Path",
        "non-default port should be kept",
    )
    assert_true(
        normalize_url_for_dedup("https://example.com:443/Path") == "https://example.com/Path",
        "default port should be removed",
    )


def test_has_response_content() -> None:
    assert_true(has_response_content(SearchResponse(query="q", answer="yes")) is True, "answer should count")
    assert_true(
        has_response_content(SearchResponse(query="q", results=(make_result("q", 1),))) is True,
        "results should count",
    )
    assert_true(
        has_response_content(SearchResponse(query="q", images=(ImageResult(url="https://img.example.com/a.png"),))) is True,
        "images should count",
    )
    assert_true(has_response_content(SearchResponse(query="q")) is False, "empty response should not count")


async def main() -> int:
    sync_tests = [
        test_normalize_queries,
        test_normalize_queries_notes,
        test_normalize_url_for_dedup,
        test_has_site_operator,
        test_merge_deduplicates_urls_domains_and_images,
        test_merge_reports_deduplication_notes,
        test_merge_can_disable_domain_deduplication,
        test_format_response_contains_evidence_pack_sections,
        test_format_response_deduplicates_notes,
        test_tool_schema_only_exposes_final_params,
        test_tool_description_contains_new_guidance,
        test_removed_old_page_reader_name,
        test_format_response_does_not_truncate_long_output,
        test_add_note,
        test_add_note_dedupes_pre_existing_unnormalized,
        test_deduplicate_images_removes_tracking_params,
        test_normalize_url_preserves_path_case_and_nondefault_port,
        test_has_response_content,
    ]

    async_tests = [
        test_search_many_returns_merged_results,
        test_one_query_failure_does_not_break_others,
        test_search_many_with_images,
        test_search_many_never_calls_tavily,
        test_search_many_passes_allow_paid_fallback_false,
        test_tool_normal_mode_uses_search_many_for_one_query,
        test_tool_requires_queries,
        test_tool_site_operator_disables_domain_dedupe,
        test_tool_website_and_offsite_do_not_disable_domain_dedupe,
        test_deep_mode_reads_top_eight_unique_pages,
        test_deep_mode_output_shows_internal_page_fetch,
        test_read_page_contents_deduplicates_normalized_urls,
        test_read_page_contents_concurrency_limit,
        test_read_page_contents_failure_does_not_block_others,
        test_read_page_contents_skips_none_and_non_text,
        test_read_page_contents_timeout_adds_note,
        test_read_page_contents_cache_window_failure_skips_only_current_page,
        test_read_page_contents_output_binds_result_index,
        test_tool_deep_without_content_adds_note,
        test_tool_long_output_uses_tool_content_window,
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
