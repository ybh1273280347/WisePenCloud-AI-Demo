import asyncio
import json
import re

from chat.application.tools.common.tool_content_store import (
    cache_and_format,
    cache_artifact_and_format_receipt,
    tool_content_store,
)
from chat.application.tools.config import TOOL_RESULT_MAX_CHARS
from chat.application.tools.knowledge.evidence_rank_tool import EvidenceRankTool
from chat.application.tools.knowledge.tool_content_read_tool import ToolContentReadTool
from chat.application.tools.services.evidence_ranking import (
    format_evidence_result,
    rank_evidence,
)
from chat.application.tools.services.evidence_ranking.models import (
    EvidenceRankResult,
    RankedEvidence,
)
from chat.application.tools.web.web_search_tool import WebSearchTool
from chat.application.web_search import (
    ImageResult,
    SearchManyResult,
    SearchResponse,
    SearchResult,
)


class _StaticSearchCoordinator:
    def __init__(self) -> None:
        self.request = None

    async def search_many(self, request):
        self.request = request
        return SearchManyResult(
            response=SearchResponse(
                query="artifact protocol",
                source="test",
                images=(
                    ImageResult(
                        url="https://img.example/search.png",
                        desc="search image",
                    ),
                ),
                results=(
                    SearchResult(
                        title="Alpha protocol",
                        url="https://alpha.example/docs",
                        snippet="Alpha evidence receipt protocol and rank details.",
                        images=(
                            ImageResult(
                                url="https://img.example/alpha.png",
                                desc="alpha",
                                source_url="https://alpha.example/docs",
                                resolution="640x480",
                            ),
                        ),
                    ),
                    SearchResult(
                        title="Beta runtime",
                        url="https://beta.example/runtime",
                        snippet="Runtime evidence chain and required tool followup.",
                    ),
                    SearchResult(
                        title="Gamma",
                        url="https://gamma.example/other",
                        snippet="Other result.",
                    ),
                ),
            )
        )


def _content_id_from_receipt(receipt: str) -> str:
    match = re.search(r"^content_id: (cnt_[a-f0-9]+)$", receipt, re.MULTILINE)
    assert match is not None
    return match.group(1)


def test_cache_artifact_and_format_receipt_omits_content() -> None:
    result = cache_artifact_and_format_receipt(
        session_id="session-receipt",
        tool_name="web_search",
        source="alpha",
        text='{"secret":"artifact body"}',
        metadata={
            "content_kind": "web_search_evidence_pack",
            "mode": "normal",
            "source_order": "reranked",
            "required_next_tool": "evidence_rank",
            "blocking_final_answer": True,
        },
    )

    content_id = _content_id_from_receipt(result)
    stored = tool_content_store.get(
        content_id=content_id,
        session_id="session-receipt",
    )

    assert stored is not None
    assert stored.text == '{"secret":"artifact body"}'
    assert "[ToolContent Receipt]" in result
    assert "content_kind: web_search_evidence_pack" in result
    assert "required_next_tool: evidence_rank" in result
    assert "blocking_final_answer: true" in result
    assert "artifact body" not in result


def test_cache_and_format_still_returns_content_window() -> None:
    result = cache_and_format(
        session_id="session-window",
        tool_name="web_fetch",
        source="https://example.test",
        text="visible window body",
    )

    assert "[ToolContent Metadata]" in result
    assert "[Content]\nvisible window body" in result


def test_web_search_fast_returns_snippets() -> None:
    coordinator = _StaticSearchCoordinator()
    tool = WebSearchTool(coordinator=coordinator)

    result = asyncio.run(
        tool.execute(
            {"session_id": "session-fast", "user_id": "user-1"},
            queries=["evidence protocol", "artifact rank"],
            mode="fast",
        )
    )

    assert "[Tool Result] Web search evidence pack" in result
    assert "Sources (reranked order for citations)" in result
    assert "Snippet: Alpha evidence receipt protocol" in result
    assert coordinator.request is not None
    assert coordinator.request.mode == "fast"


