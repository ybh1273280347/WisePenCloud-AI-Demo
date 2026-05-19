from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any, Mapping

import httpx
import pytest

from chat.application.tools.services.paper_search.cache.doi_hydration_cache import (
    DOIHydrationCache,
)
from chat.application.tools.services.paper_search.config import (
    DOI_HYDRATION_LIMIT_DEEP,
    DOI_HYDRATION_LIMIT_FAST,
)
from chat.application.tools.services.paper_search.doi_queue import (
    collect_dois_for_hydration,
    doi_hydration_limit,
)
from chat.application.tools.services.paper_search.entity_fusion import (
    fuse_entities,
    merge_doi_record,
)
from chat.application.tools.services.paper_search.expanders import ExaFindSimilarExpander
from chat.application.tools.services.paper_search.freshness import (
    ArxivDeltaIndex,
    ArxivMonitor,
    parse_arxiv_atom_feed,
)
from chat.application.tools.services.paper_search.hydrators import (
    ArxivHydrator,
    CrossrefDOIResolver,
    DataCiteDOIResolver,
    DOIContentNegotiationResolver,
    DOIHydrationRouter,
)
from chat.application.tools.services.paper_search.identifiers import (
    collect_candidate_dois,
    extract_arxiv_id_from_arxiv_doi,
    normalize_doi,
)
from chat.application.tools.services.paper_search.models import (
    DOIMetadataRecord,
    HydrationStatus,
    PaperEntity,
    PaperPointer,
    PaperResultType,
    PaperSearchDepth,
    PaperSearchFreshness,
    PaperSearchRequest,
    PaperSearchResponse,
    ScholarlyResourceType,
)
from chat.application.tools.services.paper_search.parsers import (
    parse_crossref_work,
    parse_csl_json,
    parse_datacite_doi,
)
from chat.application.tools.services.paper_search.query import validate_request
from chat.application.tools.services.paper_search.query_variants import build_query_variants
from chat.application.tools.services.paper_search.ranking import (
    compute_recency_score,
    compute_rewrite_rrf_scores,
    evidence_diversity_score,
    rank_entity,
)
from chat.application.tools.services.paper_search.service import PaperSearchService
from chat.application.tools.services.paper_search.sources.exa_search_source import (
    ExaSearchSource,
)
from chat.core.config.app_settings import settings


def test_request_defaults_and_agent_tool_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    request = PaperSearchRequest(query="latest RAG 2025")
    assert request.depth == PaperSearchDepth.DEEP
    assert request.freshness == PaperSearchFreshness.BALANCED
    assert build_query_variants(
        PaperSearchRequest(
            query="RAG",
            query_variants=["retrieval augmented generation", "agentic RAG"],
        )
    ) == ["RAG", "retrieval augmented generation", "agentic RAG"]

    object.__setattr__(request, "query_variants", ["ok", ""])
    with pytest.raises(ValueError, match="query_variants must not contain empty"):
        validate_request(request)

    object.__setattr__(request, "query_variants", ["ok", 1])
    with pytest.raises(TypeError, match="query_variants must contain str"):
        validate_request(request)


def test_exa_payload_depth_freshness_and_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "json": _json(request)})
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "RAG Systems",
                        "url": "https://doi.org/10.1000/rag",
                        "highlights": ["DOI 10.1000/rag"],
                        "publishedDate": "2026-01-01",
                    }
                ]
            },
        )

    monkeypatch.setattr(settings, "EXA_BASE_URL", "https://exa.test")
    monkeypatch.setattr(settings, "EXA_API_KEY", "key")

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = ExaSearchSource(client)
            fast, _ = await source.search(
                query="RAG",
                rewrite_query="RAG",
                depth=PaperSearchDepth.FAST,
                freshness=PaperSearchFreshness.STABLE,
            )
            deep, _ = await source.search(
                query="RAG",
                rewrite_query="RAG",
                depth=PaperSearchDepth.DEEP,
                freshness=PaperSearchFreshness.LATEST,
            )
            assert fast and deep

    asyncio.run(run())

    assert calls[0]["url"] == "https://exa.test/search"
    assert calls[0]["json"]["category"] == "research paper"
    assert calls[0]["json"]["contents"] == {"highlights": True}
    assert calls[0]["json"]["type"] == "auto"
    assert "includeDomains" not in calls[0]["json"]
    assert "startPublishedDate" not in calls[0]["json"]
    assert calls[1]["json"]["type"] == "deep-lite"
    assert "startPublishedDate" in calls[1]["json"]


