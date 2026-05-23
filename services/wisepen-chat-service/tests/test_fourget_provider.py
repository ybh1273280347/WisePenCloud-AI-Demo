from __future__ import annotations

import asyncio
from typing import Any, List, Optional

import httpx
import pytest

from chat.application.web_search.errors import (
    SearchProviderError,
    SearchProviderTransientError,
    SearchTimeoutError,
)
from chat.application.web_search.internal.cache import SearchCache
from chat.application.web_search.internal.models.fourget import (
    FourGetSearchRequest,
    map_fourget_response,
)
from chat.application.web_search.internal.planning.models import (
    QueryVariant,
    VariantSearchResponse,
)
from chat.application.web_search.internal.provider_policy import (
    select_default_provider_calls,
)
from chat.application.web_search.internal.ranking.url_ranker import rank_urls_pipeline
from chat.application.web_search.internal.search_coordinator import (
    SearchCoordinator,
    SearchManyRequest,
)
from chat.application.web_search.internal.searcher.fourget_searcher import (
    FourGetSearcher,
)
from chat.application.web_search.models.common import SearchResponse, SearchResult


def test_fourget_search_request_params() -> None:
    request = FourGetSearchRequest(query="RAG reranking", scraper="ddg")

    assert request.to_params() == {"s": "RAG reranking", "scraper": "ddg"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": "", "scraper": "ddg"},
        {"query": "   ", "scraper": "ddg"},
        {"query": None, "scraper": "ddg"},
        {"query": True, "scraper": "ddg"},
        {"query": 123, "scraper": "ddg"},
        {"query": "q", "scraper": ""},
        {"query": "q", "scraper": "   "},
        {"query": "q", "scraper": None},
        {"query": "q", "scraper": True},
        {"query": "q", "scraper": 123},
        {"query": "q", "scraper": "google"},
        {"query": "q", "scraper": "ddg", "endpoint": None},
        {"query": "q", "scraper": "ddg", "endpoint": True},
        {"query": "q", "scraper": "ddg", "endpoint": 1},
        {"query": "q", "scraper": "ddg", "endpoint": ""},
        {"query": "q", "scraper": "ddg", "endpoint": "images"},
    ],
)
def test_fourget_search_request_strict_validation(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        FourGetSearchRequest(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"base_url": ""},
        {"base_url": "   "},
        {"base_url": None},
        {"base_url": True},
        {"base_url": 123},
        {"base_url": "ftp://fourget"},
        {"base_url": "http://fourget", "timeout": 0},
        {"base_url": "http://fourget", "timeout": -1},
        {"base_url": "http://fourget", "timeout": True},
        {"base_url": "http://fourget", "timeout": "8"},
        {"base_url": "http://fourget", "web_scraper": "google"},
        {"base_url": "http://fourget", "web_scraper": True},
        {"base_url": "http://fourget", "max_concurrency": 0},
        {"base_url": "http://fourget", "max_concurrency": True},
        {"base_url": "http://fourget", "max_concurrency": 1.5},
        {"base_url": "http://fourget", "max_retries": -1},
        {"base_url": "http://fourget", "max_retries": False},
        {"base_url": "http://fourget", "retry_backoff_seconds": -0.1},
        {"base_url": "http://fourget", "retry_backoff_seconds": False},
    ],
)
def test_fourget_searcher_constructor_strict_validation(
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        FourGetSearcher(**kwargs)


def test_map_fourget_response_preserves_candidate_order_and_metadata() -> None:
    response = map_fourget_response(
        {
            "status": "ok",
            "npt": "next",
            "web": [
                {"title": "skip", "url": "", "description": "no url"},
                {
                    "title": "A",
                    "url": "https://a.example",
                    "description": "First",
                    "date": 1710000000,
                    "type": "web",
                    "thumb": None,
                    "sublink": [{"title": "Sub"}],
                    "table": {"x": "y"},
                },
                {
                    "title": None,
                    "url": "https://b.example",
                    "description": "Second",
                    "date": "raw date",
                },
            ],
        },
        query="query",
        scraper="ddg",
        max_results=2,
    )

    assert [result.url for result in response.results] == [
        "https://a.example",
        "https://b.example",
    ]
    assert response.results[0].metadata["raw_rank"] == 2
    assert response.results[0].metadata["provider"] == "fourget"
    assert response.results[0].metadata["scraper"] == "ddg"
    assert response.results[0].metadata["query"] == "query"
    assert response.results[0].metadata["date"] == 1710000000
    assert response.results[1].metadata["date"] == "raw date"


def test_fourget_searcher_success_maps_web_response() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "npt": "token",
                "web": [
                    {
                        "title": "Result",
                        "url": "https://example.com",
                        "description": "Snippet",
                    },
                    {
                        "title": "Second",
                        "url": "https://example.org",
                        "description": "Snippet 2",
                    },
                ],
            },
        )

    async def run() -> SearchResponse:
        searcher = _fourget_with_transport(httpx.MockTransport(handler))
        try:
            return await searcher.search("RAG reranking", max_results=1)
        finally:
            await searcher.close()

    response = asyncio.run(run())

    assert len(response.results) == 1
    assert response.results[0].title == "Result"
    assert response.results[0].metadata["provider"] == "fourget"
    assert requests[0].url.path == "/api/v1/web"
    assert requests[0].url.params["s"] == "RAG reranking"
    assert requests[0].url.params["scraper"] == "ddg"