def test_web_search_normal_returns_receipt_only_artifact() -> None:
    coordinator = _StaticSearchCoordinator()
    tool = WebSearchTool(coordinator=coordinator)

    result = asyncio.run(
        tool.execute(
            {"session_id": "session-normal", "user_id": "user-1"},
            queries=["evidence protocol", "artifact rank"],
            mode="normal",
        )
    )

    content_id = _content_id_from_receipt(result)
    stored = tool_content_store.get(
        content_id=content_id,
        session_id="session-normal",
    )

    assert stored is not None
    assert "[ToolContent Receipt]" in result
    assert "content_kind: web_search_evidence_pack" in result
    assert "required_next_tool: evidence_rank" in result
    assert "blocking_final_answer: true" in result
    assert "Snippet:" not in result
    assert "Sources (reranked order for citations)" not in result
    assert "Alpha evidence receipt protocol" not in result
    assert stored.content_type == "application/json"
    assert stored.metadata["required_next_tool"] == "evidence_rank"
    assert stored.metadata["blocking_final_answer"] is True

    payload = json.loads(stored.text)
    assert payload["content_kind"] == "web_search_evidence_pack"
    assert payload["mode"] == "normal"
    assert payload["source_order"] == "reranked"
    assert payload["summary"]["candidate_page_count"] == 3
    assert payload["results"][0]["source_id"] == "1"
    assert payload["results"][0]["is_candidate_page"] is True
    assert payload["results"][0]["images"][0]["source_url"] == (
        "https://alpha.example/docs"
    )
    assert payload["candidate_pages"][0]["domain"] == "alpha.example"
    assert payload["citations"][0]["order"] == "reranked"


def test_web_search_deep_returns_receipt_only_artifact() -> None:
    coordinator = _StaticSearchCoordinator()
    tool = WebSearchTool(coordinator=coordinator)

    result = asyncio.run(
        tool.execute(
            {"session_id": "session-deep", "user_id": "user-1"},
            queries=["evidence protocol", "artifact rank"],
            mode="deep",
        )
    )

    content_id = _content_id_from_receipt(result)
    stored = tool_content_store.get(
        content_id=content_id,
        session_id="session-deep",
    )

    assert stored is not None
    assert "mode: deep" in result
    assert "Snippet:" not in result
    assert json.loads(stored.text)["mode"] == "deep"


def test_web_search_strict_parameter_validation() -> None:
    tool = WebSearchTool(coordinator=_StaticSearchCoordinator())
    context = {"session_id": "session-validation", "user_id": "user-1"}

    assert "mode is required" in asyncio.run(
        tool.execute(context, queries=["evidence protocol", "artifact rank"])
    )
    assert "mode must be one of" in asyncio.run(
        tool.execute(
            context,
            queries=["evidence protocol", "artifact rank"],
            mode="slow",
        )
    )
    assert "with_images must be a boolean" in asyncio.run(
        tool.execute(
            context,
            queries=["evidence protocol", "artifact rank"],
            mode="fast",
            with_images="true",
        )
    )
    assert "queries items must not contain leading" in asyncio.run(
        tool.execute(
            context,
            queries=[" evidence protocol", "artifact rank"],
            mode="fast",
        )
    )
    assert "queries must be distinct" in asyncio.run(
        tool.execute(
            context,
            queries=["evidence protocol", "evidence  protocol"],
            mode="fast",
        )
    )
    assert "wikipedia_keywords must be a list" in asyncio.run(
        tool.execute(
            context,
            queries=["evidence protocol", "artifact rank"],
            mode="normal",
            wikipedia_keywords="Protocol",
        )
    )
    assert "language must be one of" in asyncio.run(
        tool.execute(
            context,
            queries=["evidence protocol", "artifact rank"],
            mode="normal",
            language="fr",
        )
    )


def test_rank_evidence_ranks_web_search_artifact_results() -> None:
    receipt = cache_artifact_and_format_receipt(
        session_id="session-rank-web",
        tool_name="web_search",
        source="artifact rank",
        text=json.dumps(
            {
                "content_kind": "web_search_evidence_pack",
                "results": [
                    {
                        "source_id": "1",
                        "title": "Alpha receipt protocol",
                        "url": "https://alpha.example/docs",
                        "domain": "alpha.example",
                        "snippet": "Receipt-only artifacts require evidence rank.",
                    },
                    {
                        "source_id": "2",
                        "title": "Beta unrelated",
                        "url": "https://beta.example/docs",
                        "domain": "beta.example",
                        "snippet": "Different content.",
                    },
                    {
                        "source_id": "3",
                        "title": "Alpha follow up",
                        "url": "https://alpha.example/extra",
                        "domain": "alpha.example",
                        "snippet": "More receipt protocol evidence.",
                    },
                    {
                        "source_id": "4",
                        "title": "Alpha overflow",
                        "url": "https://alpha.example/overflow",
                        "domain": "alpha.example",
                        "snippet": "Receipt protocol overflow evidence.",
                    },
                ],
            }
        ),
        metadata={"content_kind": "web_search_evidence_pack"},
    )
    content_id = _content_id_from_receipt(receipt)

    result = rank_evidence(
        query="receipt protocol evidence",
        content_ids=[content_id],
        session_id="session-rank-web",
        max_evidence=4,
    )
    formatted = format_evidence_result(result)

    assert result.total_chunks_scanned == 4
    assert result.evidence
    assert all(ev.evidence_type == "web_search_result" for ev in result.evidence)
    assert result.evidence[0].source_id in {"1", "3", "4"}
    assert result.evidence[0].domain == "alpha.example"
    assert result.evidence[0].url.startswith("https://alpha.example/")
    assert len([ev for ev in result.evidence if ev.domain == "alpha.example"]) == 2
    assert "source_id:" in formatted
    assert "Evidence type: web_search_result" in formatted
    assert "These ranked items are search-result snippets" in formatted