def test_identifiers_and_doi_queue() -> None:
    assert normalize_doi("https://doi.org/10.1000/ABC.") == "10.1000/abc"
    assert normalize_doi("not a doi") is None
    assert extract_arxiv_id_from_arxiv_doi("10.48550/arXiv.2501.10120") == "2501.10120"

    pointer = PaperPointer(
        title="A paper",
        url="https://example.com/work",
        source_name="exa",
        rank=0,
        rewrite_query="q",
        pointer_type="research_paper_candidate",
        highlights=["Published as 10.1000/example"],
    )
    extracted = collect_candidate_dois(pointer)
    assert extracted[0].doi == "10.1000/example"
    assert extracted[0].confidence.value == "medium"

    entities = [
        _entity("arxiv", external_ids={"doi": "10.48550/arxiv.2501.10120"}),
        _entity("doi", external_ids={"doi": "10.1000/example"}),
    ]
    assert collect_dois_for_hydration(entities) == ["10.1000/example"]
    assert doi_hydration_limit(PaperSearchDepth.FAST) == DOI_HYDRATION_LIMIT_FAST
    assert doi_hydration_limit(PaperSearchDepth.DEEP) == DOI_HYDRATION_LIMIT_DEEP


def test_arxiv_delta_index_and_rss_parser() -> None:
    records = parse_arxiv_atom_feed(_ARXIV_FEED, source_feed="https://rss.arxiv.org/atom/cs.CL")
    assert records[0].arxiv_id == "2601.00001"
    assert records[0].authors == ["Ada Lovelace"]
    assert records[0].categories == ["cs.CL"]

    index = ArxivDeltaIndex()
    index.upsert_many(records + records)
    pointers = index.search("retrieval generation", max_results=3)
    assert len(pointers) == 1
    assert pointers[0].source_name == "arxiv_delta_index"
    assert pointers[0].extracted_arxiv_id == "2601.00001"


def test_arxiv_monitor_uses_gate_and_continues_on_feed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Gate:
        calls = 0

        async def wait(self) -> None:
            self.calls += 1

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url).endswith("/bad"):
            return httpx.Response(500)
        return httpx.Response(200, text=_ARXIV_FEED)

    monkeypatch.setattr(settings, "ARXIV_RSS_BASE_URL", "https://rss.test")
    index = ArxivDeltaIndex()
    gate = Gate()

    async def run() -> list[str]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ArxivMonitor(client, index, gate).sync_categories(["cs.CL", "bad"])

    warnings = asyncio.run(run())

    assert gate.calls == 2
    assert calls == ["https://rss.test/cs.CL", "https://rss.test/bad"]
    assert any("arxiv rss sync failed" in warning for warning in warnings)
    assert index.search("retrieval", max_results=1)


def test_arxiv_hydrator_batches_id_list_and_extracts_journal_doi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, text=_ARXIV_API_RESPONSE)

    monkeypatch.setattr(settings, "ARXIV_API_BASE_URL", "https://arxiv.test/query")

    async def run() -> list[PaperEntity]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            entities, warnings = await ArxivHydrator(client, _NoopGate()).hydrate(
                [
                    _pointer("https://arxiv.org/abs/2601.00001"),
                    _pointer("https://arxiv.org/abs/not-valid"),
                    _pointer("https://arxiv.org/pdf/2601.00002.pdf"),
                ]
            )
            assert warnings == []
            return entities

    entities = asyncio.run(run())

    assert captured[0].url.params["id_list"] == "2601.00001,2601.00002"
    assert entities[0].external_ids["doi"] == "10.1000/journal"
    assert entities[0].resource_type == ScholarlyResourceType.PREPRINT
    assert entities[0].preferred_version == "arxiv:2601.00001"


