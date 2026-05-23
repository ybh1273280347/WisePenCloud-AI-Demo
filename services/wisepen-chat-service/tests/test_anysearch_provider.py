from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from chat.application.web_search.errors import (
    SearchProviderError,
    SearchRateLimitError,
    SearchTimeoutError,
)
from chat.application.web_search.internal.models.anysearch import (
    AnySearchRequest,
    map_anysearch_response,
)
from chat.application.web_search.internal.searcher.anysearch_searcher import (
    AnySearchSearcher,
)
from chat.application.web_search.provider_policy import parse_custom_provider_credentials
from chat.application.web_search.models.common import SearchResponse


def test_anysearch_request_payload_minimal() -> None:
    request = AnySearchRequest(query="RAG reranking", max_results=5)

    assert request.to_payload() == {
        "query": "RAG reranking",
        "max_results": 5,
    }


def test_anysearch_request_payload_with_language_and_zone() -> None:
    request = AnySearchRequest(
        query="RAG reranking",
        max_results=5,
        language="zh-CN",
        zone="cn",
    )

    assert request.to_payload() == {
        "query": "RAG reranking",
        "max_results": 5,
        "language": "zh-CN",
        "zone": "cn",
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": "", "max_results": 1},
        {"query": "   ", "max_results": 1},
        {"query": None, "max_results": 1},
        {"query": True, "max_results": 1},
        {"query": 123, "max_results": 1},
        {"query": "q", "max_results": 0},
        {"query": "q", "max_results": 101},
        {"query": "q", "max_results": True},
        {"query": "q", "max_results": "10"},
        {"query": "q", "max_results": 1, "language": ""},
        {"query": "q", "max_results": 1, "language": "   "},
        {"query": "q", "max_results": 1, "language": True},
        {"query": "q", "max_results": 1, "zone": ""},
        {"query": "q", "max_results": 1, "zone": "CN"},
        {"query": "q", "max_results": 1, "zone": "us"},
        {"query": "q", "max_results": 1, "zone": True},
    ],
)
def test_anysearch_request_strict_validation(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        AnySearchRequest(**kwargs)


def test_map_anysearch_response_preserves_metadata() -> None:
    response = map_anysearch_response(
        {
            "results": [
                {"title": "skip", "description": "no url"},
                {
                    "title": "Go 1.22 Release Notes",
                    "url": "https://go.dev/doc/go1.22",
                    "description": "Go 1.22 is a major release...",
                    "content": "Detailed content here...",
                    "source": "web",
                    "score": 0.87,
                    "quality_score": 0.95,
                    "published_at": "2024-02-06T00:00:00Z",
                    "raw_content": "raw",
                },
                {
                    "title": "",
                    "url": "https://example.com/fallback-title",
                    "description": "Fallback title uses URL.",
                },
            ],
            "metadata": {"request_id": "req_abc123"},
        },
        query="go release",
        source="custom:anysearch",
    )

    assert [result.url for result in response.results] == [
        "https://go.dev/doc/go1.22",
        "https://example.com/fallback-title",
    ]
    assert response.results[0].snippet == "Go 1.22 is a major release..."
    assert response.results[0].metadata["provider"] == "anysearch"
    assert response.results[0].metadata["raw_rank"] == 2
    assert response.results[0].metadata["content"] == "Detailed content here..."
    assert response.results[0].metadata["provider_source"] == "web"
    assert response.results[0].metadata["provider_score"] == 0.87
    assert response.results[0].metadata["provider_quality_score"] == 0.95
    assert response.results[0].metadata["published_at"] == "2024-02-06T00:00:00Z"
    assert response.results[0].metadata["raw_content"] == "raw"
    assert response.results[1].title == "https://example.com/fallback-title"
    assert response.metadata["provider"] == "anysearch"
    assert response.metadata["raw_metadata"] == {"request_id": "req_abc123"}


def test_map_anysearch_response_non_list_results_is_empty() -> None:
    response = map_anysearch_response(
        {"results": {"bad": "shape"}, "metadata": {"request_id": "req"}},
        query="query",
        source="custom:anysearch",
    )

    assert response.results == ()
    assert response.metadata["raw_metadata"] == {"request_id": "req"}


def test_anysearch_searcher_omits_authorization_without_api_key() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Result",
                        "url": "https://example.com",
                        "description": "Snippet",
                    }
                ],
                "metadata": {"request_id": "req"},
            },
        )

    async def run() -> SearchResponse:
        searcher = _anysearch_with_transport(httpx.MockTransport(handler), api_key=None)
        try:
            return await searcher.search(
                "RAG reranking",
                max_results=5,
                language="zh-CN",
            )
        finally:
            await searcher.close()

    response = asyncio.run(run())

    assert len(response.results) == 1
    assert requests[0].url.path == "/v1/search"
    assert "authorization" not in requests[0].headers
    assert requests[0].headers["content-type"] == "application/json"
    assert requests[0].read() == (
        b'{"query":"RAG reranking","max_results":5,"language":"zh-CN"}'
    )


