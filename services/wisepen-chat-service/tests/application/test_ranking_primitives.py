from __future__ import annotations

import inspect
from uuid import uuid4

import pytest

import chat.application.algorithms.ranking.bm25 as bm25_module
from chat.application.algorithms.ranking import (
    FieldedDocument,
    RankedList,
    rank_documents_by_bm25,
    rank_fielded_bm25,
    score_fielded_bm25,
    tokenize_for_bm25,
    weighted_rrf,
)


def test_bm25_empty_documents_returns_empty_ranked() -> None:
    assert rank_documents_by_bm25("needle", []).ranked == ()


def test_bm25_empty_query_returns_zero_scores_in_input_order() -> None:
    result = rank_documents_by_bm25("", [("a", "alpha"), ("b", "beta")])

    assert [item.id for item in result.ranked] == ["a", "b"]
    assert [item.rank for item in result.ranked] == [0, 1]
    assert all(item.score == 0.0 for item in result.ranked)


def test_bm25_single_document_without_overlap_scores_zero() -> None:
    result = rank_documents_by_bm25("python", [("a", "java stream")])

    assert result.ranked[0].id == "a"
    assert result.ranked[0].score == 0.0


def test_bm25_single_document_with_overlap_scores_positive() -> None:
    result = rank_documents_by_bm25("python async", [("a", "python guide")])

    assert result.ranked[0].score > 0.0
    assert result.ranked[0].score < 1.0


def test_bm25_single_empty_document_scores_zero() -> None:
    result = rank_documents_by_bm25("python", [("a", "")])

    assert result.ranked[0].score == 0.0


def test_bm25_multi_document_ties_keep_input_order() -> None:
    result = rank_documents_by_bm25(
        "missing",
        [("a", "alpha"), ("b", "beta"), ("c", "gamma")],
    )

    assert [item.id for item in result.ranked] == ["a", "b", "c"]
    assert all(item.score == 0.0 for item in result.ranked)


def test_bm25_cache_hit_uses_same_fingerprint() -> None:
    cache_key = f"ranking-cache-{uuid4()}"
    documents = [("a", "python async"), ("b", "java stream"), ("c", "rust borrow")]

    first = rank_documents_by_bm25("python", documents, cache_key=cache_key)
    second = rank_documents_by_bm25("python", documents, cache_key=cache_key)

    assert not first.cache_hit
    assert second.cache_hit


def test_bm25_cache_key_with_changed_fingerprint_rebuilds() -> None:
    cache_key = f"ranking-cache-{uuid4()}"
    documents = [("a", "python async"), ("b", "java stream"), ("c", "rust borrow")]
    changed_documents = [
        ("a", "java stream"),
        ("b", "python ranking"),
        ("c", "rust borrow"),
    ]

    rank_documents_by_bm25("python", documents, cache_key=cache_key)
    changed = rank_documents_by_bm25("python", changed_documents, cache_key=cache_key)

    assert not changed.cache_hit
    assert changed.ranked[0].id == "b"


def test_fielded_bm25_single_document_without_field_hits_scores_zero() -> None:
    documents = [
        FieldedDocument(id="a", fields={"title": "alpha", "body": "beta"}),
    ]

    scores = score_fielded_bm25(
        "missing",
        documents,
        {"title": 3.0, "body": 1.0},
    )

    assert scores == {"a": 0.0}


def test_fielded_bm25_title_hit_can_outweigh_body_hit() -> None:
    documents = [
        FieldedDocument(id="title", fields={"title": "python", "body": ""}),
        FieldedDocument(id="body", fields={"title": "", "body": "python"}),
        FieldedDocument(id="none", fields={"title": "", "body": ""}),
    ]

    scores = score_fielded_bm25(
        "python",
        documents,
        {"title": 4.0, "body": 1.0},
    )

    assert scores["title"] > scores["body"]
    assert scores["none"] == 0.0