def test_rank_evidence_handles_invalid_web_search_json_note() -> None:
    receipt = cache_artifact_and_format_receipt(
        session_id="session-rank-invalid",
        tool_name="web_search",
        source="broken",
        text="{not-json",
        metadata={"content_kind": "web_search_evidence_pack"},
    )
    content_id = _content_id_from_receipt(receipt)

    result = rank_evidence(
        query="anything",
        content_ids=[content_id],
        session_id="session-rank-invalid",
    )

    assert not result.evidence
    assert any("not valid JSON" in note for note in result.notes)


def test_format_evidence_result_uses_continuous_display_numbers() -> None:
    result = EvidenceRankResult(
        query="q",
        evidence=(
            RankedEvidence(
                content_id="cnt_a",
                chunk_index=-1,
                score=2.0,
                rank=4,
                title="Fourth raw",
                evidence_type="web_search_result",
            ),
            RankedEvidence(
                content_id="cnt_b",
                chunk_index=-1,
                score=1.0,
                rank=9,
                title="Ninth raw",
                evidence_type="web_search_result",
            ),
        ),
        total_chunks_scanned=2,
        content_ids_found=("cnt_a", "cnt_b"),
    )

    formatted = format_evidence_result(result)

    assert "\n[1]\n   Raw rank: 5" in formatted
    assert "\n[2]\n   Raw rank: 10" in formatted
    assert "\n[5]" not in formatted
    assert "\n[10]" not in formatted


def test_rank_evidence_keeps_generic_chunk_ranking() -> None:
    content_id = tool_content_store.put(
        session_id="session-rank-generic",
        tool_name="web_fetch",
        source="https://example.test",
        text="first chunk alpha\n\nsecond chunk receipt protocol evidence",
        metadata={"title": "Generic content", "url": "https://example.test"},
    )
    assert content_id is not None

    result = rank_evidence(
        query="receipt protocol evidence",
        content_ids=[content_id],
        session_id="session-rank-generic",
        max_evidence=1,
    )

    assert result.evidence
    assert result.evidence[0].evidence_type == "chunk"
    assert result.evidence[0].chunk_index >= 0


def test_rank_evidence_chunk_excerpt_uses_raw_chunk_text_and_display_title() -> None:
    tmp_source = "/tmp/wisepen-chat-upload-files/session/source-paper.pdf"
    content_id = tool_content_store.put(
        session_id="session-rank-display",
        tool_name="document_parse",
        source=tmp_source,
        text="raw chunk text with receipt protocol evidence only",
        metadata={},
    )
    assert content_id is not None

    result = rank_evidence(
        query="receipt protocol evidence",
        content_ids=[content_id],
        session_id="session-rank-display",
        max_evidence=1,
    )
    formatted = format_evidence_result(result)

    assert result.evidence
    evidence = result.evidence[0]
    assert evidence.title == "source-paper.pdf"
    assert tmp_source not in evidence.excerpt
    assert evidence.excerpt == "raw chunk text with receipt protocol evidence only"
    assert "Title: source-paper.pdf" in formatted
    assert tmp_source not in formatted
    assert "chunk_index" in formatted
    assert "before_chunks" in formatted
    assert "content_id and offset" not in formatted
    assert "start_offset:" in formatted
    assert "end_offset:" in formatted