def test_fourget_status_not_ok_raises_provider_error() -> None:
    async def run() -> None:
        searcher = _fourget_with_transport(
            httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={"status": "something failed", "npt": None, "web": []},
                )
            )
        )
        try:
            await searcher.search("query")
        finally:
            await searcher.close()

    with pytest.raises(SearchProviderError, match="provider_status_error"):
        asyncio.run(run())


def test_fourget_429_status_not_ok_preserves_provider_status() -> None:
    async def run() -> None:
        searcher = _fourget_with_transport(
            httpx.MockTransport(
                lambda _request: httpx.Response(
                    429,
                    json={"status": "invalid pass", "npt": None, "web": []},
                )
            )
        )
        try:
            await searcher.search("query")
        finally:
            await searcher.close()

    with pytest.raises(SearchProviderError) as exc_info:
        asyncio.run(run())

    message = str(exc_info.value)
    assert "provider_status_error" in message
    assert "invalid pass" in message


@pytest.mark.parametrize(
    "status",
    [
        "invalid pass",
        "captcha required",
        "unauthorized",
        "expired token",
        "blocked",
        "rate limited",
        "unsupported scraper",
    ],
)
def test_fourget_status_reason_is_preserved(status: str) -> None:
    async def run() -> None:
        searcher = _fourget_with_transport(
            httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={"status": status, "npt": None, "web": []},
                )
            )
        )
        try:
            await searcher.search("query")
        finally:
            await searcher.close()

    with pytest.raises(SearchProviderError) as exc_info:
        asyncio.run(run())

    assert status in str(exc_info.value)


def test_fourget_empty_web_retries_then_transient_error() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"status": "ok", "npt": None, "web": []})

    async def run() -> None:
        searcher = _fourget_with_transport(
            httpx.MockTransport(handler),
            max_retries=1,
            retry_backoff_seconds=0,
        )
        try:
            await searcher.search("query")
        finally:
            await searcher.close()

    with pytest.raises(SearchProviderTransientError, match="empty_result"):
        asyncio.run(run())

    assert calls == 2


def test_fourget_invalid_json_raises_provider_error() -> None:
    async def run() -> None:
        searcher = _fourget_with_transport(
            httpx.MockTransport(lambda _request: httpx.Response(200, text="not json"))
        )
        try:
            await searcher.search("query")
        finally:
            await searcher.close()

    with pytest.raises(SearchProviderError, match="json_parse_error"):
        asyncio.run(run())