def test_fielded_bm25_heading_weight_is_applied() -> None:
    documents = [
        FieldedDocument(id="heading", fields={"heading": "python", "body": ""}),
        FieldedDocument(id="body", fields={"heading": "", "body": "python"}),
        FieldedDocument(id="none", fields={"heading": "", "body": ""}),
    ]

    scores = score_fielded_bm25(
        "python",
        documents,
        {"heading": 3.0, "body": 1.0},
    )

    assert scores["heading"] > scores["body"]


def test_fielded_bm25_empty_fields_do_not_create_scores() -> None:
    documents = [
        FieldedDocument(id="a", fields={"title": "", "body": ""}),
    ]

    scores = score_fielded_bm25("python", documents, {"title": 2.0, "body": 1.0})

    assert scores["a"] == 0.0


def test_fielded_bm25_multiple_fields_accumulate_stably() -> None:
    documents = [
        FieldedDocument(id="both", fields={"title": "needle", "body": "needle"}),
        FieldedDocument(id="title", fields={"title": "needle", "body": ""}),
        FieldedDocument(id="body", fields={"title": "", "body": "needle"}),
        FieldedDocument(id="none-a", fields={"title": "", "body": ""}),
        FieldedDocument(id="none-b", fields={"title": "", "body": ""}),
    ]

    scores = score_fielded_bm25(
        "needle",
        documents,
        {"title": 1.0, "body": 1.0},
    )

    assert scores["both"] > scores["title"]
    assert scores["both"] > scores["body"]
    assert (
        rank_fielded_bm25("needle", documents, {"title": 1.0, "body": 1.0})[0]
        == "both"
    )


def test_fielded_bm25_public_api_signatures_stay_stable() -> None:
    assert list(inspect.signature(score_fielded_bm25).parameters) == [
        "query",
        "documents",
        "field_weights",
    ]
    assert list(inspect.signature(rank_fielded_bm25).parameters) == [
        "query",
        "documents",
        "field_weights",
    ]


def test_fielded_bm25_reuses_field_index_across_queries(monkeypatch) -> None:
    build_calls = _count_bm25_index_builds(monkeypatch)
    suffix = uuid4().hex
    documents = [
        FieldedDocument(id=f"a-{suffix}", fields={"title": "python async"}),
        FieldedDocument(id=f"b-{suffix}", fields={"title": "java stream"}),
        FieldedDocument(id=f"c-{suffix}", fields={"title": "rust borrow"}),
    ]

    score_fielded_bm25("python", documents, {"title": 1.0})
    score_fielded_bm25("java", documents, {"title": 1.0})

    assert len(build_calls) == 1


def test_fielded_bm25_changed_field_text_rebuilds_index(monkeypatch) -> None:
    build_calls = _count_bm25_index_builds(monkeypatch)
    suffix = uuid4().hex
    documents = [
        FieldedDocument(id=f"a-{suffix}", fields={"title": "python async"}),
        FieldedDocument(id=f"b-{suffix}", fields={"title": "java stream"}),
        FieldedDocument(id=f"c-{suffix}", fields={"title": "rust borrow"}),
    ]
    changed_documents = [
        FieldedDocument(id=f"a-{suffix}", fields={"title": "go routine"}),
        FieldedDocument(id=f"b-{suffix}", fields={"title": "java stream"}),
        FieldedDocument(id=f"c-{suffix}", fields={"title": "rust borrow"}),
    ]

    score_fielded_bm25("python", documents, {"title": 1.0})
    score_fielded_bm25("python", changed_documents, {"title": 1.0})

    assert len(build_calls) == 2


def test_fielded_bm25_weight_change_reuses_field_index(monkeypatch) -> None:
    build_calls = _count_bm25_index_builds(monkeypatch)
    suffix = uuid4().hex
    documents = [
        FieldedDocument(id=f"a-{suffix}", fields={"title": "python async"}),
        FieldedDocument(id=f"b-{suffix}", fields={"title": "java stream"}),
        FieldedDocument(id=f"c-{suffix}", fields={"title": "rust borrow"}),
    ]

    base_scores = score_fielded_bm25("python", documents, {"title": 1.0})
    weighted_scores = score_fielded_bm25("python", documents, {"title": 4.0})

    assert len(build_calls) == 1
    for document in documents:
        assert weighted_scores[document.id] == pytest.approx(
            base_scores[document.id] * 4.0
        )