def test_tool_content_read_supports_chunk_index_context_window() -> None:
    content_id = tool_content_store.put(
        session_id="session-read-chunk",
        tool_name="document_parse",
        source="large-doc",
        text=("A" * 4000) + "TARGETCHUNK" + ("B" * 4000) + "AFTERCHUNK",
        metadata={"title": "large-doc"},
    )
    assert content_id is not None

    tool = ToolContentReadTool()
    result = asyncio.run(
        tool.execute(
            {"session_id": "session-read-chunk"},
            content_id=content_id,
            chunk_index=1,
            before_chunks=1,
            after_chunks=1,
        )
    )

    assert "[ToolContent Metadata]" in result
    assert "chunk_index: 1" in result
    assert "start_chunk_index: 0" not in result
    assert "AAAAAAAAAAAAAAAAAAAA" in result
    assert "TARGETCHUNK" in result
    assert "AFTERCHUNK" in result


def test_evidence_rank_tool_strict_validation() -> None:
    tool = EvidenceRankTool()
    context = {"session_id": "session-tool-validation"}

    assert "query must be a string" in asyncio.run(
        tool.execute(context, query=1, content_ids=["cnt_a"])
    )
    assert "query must be a non-empty string" in asyncio.run(
        tool.execute(context, query="", content_ids=["cnt_a"])
    )
    assert "query must not contain leading" in asyncio.run(
        tool.execute(context, query=" q", content_ids=["cnt_a"])
    )
    assert "content_ids must be a list" in asyncio.run(
        tool.execute(context, query="q", content_ids="cnt_a")
    )
    assert "content_ids items must be strings" in asyncio.run(
        tool.execute(context, query="q", content_ids=[1])
    )
    assert "content_ids items must be non-empty" in asyncio.run(
        tool.execute(context, query="q", content_ids=[""])
    )
    assert "content_ids items must not contain leading" in asyncio.run(
        tool.execute(context, query="q", content_ids=[" cnt_a"])
    )
    assert "content_ids must be cnt_* values" in asyncio.run(
        tool.execute(context, query="q", content_ids=["file_ref_1"])
    )
    assert "max_evidence must be an integer" in asyncio.run(
        tool.execute(context, query="q", content_ids=["cnt_a"], max_evidence=True)
    )


def test_tool_content_read_tool_strict_validation() -> None:
    tool = ToolContentReadTool()
    context = {"session_id": "session-read-validation"}

    assert "content_id must be a string" in asyncio.run(
        tool.execute(context, content_id=1)
    )
    assert "content_id must be a non-empty string" in asyncio.run(
        tool.execute(context, content_id="")
    )
    assert "content_id must not contain leading" in asyncio.run(
        tool.execute(context, content_id=" cnt_a")
    )
    assert "content_id must be a cnt_* value" in asyncio.run(
        tool.execute(context, content_id="file_ref_1")
    )
    assert "offset must be an integer" in asyncio.run(
        tool.execute(context, content_id="cnt_a", offset=True)
    )
    assert "offset must be greater than or equal to 0" in asyncio.run(
        tool.execute(context, content_id="cnt_a", offset=-1)
    )
    assert "limit must be an integer" in asyncio.run(
        tool.execute(context, content_id="cnt_a", limit=True)
    )
    assert "limit must be greater than or equal to 1" in asyncio.run(
        tool.execute(context, content_id="cnt_a", limit=0)
    )
    assert f"less than or equal to {TOOL_RESULT_MAX_CHARS}" in asyncio.run(
        tool.execute(
            context,
            content_id="cnt_a",
            limit=TOOL_RESULT_MAX_CHARS + 1,
        )
    )
    assert "Use either chunk_index mode or offset mode" in asyncio.run(
        tool.execute(context, content_id="cnt_a", offset=0, chunk_index=1)
    )
    assert "before_chunks/after_chunks require chunk_index mode" in asyncio.run(
        tool.execute(context, content_id="cnt_a", before_chunks=1)
    )
    assert "chunk_index must be an integer" in asyncio.run(
        tool.execute(context, content_id="cnt_a", chunk_index=True)
    )
    assert "chunk_index must be greater than or equal to 0" in asyncio.run(
        tool.execute(context, content_id="cnt_a", chunk_index=-1)
    )
    assert "before_chunks must be an integer" in asyncio.run(
        tool.execute(context, content_id="cnt_a", chunk_index=1, before_chunks=True)
    )
    assert "before_chunks must be between 0 and 3" in asyncio.run(
        tool.execute(context, content_id="cnt_a", chunk_index=1, before_chunks=4)
    )
    assert "after_chunks must be an integer" in asyncio.run(
        tool.execute(context, content_id="cnt_a", chunk_index=1, after_chunks=True)
    )
    assert "after_chunks must be between 0 and 3" in asyncio.run(
        tool.execute(context, content_id="cnt_a", chunk_index=1, after_chunks=4)
    )