def test_fourget_invalid_response_type_raises_provider_error() -> None:
    async def run() -> None:
        searcher = _fourget_with_transport(
            httpx.MockTransport(lambda _request: httpx.Response(200, json=[]))
        )
        try:
            await searcher.search("query")
        finally:
            await searcher.close()

    with pytest.raises(SearchProviderError, match="invalid_response_type"):
        asyncio.run(run())


def test_fourget_http_error_raises_provider_error() -> None:
    async def run() -> None:
        searcher = _fourget_with_transport(
            httpx.MockTransport(
                lambda _request: httpx.Response(
                    500,
                    json={
                        "status": "ok",
                        "npt": None,
                        "web": [
                            {
                                "title": "Result",
                                "url": "https://example.com",
                                "description": "Snippet",
                            }
                        ],
                    },
                )
            )
        )
        try:
            await searcher.search("query")
        finally:
            await searcher.close()

    with pytest.raises(SearchProviderError, match="http_error"):
        asyncio.run(run())


def test_fourget_connection_error_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    async def run() -> None:
        searcher = _fourget_with_transport(httpx.MockTransport(handler))
        try:
            await searcher.search("query")
        finally:
            await searcher.close()

    with pytest.raises(SearchProviderError, match="connection_error"):
        asyncio.run(run())


def test_fourget_timeout_raises_search_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    async def run() -> None:
        searcher = _fourget_with_transport(httpx.MockTransport(handler))
        try:
            await searcher.search("query")
        finally:
            await searcher.close()

    with pytest.raises(SearchTimeoutError):
        asyncio.run(run())


def test_fourget_redirect_raises_provider_error() -> None:
    async def run() -> None:
        searcher = _fourget_with_transport(
            httpx.MockTransport(
                lambda _request: httpx.Response(
                    302,
                    headers={"location": "https://example.com"},
                )
            )
        )
        try:
            await searcher.search("query")
        finally:
            await searcher.close()

    with pytest.raises(SearchProviderError, match="redirect"):
        asyncio.run(run())


def test_fourget_engines_override_raises_provider_error() -> None:
    async def run() -> None:
        searcher = FourGetSearcher("http://fourget")
        try:
            await searcher.search("query", engines=("google",))
        finally:
            await searcher.close()

    with pytest.raises(SearchProviderError, match="per-request engines override"):
        asyncio.run(run())


def test_fourget_with_images_uses_web_endpoint_only() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "npt": None,
                "web": [
                    {
                        "title": "Web",
                        "url": "https://example.com",
                        "description": "Only web",
                    }
                ],
            },
        )

    async def run() -> SearchResponse:
        searcher = _fourget_with_transport(httpx.MockTransport(handler))
        try:
            return await searcher.search("query", with_images=True)
        finally:
            await searcher.close()

    response = asyncio.run(run())

    assert len(response.results) == 1
    assert paths == ["/api/v1/web"]


def test_default_chain_policy_is_fourget_to_serper_and_excludes_searxng() -> None:
    variant = _variant("primary query")
    calls = select_default_provider_calls(
        mode="normal",
        variants=(variant,),
        primary_responses=(
            VariantSearchResponse(
                variant=variant,
                response=SearchResponse(
                    query="primary query",
                    results=(
                        SearchResult(
                            title="FourGet",
                            url="https://example.com",
                            snippet="result",
                        ),
                    ),
                    source="fourget",
                ),
            ),
        ),
        serper_enabled=True,
    )

    assert calls == ()

    fallback_calls = select_default_provider_calls(
        mode="normal",
        variants=(variant,),
        primary_responses=(),
        serper_enabled=True,
    )

    assert [call.provider for call in fallback_calls] == ["serper"]
    assert "searxng" not in [call.provider for call in fallback_calls]