def test_doi_router_order_concurrency_and_cache() -> None:
    calls: list[str] = []
    record = _doi_record("Crossref")

    class Crossref:
        async def resolve(self, doi: str):
            calls.append(f"crossref:{doi}")
            return record if doi == "10.1000/a" else None

    class DataCite:
        async def resolve(self, doi: str):
            calls.append(f"datacite:{doi}")
            return _doi_record("DataCite") if doi == "10.1000/b" else None

    class ContentNegotiation:
        async def resolve(self, doi: str):
            calls.append(f"content:{doi}")
            return None

    async def run() -> None:
        router = DOIHydrationRouter(
            Crossref(),
            DataCite(),
            ContentNegotiation(),
            DOIHydrationCache(),
            max_concurrency=5,
        )
        records, failures = await router.hydrate_many(["10.1000/a", "10.1000/b", "10.1000/c"])
        cached, _ = await router.hydrate_many(["10.1000/a", "10.1000/c"])
        assert records["10.1000/a"].source_name == "Crossref"
        assert records["10.1000/b"].source_name == "DataCite"
        assert failures["10.1000/c"] == "doi_metadata_unresolved"
        assert cached["10.1000/a"].source_name == "Crossref"

    asyncio.run(run())

    assert calls.count("crossref:10.1000/a") == 1
    assert "datacite:10.1000/a" not in calls
    assert calls.count("crossref:10.1000/c") == 1
    assert calls.count("content:10.1000/c") == 1


def test_doi_resolvers_use_configured_base_urls_and_crossref_mailto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "crossref.test" in str(request.url):
            return httpx.Response(200, json=_CROSSREF_JSON)
        if "datacite.test" in str(request.url):
            return httpx.Response(200, json=_DATACITE_JSON)
        return httpx.Response(200, json=_CSL_JSON)

    monkeypatch.setattr(settings, "CROSSREF_BASE_URL", "https://crossref.test")
    monkeypatch.setattr(settings, "DATACITE_BASE_URL", "https://datacite.test")
    monkeypatch.setattr(settings, "DOI_BASE_URL", "https://doi.test")
    monkeypatch.setattr(settings, "TOOL_CONTACT_EMAIL", "tool@example.com")

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await CrossrefDOIResolver(client).resolve("10.1000/rag")
            assert await DataCiteDOIResolver(client).resolve("10.1000/data")
            assert await DOIContentNegotiationResolver(client).resolve("10.1000/csl")

    asyncio.run(run())

    assert str(requests[0].url).startswith("https://crossref.test/works/10.1000/rag")
    assert requests[0].url.params["mailto"] == "tool@example.com"
    assert str(requests[1].url) == "https://datacite.test/dois/10.1000/data"
    assert str(requests[2].url) == "https://doi.test/10.1000/csl"
    assert requests[2].headers["Accept"] == "application/vnd.citationstyles.csl+json"


def test_doi_parsers_map_resource_types() -> None:
    crossref = parse_crossref_work(_CROSSREF_JSON)
    datacite = parse_datacite_doi(_DATACITE_JSON)
    csl = parse_csl_json(_CSL_JSON)

    assert crossref and crossref.resource_type == ScholarlyResourceType.JOURNAL_ARTICLE
    assert crossref.venue == "Journal of Retrieval"
    assert crossref.publisher == "ACM"
    assert datacite and datacite.resource_type == ScholarlyResourceType.DATASET
    assert csl and csl.resource_type == ScholarlyResourceType.PROCEEDINGS_ARTICLE