def test_anysearch_searcher_sends_authorization_with_api_key_and_zone() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"results": [], "metadata": {}})

    async def run() -> None:
        searcher = _anysearch_with_transport(
            httpx.MockTransport(handler),
            api_key="key-123",
            zone="intl",
        )
        try:
            await searcher.search("RAG reranking", max_results=5)
        finally:
            await searcher.close()

    asyncio.run(run())

    assert requests[0].headers["authorization"] == "Bearer key-123"
    assert requests[0].read() == (
        b'{"query":"RAG reranking","max_results":5,"zone":"intl"}'
    )


@pytest.mark.parametrize("status", [401, 403])
def test_anysearch_auth_status_raises_provider_error(status: int) -> None:
    async def run() -> None:
        searcher = _anysearch_with_transport(
            httpx.MockTransport(lambda _request: httpx.Response(status, json={}))
        )
        try:
            await searcher.search("query")
        finally:
            await searcher.close()

    with pytest.raises(SearchProviderError, match="authentication failed"):
        asyncio.run(run())


def test_anysearch_402_status_raises_quota_provider_error() -> None:
    async def run() -> None:
        searcher = _anysearch_with_transport(
            httpx.MockTransport(lambda _request: httpx.Response(402, json={}))
        )
        try:
            await searcher.search("query")
        finally:
            await searcher.close()

    with pytest.raises(SearchProviderError, match="quota exhausted"):
        asyncio.run(run())


def test_anysearch_429_status_raises_rate_limit_error() -> None:
    async def run() -> None:
        searcher = _anysearch_with_transport(
            httpx.MockTransport(lambda _request: httpx.Response(429, json={}))
        )
        try:
            await searcher.search("query")
        finally:
            await searcher.close()

    with pytest.raises(SearchRateLimitError):
        asyncio.run(run())


def test_anysearch_invalid_json_raises_provider_error() -> None:
    async def run() -> None:
        searcher = _anysearch_with_transport(
            httpx.MockTransport(lambda _request: httpx.Response(200, text="not json"))
        )
        try:
            await searcher.search("query")
        finally:
            await searcher.close()

    with pytest.raises(SearchProviderError, match="invalid JSON"):
        asyncio.run(run())


def test_anysearch_invalid_response_type_raises_provider_error() -> None:
    async def run() -> None:
        searcher = _anysearch_with_transport(
            httpx.MockTransport(lambda _request: httpx.Response(200, json=[]))
        )
        try:
            await searcher.search("query")
        finally:
            await searcher.close()

    with pytest.raises(SearchProviderError, match="invalid response type"):
        asyncio.run(run())


def test_anysearch_timeout_raises_search_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    async def run() -> None:
        searcher = _anysearch_with_transport(httpx.MockTransport(handler))
        try:
            await searcher.search("query")
        finally:
            await searcher.close()

    with pytest.raises(SearchTimeoutError):
        asyncio.run(run())


def test_anysearch_empty_api_key_is_supported_custom_credential() -> None:
    credentials = parse_custom_provider_credentials(
        [{"provider": "anysearch", "api_key": "", "enabled": True, "zone": "cn"}]
    )

    assert len(credentials) == 1
    assert credentials[0].provider == "anysearch"
    assert credentials[0].api_key == ""
    assert credentials[0].zone == "cn"


def _anysearch_with_transport(
    transport: httpx.AsyncBaseTransport,
    *,
    api_key: str | None = None,
    zone: str | None = None,
) -> AnySearchSearcher:
    return AnySearchSearcher(
        api_key=api_key,
        base_url="https://anysearch.test",
        timeout_seconds=1.0,
        zone=zone,
        transport=transport,
    )