def test_deep_fourget_low_coverage_supplements_serper() -> None:
    variant = _variant("primary query")
    calls = select_default_provider_calls(
        mode="deep",
        variants=(variant,),
        primary_responses=(
            VariantSearchResponse(
                variant=variant,
                response=SearchResponse(
                    query="primary query",
                    results=(
                        SearchResult(
                            title="One",
                            url="https://one.example/a",
                            snippet="result",
                        ),
                        SearchResult(
                            title="Two",
                            url="https://one.example/b",
                            snippet="result",
                        ),
                    ),
                    source="fourget",
                ),
            ),
        ),
        serper_enabled=True,
    )

    assert [call.provider for call in calls] == ["serper"]


def test_deep_fourget_sufficient_coverage_skips_serper() -> None:
    variant = _variant("primary query")
    calls = select_default_provider_calls(
        mode="deep",
        variants=(variant,),
        primary_responses=(
            VariantSearchResponse(
                variant=variant,
                response=SearchResponse(
                    query="primary query",
                    results=(
                        SearchResult(
                            title="One",
                            url="https://one.example/a",
                            snippet="result",
                        ),
                        SearchResult(
                            title="Two",
                            url="https://two.example/a",
                            snippet="result",
                        ),
                        SearchResult(
                            title="Three",
                            url="https://three.example/a",
                            snippet="result",
                        ),
                        SearchResult(
                            title="Four",
                            url="https://one.example/b",
                            snippet="result",
                        ),
                        SearchResult(
                            title="Five",
                            url="https://two.example/b",
                            snippet="result",
                        ),
                    ),
                    source="fourget",
                ),
            ),
        ),
        serper_enabled=True,
    )

    assert calls == ()


def test_fourget_success_does_not_call_serper() -> None:
    async def run() -> SearchResponse:
        coordinator = _coordinator(
            fourget_searcher=_StubFourGetSearcher(
                SearchResponse(
                    query="query",
                    results=(
                        SearchResult(
                            title="FourGet",
                            url="https://fourget.example",
                            snippet="primary",
                        ),
                    ),
                    source="fourget",
                )
            ),
            serper_searcher=_StubSerperSearcher(
                SearchResponse(query="query", results=())
            ),
        )
        try:
            result = await coordinator.search_many(
                SearchManyRequest(
                    queries=["RAG reranking"],
                    mode="fast",
                )
            )
            return result.response
        finally:
            await coordinator.close()

    response = asyncio.run(run())

    assert "fourget" in (response.source or "")


def test_fourget_failure_calls_serper() -> None:
    serper = _StubSerperSearcher(
        SearchResponse(
            query="query",
            results=(
                SearchResult(
                    title="Serper",
                    url="https://serper.example",
                    snippet="fallback",
                ),
            ),
            source="serper",
        )
    )

    async def run() -> SearchResponse:
        coordinator = _coordinator(
            fourget_searcher=_StubFourGetSearcher(None),
            serper_searcher=serper,
        )
        try:
            result = await coordinator.search_many(
                SearchManyRequest(
                    queries=["RAG reranking"],
                    mode="fast",
                )
            )
            return result.response
        finally:
            await coordinator.close()

    response = asyncio.run(run())

    assert serper.calls
    assert "serper" in (response.source or "")


def test_deep_low_coverage_fourget_calls_serper() -> None:
    serper = _StubSerperSearcher(
        SearchResponse(
            query="query",
            results=(
                SearchResult(
                    title="Serper",
                    url="https://serper.example",
                    snippet="supplement",
                ),
            ),
            source="serper",
        )
    )

    async def run() -> SearchResponse:
        coordinator = _coordinator(
            fourget_searcher=_StubFourGetSearcher(
                SearchResponse(
                    query="query",
                    results=(
                        SearchResult(
                            title="FourGet",
                            url="https://fourget.example",
                            snippet="primary",
                        ),
                    ),
                    source="fourget",
                )
            ),
            serper_searcher=serper,
        )
        try:
            result = await coordinator.search_many(
                SearchManyRequest(
                    queries=["RAG reranking", "RAG reranking official"],
                    mode="deep",
                )
            )
            return result.response
        finally:
            await coordinator.close()

    response = asyncio.run(run())

    assert serper.calls
    assert "serper" in (response.source or "")