def test_entity_fusion_and_doi_merge_semantics() -> None:
    preprint = _entity(
        "preprint",
        title="Neural Ranking for Web Search",
        authors=["Ada Lovelace"],
        year=2025,
        external_ids={"arxiv": "2601.00001", "doi": "10.1000/rank"},
        resource_type=ScholarlyResourceType.PREPRINT,
        abstract="arxiv abstract",
        abstract_source="arxiv",
    )
    discovered = _entity(
        "discovered",
        title="Neural Ranking for Web Search",
        authors=["Ada Lovelace"],
        year=2026,
        external_ids={"doi": "10.1000/rank"},
        evidence_sources=["exa"],
    )
    unrelated_same_title = _entity(
        "other",
        title="Neural Ranking for Web Search",
        authors=["Grace Hopper"],
        year=2030,
        url="https://example.edu/other",
    )

    fused = fuse_entities([preprint, discovered, unrelated_same_title])
    assert len(fused) == 2

    merged = merge_doi_record(preprint, _doi_record("Crossref"))
    assert merged.resource_type == ScholarlyResourceType.JOURNAL_ARTICLE
    assert merged.result_type == PaperResultType.PAPER
    assert merged.preferred_version == "arxiv:2601.00001"
    assert merged.authoritative_version == "doi:10.1000/rag"
    assert merged.abstract == "arxiv abstract"
    assert any(version.external_id == "2601.00001" for version in merged.versions)
    assert any(version.external_id == "10.1000/rag" for version in merged.versions)


def test_ranking_adjustment_is_capped_and_recency_modes() -> None:
    entity = _entity(
        "ranking",
        metadata_confidence=1.0,
        source_confidence=1.0,
        evidence_sources=["exa", "arxiv", "doi"],
        publication_date=(date.today() - timedelta(days=180)).isoformat(),
    )

    assert compute_rewrite_rrf_scores({"a": ["x", "y"], "b": ["y"]})["y"] == pytest.approx(1.0)
    assert evidence_diversity_score(_entity("none", evidence_sources=[])) == 0.0
    assert evidence_diversity_score(_entity("one", evidence_sources=["exa"])) == 0.2
    assert evidence_diversity_score(entity) == 1.0
    assert compute_recency_score(
        _entity("nodate", publication_date=None, year=None),
        PaperSearchFreshness.BALANCED,
        date.today(),
    ) == 0.5
    assert compute_recency_score(entity, PaperSearchFreshness.STABLE, date.today()) > compute_recency_score(
        entity,
        PaperSearchFreshness.LATEST,
        date.today(),
    )

    score = rank_entity(
        entity,
        query_relevance=0.1,
        rewrite_rrf=0.0,
        freshness=PaperSearchFreshness.STABLE,
        reference_date=date.today(),
    )
    assert score <= 0.1 + 0.05 + 0.025


def test_service_uses_latest_delta_only_when_explicit_and_deep_find_similar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, Mapping[str, Any]]] = []
    index = ArxivDeltaIndex()
    index.upsert_many(parse_arxiv_atom_feed(_ARXIV_FEED, source_feed="rss"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            payload = _json(request)
            requests.append((str(request.url), payload))
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Retrieval Generation",
                            "url": "https://arxiv.org/abs/2601.00001",
                            "highlights": ["retrieval generation"],
                            "publishedDate": "2026-01-01",
                        }
                    ]
                },
            )
        return httpx.Response(200, text=_ARXIV_API_RESPONSE)

    monkeypatch.setattr(settings, "EXA_API_KEY", "key")
    monkeypatch.setattr(settings, "EXA_BASE_URL", "https://exa.test")
    monkeypatch.setattr(settings, "PAPER_SEARCH_ENABLE_DOI_HYDRATION", False)

    async def run() -> tuple[PaperSearchResponse, PaperSearchResponse]:
        original_client = httpx.AsyncClient

        def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(handler)
            return original_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        service = PaperSearchService(delta_index=index)
        balanced = await service.search(
            PaperSearchRequest(
                query="retrieval generation",
                freshness=PaperSearchFreshness.BALANCED,
                depth=PaperSearchDepth.FAST,
            )
        )
        latest = await service.search(
            PaperSearchRequest(
                query="retrieval generation",
                freshness=PaperSearchFreshness.LATEST,
                depth=PaperSearchDepth.DEEP,
            )
        )
        return balanced, latest

    balanced, latest = asyncio.run(run())

    assert "arxiv_delta_index" not in balanced.searched_sources
    assert "arxiv_delta_index" in latest.searched_sources
    assert balanced.results
    assert latest.results
    assert requests[0][1]["type"] == "auto"
    assert requests[1][1]["type"] == "deep-lite"
    assert "startPublishedDate" in requests[1][1]
    assert any(url.endswith("/findSimilar") for url, _ in requests)