def test_fielded_bm25_different_fields_use_different_cache_entries(
    monkeypatch,
) -> None:
    build_calls = _count_bm25_index_builds(monkeypatch)
    suffix = uuid4().hex
    documents = [
        FieldedDocument(
            id=f"a-{suffix}",
            fields={"title": "python async", "body": "python async"},
        ),
        FieldedDocument(
            id=f"b-{suffix}",
            fields={"title": "java stream", "body": "java stream"},
        ),
        FieldedDocument(
            id=f"c-{suffix}",
            fields={"title": "rust borrow", "body": "rust borrow"},
        ),
    ]

    score_fielded_bm25("python", documents, {"title": 1.0, "body": 1.0})

    assert len(build_calls) == 2


def test_rrf_rejects_non_positive_k() -> None:
    with pytest.raises(ValueError, match="k must be > 0"):
        weighted_rrf([RankedList(name="a", ids=["x"])], k=0)


def test_rrf_rejects_negative_weight() -> None:
    with pytest.raises(ValueError, match="weight must be >= 0"):
        weighted_rrf([RankedList(name="a", ids=["x"], weight=-1.0)])


def test_rrf_duplicate_ids_in_one_list_count_only_first_occurrence() -> None:
    fused = weighted_rrf([RankedList(name="a", ids=["x", "x", "y"])], k=1)

    assert fused[0].id == "x"
    assert fused[0].score == pytest.approx(1 / 2)


def test_rrf_sources_are_deduplicated() -> None:
    fused = weighted_rrf(
        [
            RankedList(name="same", ids=["x"]),
            RankedList(name="same", ids=["x"]),
        ]
    )

    assert fused[0].sources == ("same",)


def test_rrf_source_order_is_stable_across_lists() -> None:
    fused = weighted_rrf(
        [
            RankedList(name="first", ids=["x"]),
            RankedList(name="second", ids=["x"]),
        ]
    )

    assert fused[0].sources == ("first", "second")


def test_rrf_score_ties_use_first_seen_order() -> None:
    fused = weighted_rrf(
        [
            RankedList(name="first", ids=["b"]),
            RankedList(name="second", ids=["a"]),
        ]
    )

    assert [item.id for item in fused] == ["b", "a"]


def test_rrf_zero_weight_list_is_allowed_without_score_contribution() -> None:
    fused = weighted_rrf([RankedList(name="zero", ids=["x"], weight=0.0)])

    assert fused[0].id == "x"
    assert fused[0].score == 0.0
    assert fused[0].sources == ("zero",)


def test_tokenizer_keeps_chinese_segmentation() -> None:
    tokens = tokenize_for_bm25("中文搜索排序算法")

    assert any(token in tokens for token in ["中文", "搜索", "排序"])


def test_tokenizer_keeps_english_technical_tokens() -> None:
    tokens = tokenize_for_bm25("Python asyncio HTTP2 parser")

    assert {"python", "asyncio", "http2", "parser"}.issubset(tokens)


def test_tokenizer_keeps_identifier_style_tokens() -> None:
    tokens = tokenize_for_bm25("snake_case dotted.path kebab-case")

    assert "snake_case" in tokens
    assert "dotted.path" in tokens
    assert "kebab-case" in tokens


def test_tokenizer_filters_stopwords() -> None:
    tokens = tokenize_for_bm25("the and python 的 搜索")

    assert "the" not in tokens
    assert "and" not in tokens
    assert "的" not in tokens
    assert "python" in tokens


def _count_bm25_index_builds(monkeypatch) -> list[tuple[tuple[str, str], ...]]:
    build_calls: list[tuple[tuple[str, str], ...]] = []
    original_build_bm25_index = bm25_module._build_bm25_index

    def wrapped_build_bm25_index(documents):
        build_calls.append(tuple(documents))
        return original_build_bm25_index(documents)

    monkeypatch.setattr(
        bm25_module,
        "_build_bm25_index",
        wrapped_build_bm25_index,
    )
    return build_calls