def test_default_chain_still_enters_ranking_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    from chat.application.web_search.internal import search_coordinator as module

    calls = {"ranking": 0}
    original_ranker = module.rank_urls_pipeline

    def tracking_ranker(*args: Any, **kwargs: Any):
        calls["ranking"] += 1
        return original_ranker(*args, **kwargs)

    monkeypatch.setattr(module, "rank_urls_pipeline", tracking_ranker)

    async def run() -> SearchResponse:
        coordinator = _coordinator(
            fourget_searcher=_StubFourGetSearcher(
                SearchResponse(
                    query="query",
                    results=(
                        SearchResult(
                            title="FourGet",
                            url="https://fourget.example",
                            snippet="primary",
                        ),
                    ),
                    source="fourget",
                )
            ),
            serper_searcher=_StubSerperSearcher(SearchResponse(query="query")),
        )
        try:
            result = await coordinator.search_many(
                SearchManyRequest(
                    queries=["RAG reranking"],
                    mode="fast",
                )
            )
            return result.response
        finally:
            await coordinator.close()

    response = asyncio.run(run())

    assert calls["ranking"] == 1
    assert response.results


def test_rank_urls_pipeline_accepts_fourget_candidates() -> None:
    variant = _variant("RAG reranking")
    ranked = rank_urls_pipeline(
        variant_responses=[
            VariantSearchResponse(
                variant=variant,
                response=SearchResponse(
                    query=variant.text,
                    source="fourget",
                    results=(
                        SearchResult(
                            title="Best RAG reranking",
                            url="https://example.com/rag",
                            snippet="RAG reranking guide",
                        ),
                    ),
                ),
            )
        ],
        mode="fast",
        merged_limit=10,
    )

    assert ranked
    assert ranked[0].candidate.provider == "fourget"


def _fourget_with_transport(
    transport: httpx.AsyncBaseTransport,
    *,
    max_retries: int = 1,
    retry_backoff_seconds: float = 0,
) -> FourGetSearcher:
    return FourGetSearcher(
        "http://fourget",
        timeout=8.0,
        web_scraper="ddg",
        max_concurrency=5,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        transport=transport,
    )


def _variant(text: str) -> QueryVariant:
    return QueryVariant(
        id="v0",
        text=text,
        role="primary",
        language="en",
        engines=("aol",),
        serial=False,
        max_results=10,
        weight=1.0,
    )


def _coordinator(
    *,
    fourget_searcher: Any,
    serper_searcher: Any,
) -> SearchCoordinator:
    return SearchCoordinator(
        cache=SearchCache(),
        fourget_searcher=fourget_searcher,
        searxng_searcher=_StubSearXNGSearcher(),
        serper_searcher=serper_searcher,
        fourget_enabled=True,
        searxng_enabled=False,
        serper_enabled=True,
    )


class _StubFourGetSearcher:
    def __init__(self, response: Optional[SearchResponse]) -> None:
        self.response = response
        self.calls: List[dict[str, Any]] = []

    async def search(self, query: str, **kwargs: Any) -> SearchResponse:
        self.calls.append({"query": query, **kwargs})
        if self.response is None:
            raise SearchProviderError("fourget", "forced failure")
        return self.response

    async def close(self) -> None:
        return None


class _StubSerperSearcher:
    def __init__(self, response: SearchResponse) -> None:
        self.response = response
        self.calls: List[dict[str, Any]] = []

    async def search(self, query: str, **kwargs: Any) -> SearchResponse:
        self.calls.append({"query": query, **kwargs})
        return self.response

    async def close(self) -> None:
        return None


class _StubSearXNGSearcher:
    async def search(self, query: str, **kwargs: Any) -> SearchResponse:
        raise AssertionError("SearXNG must not participate in the default chain")

    async def close(self) -> None:
        return None