def test_service_instructs_web_search_fallback_when_exa_quota_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chat.application.tools.services.paper_search.formatting import format_paper_search_response

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "quota exceeded"})

    monkeypatch.setattr(settings, "EXA_API_KEY", "key")
    monkeypatch.setattr(settings, "EXA_BASE_URL", "https://exa.test")
    monkeypatch.setattr(settings, "PAPER_SEARCH_ENABLE_ARXIV_MONITOR", False)
    monkeypatch.setattr(settings, "PAPER_SEARCH_ENABLE_DOI_HYDRATION", False)

    async def run() -> PaperSearchResponse:
        original_client = httpx.AsyncClient

        def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(handler)
            return original_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        service = PaperSearchService()
        return await service.search(
            PaperSearchRequest(
                query="retrieval generation",
                freshness=PaperSearchFreshness.BALANCED,
                depth=PaperSearchDepth.FAST,
            )
        )

    response = asyncio.run(run())
    formatted = format_paper_search_response(response)

    assert response.results == []
    assert "exa" in response.searched_sources
    assert "exa" in response.failed_sources
    assert "exa search failed: quota or rate limit reached" in response.warnings
    assert any("use web_search as a recall fallback" in warning for warning in response.warnings)
    assert "Call web_search as a recall fallback before answering." in formatted
    assert "Tell the user clearly that Exa discovery is unavailable" in formatted


def test_find_similar_uses_settings_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "Similar", "url": "https://example.edu/similar", "highlights": []}
                ]
            },
        )

    monkeypatch.setattr(settings, "EXA_BASE_URL", "https://exa-similar.test")
    monkeypatch.setattr(settings, "EXA_API_KEY", "key")

    async def run() -> list[PaperPointer]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            pointers, warnings = await ExaFindSimilarExpander(client).expand(
                seeds=[_entity("seed", url="https://example.edu/seed")]
            )
            assert warnings == []
            return pointers

    pointers = asyncio.run(run())

    assert urls == ["https://exa-similar.test/findSimilar"]
    assert pointers[0].source_name == "exa_find_similar"


def test_formatting_uses_v15_labels_and_no_banned_words() -> None:
    from chat.application.tools.services.paper_search.formatting import format_paper_search_response

    text = format_paper_search_response(
        PaperSearchResponse(
            query="rag",
            results=[_entity("result", hydration_sources=["arxiv"])],
            searched_sources=["exa"],
            skipped_sources=[],
            failed_sources=[],
            warnings=[],
        )
    )

    assert "hydration_status:" in text
    assert "resource_type:" in text
    assert "abstract_source:" in text
    assert "verified" not in text.lower()
    assert "trusted" not in text.lower()
    assert "authoritative paper" not in text.lower()


def _pointer(url: str) -> PaperPointer:
    return PaperPointer(
        title="Pointer",
        url=url,
        source_name="exa",
        rank=0,
        rewrite_query="q",
        pointer_type="arxiv",
    )


