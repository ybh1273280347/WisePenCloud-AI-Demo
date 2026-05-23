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
from chat.application.tools.knowledge.tool_content_batch_read_tool import (
    ToolContentBatchReadTool,
)
from chat.application.tools.knowledge.tool_content_read_tool import ToolContentReadTool
from chat.application.tools.services.evidence_ranking import (
    format_evidence_result,
    rank_evidence,
)
from chat.application.tools.services.evidence_ranking.models import (
    EvidenceFieldHitStat,
    EvidenceRankResult,
    EvidenceTermHitStat,
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


def test_evidence_rank_models_term_hit_stats_defaults() -> None:
    field_stat = EvidenceFieldHitStat(field="body", count=2)
    term_stat = EvidenceTermHitStat(term="苹果", total_count=2)
    evidence = RankedEvidence(
        content_id="cnt_a",
        chunk_index=0,
        score=1.0,
        rank=0,
    )

    assert field_stat.field == "body"
    assert field_stat.count == 2
    assert term_stat.term == "苹果"
    assert term_stat.total_count == 2
    assert term_stat.field_stats == ()
    assert evidence.term_hit_stats == ()


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
                    {
                        "source_id": "5",
                        "title": "Gamma unrelated",
                        "url": "https://gamma.example/docs",
                        "domain": "gamma.example",
                        "snippet": "Different content.",
                    },
                    {
                        "source_id": "6",
                        "title": "Delta unrelated",
                        "url": "https://delta.example/docs",
                        "domain": "delta.example",
                        "snippet": "Different content.",
                    },
                    {
                        "source_id": "7",
                        "title": "Epsilon unrelated",
                        "url": "https://epsilon.example/docs",
                        "domain": "epsilon.example",
                        "snippet": "Different content.",
                    },
                    {
                        "source_id": "8",
                        "title": "Zeta unrelated",
                        "url": "https://zeta.example/docs",
                        "domain": "zeta.example",
                        "snippet": "Different content.",
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

    assert result.total_chunks_scanned == 8
    assert result.evidence
    assert all(ev.evidence_type == "web_search_result" for ev in result.evidence)
    assert result.evidence[0].source_id in {"1", "3", "4"}
    assert result.evidence[0].domain == "alpha.example"
    assert result.evidence[0].url.startswith("https://alpha.example/")
    assert len([ev for ev in result.evidence if ev.domain == "alpha.example"]) == 2
    assert "source_id:" in formatted
    assert "Evidence type: web_search_result" in formatted
    assert "These ranked items are search-result snippets" in formatted
    assert "Term hit stats:" in formatted
    assert "Matched reason: Matched BM25 query terms in" in formatted


def test_rank_evidence_web_search_term_hit_stats_cover_fields() -> None:
    receipt = cache_artifact_and_format_receipt(
        session_id="session-rank-web-fields",
        tool_name="web_search",
        source="artifact rank",
        text=json.dumps(
            {
                "content_kind": "web_search_evidence_pack",
                "results": [
                    {
                        "source_id": "1",
                        "title": "苹果 保存",
                        "url": "https://保存.example/docs",
                        "domain": "保存.example",
                        "snippet": "苹果 需要 保存",
                    }
                ],
            }
        ),
        metadata={"content_kind": "web_search_evidence_pack"},
    )
    content_id = _content_id_from_receipt(receipt)

    result = rank_evidence(
        query="苹果 保存",
        content_ids=[content_id],
        session_id="session-rank-web-fields",
        max_evidence=1,
    )

    assert result.evidence
    stats = {stat.term: stat for stat in result.evidence[0].term_hit_stats}
    assert stats["苹果"].total_count == 2
    assert {item.field: item.count for item in stats["苹果"].field_stats} == {
        "title": 1,
        "snippet": 1,
    }
    assert stats["保存"].total_count == 3
    assert {item.field: item.count for item in stats["保存"].field_stats} == {
        "title": 1,
        "domain": 1,
        "snippet": 1,
    }


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
    assert result.evidence[0].matched_reason.startswith(
        "Matched BM25 query terms in"
    )


def test_rank_evidence_generic_term_hit_stats_cover_title_heading_body() -> None:
    content_id = tool_content_store.put(
        session_id="session-rank-term-stats",
        tool_name="document_parse",
        source="sample.md",
        text="苹果需要低温保存。苹果成熟后也可以做成果酱。",
        metadata={
            "title": "苹果保存指南",
            "heading_path": ["保存"],
        },
    )
    assert content_id is not None

    result = rank_evidence(
        query="苹果 保存",
        content_ids=[content_id],
        session_id="session-rank-term-stats",
        max_evidence=1,
    )
    formatted = format_evidence_result(result)

    assert result.evidence
    evidence = result.evidence[0]
    stats = {stat.term: stat for stat in evidence.term_hit_stats}
    assert stats["苹果"].total_count == 3
    assert {item.field: item.count for item in stats["苹果"].field_stats} == {
        "title": 1,
        "body": 2,
    }
    assert stats["保存"].total_count == 3
    assert {item.field: item.count for item in stats["保存"].field_stats} == {
        "title": 1,
        "heading": 1,
        "body": 1,
    }
    assert "Term hit stats:" in formatted
    assert "- 苹果: total=3; title=1, body=2" in formatted
    assert "- 保存: total=3; title=1, heading=1, body=1" in formatted


def test_rank_evidence_fielded_bm25_title_weight_beats_body_repetition() -> None:
    title_content_id = tool_content_store.put(
        session_id="session-rank-fielded-title",
        tool_name="document_parse",
        source="title.md",
        text="普通正文",
        metadata={"title": "苹果种植指南"},
    )
    body_content_id = tool_content_store.put(
        session_id="session-rank-fielded-title",
        tool_name="document_parse",
        source="body.md",
        text="苹果 苹果 苹果",
        metadata={"title": "水果说明"},
    )
    assert title_content_id is not None
    assert body_content_id is not None
    distractor_ids = [
        tool_content_store.put(
            session_id="session-rank-fielded-title",
            tool_name="document_parse",
            source=f"distractor-{index}.md",
            text=f"无关正文 {index}",
            metadata={"title": f"无关标题 {index}"},
        )
        for index in range(3)
    ]
    assert all(content_id is not None for content_id in distractor_ids)

    result = rank_evidence(
        query="苹果",
        content_ids=[
            title_content_id,
            body_content_id,
            *(content_id for content_id in distractor_ids if content_id is not None),
        ],
        session_id="session-rank-fielded-title",
        max_evidence=2,
    )

    assert len(result.evidence) == 2
    assert result.evidence[0].content_id == title_content_id


def test_rank_evidence_fielded_bm25_keeps_original_order_on_equal_scores() -> None:
    first_id = tool_content_store.put(
        session_id="session-rank-stable",
        tool_name="document_parse",
        source="first.md",
        text="第一段内容",
        metadata={"title": "stabletoken"},
    )
    second_id = tool_content_store.put(
        session_id="session-rank-stable",
        tool_name="document_parse",
        source="second.md",
        text="第二段内容",
        metadata={"title": "stabletoken"},
    )
    assert first_id is not None
    assert second_id is not None
    distractor_ids = [
        tool_content_store.put(
            session_id="session-rank-stable",
            tool_name="document_parse",
            source=f"stable-distractor-{index}.md",
            text=f"unrelated {index}",
            metadata={"title": f"unrelated {index}"},
        )
        for index in range(3)
    ]
    assert all(content_id is not None for content_id in distractor_ids)

    result = rank_evidence(
        query="stabletoken",
        content_ids=[
            first_id,
            second_id,
            *(content_id for content_id in distractor_ids if content_id is not None),
        ],
        session_id="session-rank-stable",
        max_evidence=2,
    )

    assert [item.content_id for item in result.evidence] == [first_id, second_id]


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


def test_format_evidence_result_outputs_batch_read_example_for_multiple_chunks() -> None:
    result = EvidenceRankResult(
        query="q",
        evidence=(
            RankedEvidence(
                content_id="cnt_a",
                chunk_index=1,
                score=2.0,
                rank=0,
                title="A",
            ),
            RankedEvidence(
                content_id="cnt_b",
                chunk_index=2,
                score=1.0,
                rank=1,
                title="B",
            ),
        ),
        total_chunks_scanned=2,
        content_ids_found=("cnt_a", "cnt_b"),
    )

    formatted = format_evidence_result(result)

    assert "tool_content_read" in formatted
    assert "tool_content_batch_read" in formatted
    assert "thematically related" in formatted


def test_tool_content_batch_read_single_and_multiple_items() -> None:
    first_id = tool_content_store.put(
        session_id="session-batch-read",
        tool_name="document_parse",
        source="first",
        text=("A" * 4000) + "FIRST_TARGET" + ("B" * 4000),
        metadata={"title": "first"},
    )
    second_id = tool_content_store.put(
        session_id="session-batch-read",
        tool_name="document_parse",
        source="second",
        text=("C" * 4000) + "SECOND_TARGET" + ("D" * 4000),
        metadata={"title": "second"},
    )
    assert first_id is not None
    assert second_id is not None

    tool = ToolContentBatchReadTool()
    single = asyncio.run(
        tool.execute(
            {"session_id": "session-batch-read"},
            items=[{"content_id": first_id, "chunk_index": 1}],
        )
    )
    multiple = asyncio.run(
        tool.execute(
            {"session_id": "session-batch-read"},
            items=[
                {
                    "content_id": first_id,
                    "chunk_index": 1,
                    "before_chunks": 0,
                    "after_chunks": 0,
                },
                {
                    "content_id": second_id,
                    "chunk_index": 1,
                    "before_chunks": 0,
                    "after_chunks": 0,
                },
            ],
        )
    )

    assert "Requested: 1 item(s)" in single
    assert "Returned: 1 window(s)" in single
    assert "FIRST_TARGET" in single
    assert "Requested: 2 item(s)" in multiple
    assert "Returned: 2 window(s)" in multiple
    assert "FIRST_TARGET" in multiple
    assert "SECOND_TARGET" in multiple


def test_tool_content_batch_read_handles_missing_and_window_boundary_limit() -> None:
    content_id = tool_content_store.put(
        session_id="session-batch-limit",
        tool_name="document_parse",
        source="large",
        text=("A" * 4000) + "TARGET" + ("B" * 4000),
        metadata={"title": "large"},
    )
    assert content_id is not None

    tool = ToolContentBatchReadTool()
    missing = asyncio.run(
        tool.execute(
            {"session_id": "session-batch-limit"},
            items=[
                {"content_id": content_id, "chunk_index": 1},
                {"content_id": "cnt_missing", "chunk_index": 1},
            ],
        )
    )
    limited = asyncio.run(
        tool.execute(
            {"session_id": "session-batch-limit"},
            items=[
                {"content_id": content_id, "chunk_index": 1},
                {"content_id": "cnt_missing", "chunk_index": 1},
            ],
            max_total_chars=1000,
        )
    )

    assert "Returned: 2 window(s)" in missing
    assert "error: cached tool content not found" in missing
    assert "Skipped: 0 item(s)" in missing
    assert "Skipped items:" not in missing
    assert "Returned: 0 window(s)" in limited
    assert "Skipped: 2 item(s)" in limited
    assert "Skip reason: max_total_chars_exceeded" in limited
    assert "Skipped items:" in limited
    assert f"content_id: {content_id}" in limited
    assert "content_id: cnt_missing" in limited
    assert "reason: max_total_chars_exceeded" in limited
    assert "TARGET" not in limited


def test_tool_content_batch_read_skips_remaining_items_at_window_boundary() -> None:
    first_id = tool_content_store.put(
        session_id="session-batch-boundary",
        tool_name="document_parse",
        source="first",
        text="FIRST_SMALL",
        metadata={"title": "first"},
    )
    second_id = tool_content_store.put(
        session_id="session-batch-boundary",
        tool_name="document_parse",
        source="second",
        text=("B" * 4000) + "SECOND_TARGET" + ("C" * 4000),
        metadata={"title": "second"},
    )
    third_id = tool_content_store.put(
        session_id="session-batch-boundary",
        tool_name="document_parse",
        source="third",
        text="THIRD_SMALL",
        metadata={"title": "third"},
    )
    assert first_id is not None
    assert second_id is not None
    assert third_id is not None

    tool = ToolContentBatchReadTool()
    result = asyncio.run(
        tool.execute(
            {"session_id": "session-batch-boundary"},
            items=[
                {"content_id": first_id, "chunk_index": 0},
                {"content_id": second_id, "chunk_index": 1},
                {"content_id": third_id, "chunk_index": 0},
            ],
            max_total_chars=1200,
        )
    )

    assert "Returned: 1 window(s)" in result
    assert "Skipped: 2 item(s)" in result
    assert "FIRST_SMALL" in result
    assert "SECOND_TARGET" not in result
    assert "THIRD_SMALL" not in result
    assert "Skipped items:" in result
    assert "[2]" in result
    assert f"content_id: {second_id}" in result
    assert "[3]" in result
    assert f"content_id: {third_id}" in result


def test_tool_content_batch_read_outputs_target_chunk_structure() -> None:
    content_id = tool_content_store.put(
        session_id="session-batch-structure",
        tool_name="document_parse",
        source="structured",
        text=("A" * 4000) + "STRUCTURED_TARGET" + ("B" * 4000),
        metadata={"title": "structured"},
    )
    assert content_id is not None
    stored = tool_content_store.get(
        content_id=content_id,
        session_id="session-batch-structure",
    )
    assert stored is not None
    stored.chunks[1].metadata = {
        "heading_path": ["3 Fruits", "3.2 Apple Storage"],
        "page_number": 8,
        "section_type": "method",
    }

    result = asyncio.run(
        ToolContentBatchReadTool().execute(
            {"session_id": "session-batch-structure"},
            items=[
                {
                    "content_id": content_id,
                    "chunk_index": 1,
                    "before_chunks": 1,
                    "after_chunks": 1,
                }
            ],
        )
    )

    assert "target_heading_path: 3 Fruits > 3.2 Apple Storage" in result
    assert "target_page_number: 8" in result
    assert "target_section_type: method" in result
    assert result.index("target_heading_path") < result.index("returned_length")


def test_tool_content_batch_read_ignores_invalid_or_missing_structure_metadata() -> None:
    invalid_id = tool_content_store.put(
        session_id="session-batch-invalid-metadata",
        tool_name="document_parse",
        source="invalid",
        text=("A" * 4000) + "INVALID_TARGET" + ("B" * 4000),
        metadata={"title": "invalid"},
    )
    missing_id = tool_content_store.put(
        session_id="session-batch-invalid-metadata",
        tool_name="document_parse",
        source="missing",
        text=("C" * 4000) + "MISSING_TARGET" + ("D" * 4000),
        metadata={"title": "missing"},
    )
    assert invalid_id is not None
    assert missing_id is not None
    stored = tool_content_store.get(
        content_id=invalid_id,
        session_id="session-batch-invalid-metadata",
    )
    assert stored is not None
    stored.chunks[1].metadata = {
        "heading_path": ["3 Fruits", 123],
        "page_number": "8",
        "section_type": " method ",
    }

    result = asyncio.run(
        ToolContentBatchReadTool().execute(
            {"session_id": "session-batch-invalid-metadata"},
            items=[
                {
                    "content_id": invalid_id,
                    "chunk_index": 1,
                    "before_chunks": 0,
                    "after_chunks": 0,
                },
                {
                    "content_id": missing_id,
                    "chunk_index": 1,
                    "before_chunks": 0,
                    "after_chunks": 0,
                },
            ],
            max_total_chars=12000,
        )
    )

    assert "[Tool Error]" not in result
    assert "INVALID_TARGET" in result
    assert "MISSING_TARGET" in result
    assert "target_heading_path" not in result
    assert "target_page_number" not in result
    assert "target_section_type" not in result


def test_tool_content_batch_read_strict_validation() -> None:
    tool = ToolContentBatchReadTool()
    context = {"session_id": "session-batch-validation"}

    invalid = asyncio.run(tool.execute(context, items="x"))
    assert invalid.startswith("[Tool Error]")
    assert "items must be a list" in invalid
    assert "Skipped items:" not in invalid
    assert "items must contain at least one item" in asyncio.run(
        tool.execute(context, items=[])
    )
    assert "items must contain at most 8 items" in asyncio.run(
        tool.execute(
            context,
            items=[
                {"content_id": f"cnt_{index}", "chunk_index": index}
                for index in range(9)
            ],
        )
    )
    assert "items[1] must be an object" in asyncio.run(tool.execute(context, items=[1]))
    assert "content_id must be a string" in asyncio.run(
        tool.execute(context, items=[{"content_id": 1, "chunk_index": 0}])
    )
    assert "content_id must be a non-empty string" in asyncio.run(
        tool.execute(context, items=[{"content_id": "", "chunk_index": 0}])
    )
    assert "content_id must not contain leading" in asyncio.run(
        tool.execute(context, items=[{"content_id": " cnt_a", "chunk_index": 0}])
    )
    assert "content_id must be a cnt_* value" in asyncio.run(
        tool.execute(context, items=[{"content_id": "file_ref_1", "chunk_index": 0}])
    )
    assert "chunk_index must be an integer" in asyncio.run(
        tool.execute(context, items=[{"content_id": "cnt_a", "chunk_index": True}])
    )
    assert "chunk_index must be greater than or equal to 0" in asyncio.run(
        tool.execute(context, items=[{"content_id": "cnt_a", "chunk_index": -1}])
    )
    assert "before_chunks must be an integer" in asyncio.run(
        tool.execute(
            context,
            items=[
                {
                    "content_id": "cnt_a",
                    "chunk_index": 0,
                    "before_chunks": True,
                }
            ],
        )
    )
    assert "before_chunks must be between 0 and 3" in asyncio.run(
        tool.execute(
            context,
            items=[{"content_id": "cnt_a", "chunk_index": 0, "before_chunks": 4}],
        )
    )
    assert "after_chunks must be an integer" in asyncio.run(
        tool.execute(
            context,
            items=[
                {
                    "content_id": "cnt_a",
                    "chunk_index": 0,
                    "after_chunks": True,
                }
            ],
        )
    )
    assert "after_chunks must be between 0 and 3" in asyncio.run(
        tool.execute(
            context,
            items=[{"content_id": "cnt_a", "chunk_index": 0, "after_chunks": 4}],
        )
    )
    assert "max_total_chars must be an integer" in asyncio.run(
        tool.execute(
            context,
            items=[{"content_id": "cnt_a", "chunk_index": 0}],
            max_total_chars=True,
        )
    )
    assert "max_total_chars must be between 1000 and 30000" in asyncio.run(
        tool.execute(
            context,
            items=[{"content_id": "cnt_a", "chunk_index": 0}],
            max_total_chars=999,
        )
    )
    assert "duplicate content_id + chunk_index" in asyncio.run(
        tool.execute(
            context,
            items=[
                {"content_id": "cnt_a", "chunk_index": 0},
                {"content_id": "cnt_a", "chunk_index": 0},
            ],
        )
    )


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