def _entity(
    canonical_id: str,
    *,
    title: str = "Retrieval Generation",
    authors: list[str] | None = None,
    year: int | None = 2026,
    publication_date: str | None = "2026-01-01",
    url: str | None = "https://example.edu/work",
    external_ids: dict[str, str] | None = None,
    evidence_sources: list[str] | None = None,
    hydration_sources: list[str] | None = None,
    resource_type: ScholarlyResourceType = ScholarlyResourceType.UNKNOWN,
    metadata_confidence: float = 0.4,
    source_confidence: float = 0.6,
    abstract: str | None = None,
    abstract_source: str | None = None,
) -> PaperEntity:
    return PaperEntity(
        canonical_id=canonical_id,
        title=title,
        authors=authors or ["Ada Lovelace"],
        year=year,
        publication_date=publication_date,
        url=url,
        external_ids=external_ids or {},
        evidence_sources=["exa"] if evidence_sources is None else evidence_sources,
        hydration_sources=hydration_sources or [],
        hydration_status=HydrationStatus.HYDRATED if hydration_sources else HydrationStatus.DISCOVERED_ONLY,
        resource_type=resource_type,
        metadata_confidence=metadata_confidence,
        source_confidence=source_confidence,
        abstract=abstract,
        abstract_source=abstract_source,
    )


def _doi_record(source_name: str) -> DOIMetadataRecord:
    return DOIMetadataRecord(
        doi="10.1000/rag",
        title="Retrieval Generation Published",
        authors=["Ada Lovelace"],
        abstract="doi abstract",
        year=2026,
        publication_date="2026-02-01",
        venue="Journal of Retrieval",
        publisher="ACM",
        resource_type=ScholarlyResourceType.JOURNAL_ARTICLE,
        url="https://doi.org/10.1000/rag",
        pdf_url="https://example.edu/rag.pdf",
        source_name=source_name,
        raw_source=source_name.lower(),
        metadata_confidence=0.92,
    )


def _json(request: httpx.Request) -> dict[str, Any]:
    import json

    return json.loads(request.content.decode("utf-8"))


class _NoopGate:
    async def wait(self) -> None:
        return None


_ARXIV_FEED = """
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>https://arxiv.org/abs/2601.00001</id>
    <title>Retrieval Generation</title>
    <summary>A retrieval generation paper.</summary>
    <author><name>Ada Lovelace</name></author>
    <published>2026-01-01T00:00:00Z</published>
    <updated>2026-01-02T00:00:00Z</updated>
    <category term="cs.CL" />
  </entry>
</feed>
"""

_ARXIV_API_RESPONSE = """
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>https://arxiv.org/abs/2601.00001</id>
    <title>Retrieval Generation</title>
    <summary>Arxiv abstract.</summary>
    <author><name>Ada Lovelace</name></author>
    <published>2026-01-01T00:00:00Z</published>
    <arxiv:doi>10.1000/journal</arxiv:doi>
    <link title="pdf" href="https://arxiv.org/pdf/2601.00001" />
  </entry>
</feed>
"""

_CROSSREF_JSON = {
    "message": {
        "DOI": "10.1000/rag",
        "title": ["Retrieval Generation"],
        "author": [{"given": "Ada", "family": "Lovelace"}],
        "abstract": "<jats:p>Crossref abstract.</jats:p>",
        "issued": {"date-parts": [[2026, 2, 1]]},
        "container-title": ["Journal of Retrieval"],
        "publisher": "ACM",
        "type": "journal-article",
        "URL": "https://doi.org/10.1000/rag",
        "link": [{"URL": "https://example.edu/rag.pdf", "content-type": "application/pdf"}],
    }
}

_DATACITE_JSON = {
    "data": {
        "attributes": {
            "doi": "10.1000/data",
            "titles": [{"title": "Retrieval Dataset"}],
            "creators": [{"name": "Ada Lovelace"}],
            "descriptions": [{"description": "Dataset abstract"}],
            "publicationYear": 2026,
            "publisher": "Zenodo",
            "types": {"resourceTypeGeneral": "Dataset"},
            "url": "https://example.edu/dataset",
        }
    }
}

_CSL_JSON = {
    "DOI": "10.1000/csl",
    "title": "Conference Retrieval",
    "author": [{"given": "Ada", "family": "Lovelace"}],
    "issued": {"date-parts": [[2026, 3, 1]]},
    "container-title": "SIGIR",
    "publisher": "ACM",
    "type": "paper-conference",
    "URL": "https://doi.org/10.1000/csl",
    "abstract": "CSL abstract",
}
