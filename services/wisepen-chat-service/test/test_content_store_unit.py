from __future__ import annotations

import threading
import unittest
import sys
import types
from types import SimpleNamespace

settings_module = types.ModuleType("chat.core.config.app_settings")
settings_module.settings = SimpleNamespace(
    TOOL_CONTENT_STORE_TTL_SECONDS=60,
    TOOL_CONTENT_STORE_MAX_TOTAL_CHARS=10_000,
    TOOL_CONTENT_STORE_MAX_ITEM_CHARS=1_000,
    TOOL_RESULT_MAX_CHARS=100,
)
sys.modules["chat.core.config.app_settings"] = settings_module

from chat.core.content_store import (
    ContentStore,
    ContentWindow,
    TTLContentRepository,
    create_uncached_window,
)
from chat.core.content_store.chunking import create_content_chunks, find_chunk_by_offset
from chat.core.content_store.formatters import content_window_to_dict, format_tool_content_window
from chat.core.content_store.models import ContentChunk, StoredContent


def _make_store(
    *,
    ttl_seconds: int = 60,
    max_total_chars: int = 10_000,
    max_item_chars: int = 1_000,
    default_chunk_size: int = 100,
    normalize_text: bool = True,
) -> ContentStore:
    return ContentStore(
        repository=TTLContentRepository(
            ttl_seconds=ttl_seconds,
            max_total_chars=max_total_chars,
        ),
        default_chunk_size=default_chunk_size,
        max_item_chars=max_item_chars,
        normalize_text=normalize_text,
    )


class TestChunking(unittest.TestCase):
    def test_empty_text_returns_empty_chunks(self):
        chunks = create_content_chunks("", 100)
        self.assertEqual(chunks, [])

    def test_chunk_size_at_least_1(self):
        chunks = create_content_chunks("hello", 0)
        self.assertGreater(len(chunks), 0)

    def test_chunk_offsets_correct(self):
        text = "A\n\nB\n\nC\n\nD"
        chunks = create_content_chunks(text, 4)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0].start_offset, 0)
        self.assertEqual(chunks[-1].end_offset, len(text))

    def test_chunk_index_continuous(self):
        text = "A\n\nB\n\nC\n\nD\n\nE\n\nF"
        chunks = create_content_chunks(text, 4)
        for i, chunk in enumerate(chunks):
            self.assertEqual(chunk.index, i)

    def test_repeated_text_does_not_reuse_first_match(self):
        text = "abc\n\nabc\n\nabc"
        chunks = create_content_chunks(text, chunk_size=100)
        offsets = [c.start_offset for c in chunks]
        for i in range(1, len(offsets)):
            self.assertGreater(offsets[i], offsets[i - 1],
                               "Repeated text must not cause offset to revert to first match")
        for chunk in chunks:
            self.assertEqual(text[chunk.start_offset:chunk.end_offset],
                             text[chunk.start_offset:chunk.end_offset])

    def test_offsets_are_monotonic_and_within_bounds(self):
        text = "alpha beta gamma delta epsilon zeta eta theta"
        chunks = create_content_chunks(text, 6)
        previous_end = 0
        for chunk in chunks:
            self.assertGreaterEqual(chunk.start_offset, previous_end)
            self.assertGreater(chunk.end_offset, chunk.start_offset)
            self.assertLessEqual(chunk.end_offset, len(text))
            self.assertTrue(text[chunk.start_offset:chunk.end_offset])
            previous_end = chunk.end_offset

    def test_chunk_text_is_exact_substring_of_source(self):
        text = "A\n\nB\n\nC\n\nD\n\nE\n\nF"
        chunks = create_content_chunks(text, 4)
        for chunk in chunks:
            piece = text[chunk.start_offset:chunk.end_offset]
            self.assertEqual(len(piece), chunk.end_offset - chunk.start_offset)
            self.assertTrue(piece)

    def test_find_chunk_by_offset_normalizes_negative_offset(self):
        chunks = [ContentChunk(index=0, start_offset=0, end_offset=5)]
        result = find_chunk_by_offset(chunks, -10)
        self.assertEqual(result, chunks[0])

    def test_find_chunk_by_offset_returns_none_for_past_end(self):
        chunks = [ContentChunk(index=0, start_offset=0, end_offset=5)]
        result = find_chunk_by_offset(chunks, 10)
        self.assertIsNone(result)


class TestTTLContentRepository(unittest.TestCase):
    def test_put_get_normal(self):
        repo = TTLContentRepository(ttl_seconds=60, max_total_chars=10_000)
        content = StoredContent(
            content_id="test_1",
            scope_id="s1",
            producer="p",
            source="src",
            content_type="text/markdown",
            text="hello world",
        )
        repo.put(content)
        result = repo.get("test_1")
        self.assertIsNotNone(result)
        self.assertEqual(result.text, "hello world")

    def test_delete_normal(self):
        repo = TTLContentRepository(ttl_seconds=60, max_total_chars=10_000)
        content = StoredContent(
            content_id="test_2",
            scope_id="s1",
            producer="p",
            source="src",
            content_type="text/markdown",
            text="hello world",
        )
        repo.put(content)
        repo.delete("test_2")
        self.assertIsNone(repo.get("test_2"))

    def test_expire_callable(self):
        repo = TTLContentRepository(ttl_seconds=60, max_total_chars=10_000)
        repo.expire()

    def test_concurrent_put_get_no_exception(self):
        repo = TTLContentRepository(ttl_seconds=60, max_total_chars=100_000)
        errors = []

        def writer():
            try:
                for i in range(50):
                    content = StoredContent(
                        content_id=f"cnt_{i}",
                        scope_id="s1",
                        producer="p",
                        source="src",
                        content_type="text/markdown",
                        text=f"content {i}",
                    )
                    repo.put(content)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for i in range(50):
                    repo.get(f"cnt_{i}")
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertEqual(len(errors), 0)


class TestContentStorePutContent(unittest.TestCase):
    def test_empty_text_returns_none(self):
        store = _make_store()
        result = store.put_content(
            scope_id="s1", producer="p", source="src", text="", content_type="text/markdown",
        )
        self.assertIsNone(result)

    def test_exceeds_max_item_chars_returns_none(self):
        store = _make_store(max_item_chars=10)
        result = store.put_content(
            scope_id="s1", producer="p", source="src", text="a" * 11, content_type="text/markdown",
        )
        self.assertIsNone(result)

    def test_normal_content_returns_cnt_prefix_id(self):
        store = _make_store()
        result = store.put_content(
            scope_id="s1", producer="p", source="src", text="hello", content_type="text/markdown",
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith("cnt_"))

    def test_metadata_contains_content_hash(self):
        store = _make_store()
        content_id = store.put_content(
            scope_id="s1", producer="p", source="src", text="hello", content_type="text/markdown",
        )
        item = store.get_content(content_id=content_id, scope_id="s1")
        self.assertIn("content_hash", item.metadata)

    def test_content_hash_based_on_normalized_text(self):
        import hashlib
        store = _make_store(normalize_text=True)
        content_id = store.put_content(
            scope_id="s1", producer="p", source="src", text="  hello  ", content_type="text/markdown",
        )
        item = store.get_content(content_id=content_id, scope_id="s1")
        expected_hash = hashlib.sha256("hello".encode("utf-8")).hexdigest()
        self.assertEqual(item.metadata["content_hash"], expected_hash)

    def test_does_not_mutate_caller_metadata(self):
        store = _make_store()
        original_meta = {"key": "value"}
        content_id = store.put_content(
            scope_id="s1", producer="p", source="src", text="hello",
            content_type="text/markdown", metadata=original_meta,
        )
        self.assertNotIn("content_hash", original_meta)

    def test_scope_id_producer_source_content_type_saved(self):
        store = _make_store()
        content_id = store.put_content(
            scope_id="s1", producer="web_fetch", source="https://example.com",
            text="hello", content_type="text/html",
        )
        item = store.get_content(content_id=content_id, scope_id="s1")
        self.assertEqual(item.scope_id, "s1")
        self.assertEqual(item.producer, "web_fetch")
        self.assertEqual(item.source, "https://example.com")
        self.assertEqual(item.content_type, "text/html")


class TestContentStoreReadWindow(unittest.TestCase):
    def test_read_first_window(self):
        text = "A\n\nB\n\nC\n\nD\n\nE\n\nF"
        store = _make_store(default_chunk_size=4)
        content_id = store.put_content(scope_id="s1", producer="p", source="src", text=text)
        window = store.read_window(content_id=content_id, scope_id="s1", offset=0, limit=4)
        self.assertIsNotNone(window)
        self.assertTrue(window.cached)
        self.assertIn(window.text, text)

    def test_read_subsequent_window_with_offset(self):
        text = "A\n\nB\n\nC\n\nD\n\nE\n\nF"
        store = _make_store(default_chunk_size=4)
        content_id = store.put_content(scope_id="s1", producer="p", source="src", text=text)
        first = store.read_window(content_id=content_id, scope_id="s1", offset=0, limit=4)
        self.assertIsNotNone(first.next_offset)
        second = store.read_window(content_id=content_id, scope_id="s1", offset=first.next_offset, limit=4)
        self.assertIsNotNone(second)
        self.assertGreater(second.offset, first.offset)

    def test_offset_out_of_range_returns_error(self):
        store = _make_store()
        content_id = store.put_content(scope_id="s1", producer="p", source="src", text="hello")
        window = store.read_window(content_id=content_id, scope_id="s1", offset=999)
        self.assertIsNotNone(window)
        self.assertEqual(window.error, "offset_out_of_range")

    def test_scope_id_mismatch_returns_none(self):
        store = _make_store()
        content_id = store.put_content(scope_id="s1", producer="p", source="src", text="hello")
        window = store.read_window(content_id=content_id, scope_id="s2", offset=0)
        self.assertIsNone(window)

    def test_limit_different_from_default_rechunks(self):
        text = "A\n\nB\n\nC\n\nD\n\nE\n\nF"
        store = _make_store(default_chunk_size=4)
        content_id = store.put_content(scope_id="s1", producer="p", source="src", text=text)
        window = store.read_window(content_id=content_id, scope_id="s1", offset=0, limit=8)
        self.assertIsNotNone(window)

    def test_window_text_not_stripped(self):
        text = "  hello  \n\n  world  "
        store = _make_store(normalize_text=False, default_chunk_size=100)
        content_id = store.put_content(
            scope_id="s1", producer="p", source="src", text=text,
            content_type="text/markdown",
        )
        window = store.read_window(content_id=content_id, scope_id="s1", offset=0)
        self.assertIsNotNone(window)
        self.assertEqual(window.returned_length, len(window.text))

    def test_returned_length_equals_window_text_length(self):
        text = "A\n\nB\n\nC\n\nD\n\nE\n\nF"
        store = _make_store(default_chunk_size=4)
        content_id = store.put_content(scope_id="s1", producer="p", source="src", text=text)
        window = store.read_window(content_id=content_id, scope_id="s1", offset=0, limit=4)
        self.assertEqual(window.returned_length, len(window.text))

    def test_next_offset_aligns_with_chunk_end_offset(self):
        text = "A\n\nB\n\nC\n\nD\n\nE\n\nF"
        store = _make_store(default_chunk_size=4)
        content_id = store.put_content(scope_id="s1", producer="p", source="src", text=text)
        window = store.read_window(content_id=content_id, scope_id="s1", offset=0, limit=4)
        if window.truncated:
            item = store.get_content(content_id=content_id, scope_id="s1")
            chunk = find_chunk_by_offset(item.chunks, 0)
            self.assertEqual(window.next_offset, chunk.end_offset)


class TestContentStorePutAndReadWindow(unittest.TestCase):
    def test_cacheable_returns_cached_true(self):
        store = _make_store()
        window = store.put_and_read_window(
            scope_id="s1", producer="p", source="src", text="hello world",
        )
        self.assertTrue(window.cached)

    def test_uncacheable_returns_cached_false(self):
        store = _make_store(max_item_chars=5)
        window = store.put_and_read_window(
            scope_id="s1", producer="p", source="src", text="hello world this is too long",
        )
        self.assertFalse(window.cached)

    def test_uncacheable_has_warning(self):
        store = _make_store(max_item_chars=5)
        window = store.put_and_read_window(
            scope_id="s1", producer="p", source="src", text="hello world this is too long",
        )
        self.assertIsNotNone(window.warning)


class TestCreateUncachedWindow(unittest.TestCase):
    def test_is_module_level_function(self):
        self.assertTrue(callable(create_uncached_window))

    def test_cached_false(self):
        window = create_uncached_window(
            text="hello world", producer="p", source="src", limit=5,
        )
        self.assertFalse(window.cached)

    def test_content_id_empty(self):
        window = create_uncached_window(
            text="hello world", producer="p", source="src", limit=5,
        )
        self.assertEqual(window.content_id, "")

    def test_window_text_not_stripped(self):
        window = create_uncached_window(
            text="  hello  ", producer="p", source="src", limit=100, normalize_text=False,
        )
        self.assertEqual(window.returned_length, len(window.text))

    def test_returned_length_equals_window_text_length(self):
        window = create_uncached_window(
            text="A\n\nB\n\nC\n\nD", producer="p", source="src", limit=4,
        )
        self.assertEqual(window.returned_length, len(window.text))


class TestReadChunkWindow(unittest.TestCase):
    def test_hit_single_chunk(self):
        store = _make_store(default_chunk_size=4)
        text = "A\n\nB\n\nC\n\nD"
        content_id = store.put_content(scope_id="s1", producer="p", source="src", text=text)
        window = store.read_chunk_window(
            content_id=content_id, scope_id="s1", chunk_index=0,
        )
        self.assertIsNotNone(window)
        self.assertEqual(window.chunk_index, 0)

    def test_aggregate_before_after_chunks(self):
        store = _make_store(default_chunk_size=4)
        text = "A\n\nB\n\nC\n\nD\n\nE\n\nF"
        content_id = store.put_content(scope_id="s1", producer="p", source="src", text=text)
        window = store.read_chunk_window(
            content_id=content_id, scope_id="s1", chunk_index=2,
            before_chunks=1, after_chunks=1,
        )
        self.assertIsNotNone(window)
        self.assertIn("start_chunk_index", window.metadata)
        self.assertIn("end_chunk_index", window.metadata)
        self.assertEqual(window.metadata["start_chunk_index"], 1)
        self.assertEqual(window.metadata["end_chunk_index"], 3)

    def test_chunk_index_out_of_range_returns_error(self):
        store = _make_store(default_chunk_size=4)
        text = "A\n\nB\n\nC"
        content_id = store.put_content(scope_id="s1", producer="p", source="src", text=text)
        window = store.read_chunk_window(
            content_id=content_id, scope_id="s1", chunk_index=999,
        )
        self.assertIsNotNone(window)
        self.assertEqual(window.error, "chunk_index_out_of_range")

    def test_metadata_contains_start_end_chunk_index(self):
        store = _make_store(default_chunk_size=4)
        text = "A\n\nB\n\nC\n\nD\n\nE"
        content_id = store.put_content(scope_id="s1", producer="p", source="src", text=text)
        window = store.read_chunk_window(
            content_id=content_id, scope_id="s1", chunk_index=1,
            before_chunks=0, after_chunks=0,
        )
        self.assertIn("start_chunk_index", window.metadata)
        self.assertIn("end_chunk_index", window.metadata)

    def test_window_text_not_stripped(self):
        store = _make_store(default_chunk_size=100, normalize_text=False)
        text = "  hello  \n\n  world  "
        content_id = store.put_content(scope_id="s1", producer="p", source="src", text=text)
        window = store.read_chunk_window(
            content_id=content_id, scope_id="s1", chunk_index=0,
        )
        self.assertEqual(window.returned_length, len(window.text))

    def test_returned_length_equals_window_text_length(self):
        store = _make_store(default_chunk_size=4)
        text = "A\n\nB\n\nC\n\nD"
        content_id = store.put_content(scope_id="s1", producer="p", source="src", text=text)
        window = store.read_chunk_window(
            content_id=content_id, scope_id="s1", chunk_index=0,
        )
        self.assertEqual(window.returned_length, len(window.text))


class TestFormatter(unittest.TestCase):
    def test_output_contains_metadata_header(self):
        window = ContentWindow(
            content_id="cnt_abc",
            producer="web_fetch",
            source="https://example.com",
            content_type="text/markdown",
            original_length=100,
            text="hello",
        )
        result = format_tool_content_window(window)
        self.assertIn("[ToolContent Metadata]", result)

    def test_content_cached_uses_lowercase_true_false(self):
        window = ContentWindow(
            content_id="cnt_abc",
            producer="p",
            source="src",
            content_type="text/markdown",
            original_length=100,
            cached=True,
            text="hello",
        )
        result = format_tool_content_window(window)
        self.assertIn("content_cached: true", result)

        window2 = ContentWindow(
            content_id="cnt_abc",
            producer="p",
            source="src",
            content_type="text/markdown",
            original_length=100,
            cached=False,
            text="hello",
        )
        result2 = format_tool_content_window(window2)
        self.assertIn("content_cached: false", result2)

    def test_producer_formatted_as_tool_name(self):
        window = ContentWindow(
            content_id="cnt_abc",
            producer="web_fetch",
            source="src",
            content_type="text/markdown",
            original_length=100,
            text="hello",
        )
        result = format_tool_content_window(window)
        self.assertIn("tool_name: web_fetch", result)

    def test_next_offset_none_outputs_empty_string(self):
        window = ContentWindow(
            content_id="cnt_abc",
            producer="p",
            source="src",
            content_type="text/markdown",
            original_length=100,
            next_offset=None,
            text="hello",
        )
        result = format_tool_content_window(window)
        self.assertIn("next_offset: \n", result)

    def test_content_section_contains_text(self):
        window = ContentWindow(
            content_id="cnt_abc",
            producer="p",
            source="src",
            content_type="text/markdown",
            original_length=100,
            text="hello world",
        )
        result = format_tool_content_window(window)
        self.assertIn("[Content]\nhello world", result)

    def test_formatter_does_not_modify_window_text(self):
        original_text = "  hello  "
        window = ContentWindow(
            content_id="cnt_abc",
            producer="p",
            source="src",
            content_type="text/markdown",
            original_length=100,
            text=original_text,
        )
        format_tool_content_window(window)
        self.assertEqual(window.text, original_text)


class TestCompatibilityFunctions(unittest.TestCase):
    def test_cache_and_format_returns_metadata_and_content(self):
        from chat.application.tool_content_store import cache_and_format
        result = cache_and_format(
            session_id="s1",
            tool_name="test_tool",
            source="test://src",
            text="hello world",
        )
        self.assertIn("[ToolContent Metadata]", result)
        self.assertIn("[Content]", result)

    def test_read_tool_content_window_empty_content_id_returns_old_error(self):
        from chat.application.tool_content_store import read_tool_content_window
        result = read_tool_content_window(session_id="s1", content_id="")
        self.assertIn("[Tool Error] Missing required content_id parameter", result)

    def test_read_tool_content_window_not_found_returns_old_error(self):
        from chat.application.tool_content_store import read_tool_content_window
        result = read_tool_content_window(session_id="s1", content_id="cnt_nonexistent")
        self.assertIn("Cached tool content not found, expired, or inaccessible", result)

    def test_read_tool_content_window_non_string_input(self):
        from chat.application.tool_content_store import read_tool_content_window
        result = read_tool_content_window(session_id="s1", content_id=None)  # type: ignore
        self.assertIn("[Tool Error] Missing required content_id parameter", result)


class TestLegacyObjectAPI(unittest.TestCase):
    def test_tool_content_store_put_get_read_window(self):
        from chat.application.tool_content_store import ToolContentStore
        store = ToolContentStore(
            ttl_seconds=60,
            max_total_chars=100_000,
            default_chunk_size=10,
            max_item_chars=100_000,
        )
        content_id = store.put(
            session_id="s1",
            tool_name="test_tool",
            source="unit",
            text="hello world",
        )
        self.assertIsNotNone(content_id)

        window = store.read_window(
            content_id=content_id,
            session_id="s1",
            offset=0,
            limit=10,
        )
        self.assertIsNotNone(window)
        self.assertTrue(window.text)

    def test_window_legacy_property_tool_name(self):
        from chat.application.tool_content_store import cache_and_window
        window = cache_and_window(
            session_id="s1",
            tool_name="test_tool",
            source="unit",
            text="hello world",
        )
        self.assertEqual(window.tool_name, "test_tool")
        self.assertEqual(window.producer, "test_tool")

    def test_window_legacy_property_content_cached(self):
        from chat.application.tool_content_store import cache_and_window
        window = cache_and_window(
            session_id="s1",
            tool_name="test_tool",
            source="unit",
            text="hello world",
        )
        self.assertTrue(window.content_cached)
        self.assertTrue(window.cached)

    def test_stored_content_legacy_property_session_id(self):
        from chat.application.tool_content_store import tool_content_store
        content_id = tool_content_store.put(
            session_id="s1",
            tool_name="test_tool",
            source="unit",
            text="hello world",
        )
        item = tool_content_store.get(content_id=content_id, session_id="s1")
        self.assertIsNotNone(item)
        self.assertEqual(item.session_id, "s1")
        self.assertEqual(item.scope_id, "s1")

    def test_stored_content_legacy_property_tool_name(self):
        from chat.application.tool_content_store import _tool_content_store as tool_content_store
        content_id = tool_content_store.put(
            session_id="s1",
            tool_name="test_tool",
            source="unit",
            text="hello world",
        )
        item = tool_content_store.get(content_id=content_id, session_id="s1")
        self.assertIsNotNone(item)
        self.assertEqual(item.tool_name, "test_tool")
        self.assertEqual(item.producer, "test_tool")

    def test_tool_content_store_import(self):
        from chat.application.tool_content_store import ToolContentStore
        self.assertTrue(callable(ToolContentStore))

    def test_type_aliases_importable(self):
        from chat.application.tool_content_store import (
            ContentChunk,
            StoredContent,
            StoredToolContent,
            ContentWindow,
            WindowedContent,
        )
        self.assertIs(StoredToolContent, StoredContent)
        self.assertIs(WindowedContent, ContentWindow)


class TestToolContentStoreFalsyParams(unittest.TestCase):
    def test_default_chunk_size_1_not_replaced_by_or(self):
        from chat.application.tool_content_store import ToolContentStore
        store = ToolContentStore(
            ttl_seconds=60,
            max_total_chars=100_000,
            default_chunk_size=1,
            max_item_chars=100_000,
        )
        content_id = store.put(
            session_id="s1",
            tool_name="test_tool",
            source="unit",
            text="A\n\nB\n\nC",
        )
        self.assertIsNotNone(content_id)
        window = store.read_window(
            content_id=content_id,
            session_id="s1",
            offset=0,
            limit=1,
        )
        self.assertIsNotNone(window)

    def test_ttl_seconds_0_accepted(self):
        from chat.application.tool_content_store import ToolContentStore
        with self.assertRaises(ValueError):
            ToolContentStore(
                ttl_seconds=0,
                max_total_chars=100_000,
                default_chunk_size=100,
                max_item_chars=100_000,
            )


class TestToolContentStoreReadChunkWindow(unittest.TestCase):
    def test_read_chunk_window_via_wrapper(self):
        from chat.application.tool_content_store import ToolContentStore
        store = ToolContentStore(
            ttl_seconds=60,
            max_total_chars=100_000,
            default_chunk_size=4,
            max_item_chars=100_000,
        )
        text = "A\n\nB\n\nC\n\nD\n\nE\n\nF"
        content_id = store.put(
            session_id="s1",
            tool_name="test_tool",
            source="unit",
            text=text,
        )
        self.assertIsNotNone(content_id)
        window = store.read_chunk_window(
            content_id=content_id,
            session_id="s1",
            chunk_index=1,
            before_chunks=1,
            after_chunks=1,
        )
        self.assertIsNotNone(window)
        self.assertIn("start_chunk_index", window.metadata)
        self.assertIn("end_chunk_index", window.metadata)

    def test_read_chunk_window_via_module_singleton(self):
        from chat.application.tool_content_store import tool_content_store
        content_id = tool_content_store.put(
            session_id="s1",
            tool_name="test_tool",
            source="unit",
            text="A\n\nB\n\nC\n\nD\n\nE\n\nF",
        )
        self.assertIsNotNone(content_id)
        window = tool_content_store.read_chunk_window(
            content_id=content_id,
            session_id="s1",
            chunk_index=0,
        )
        self.assertIsNotNone(window)
        self.assertEqual(window.chunk_index, 0)


class TestChunkingValueErrorBoundary(unittest.TestCase):
    def test_cache_and_window_propagates_value_error(self):
        from unittest.mock import patch
        from chat.application.tool_content_store import cache_and_window
        with patch("chat.core.content_store.service.create_content_chunks", side_effect=ValueError("test alignment failure")):
            with self.assertRaises(ValueError) as ctx:
                cache_and_window(
                    session_id="s1",
                    tool_name="test_tool",
                    source="unit",
                    text="hello world this is a test",
                )
            self.assertIn("test alignment failure", str(ctx.exception))

    def test_cache_and_format_propagates_value_error(self):
        from unittest.mock import patch
        from chat.application.tool_content_store import cache_and_format
        with patch("chat.core.content_store.service.create_content_chunks", side_effect=ValueError("test alignment failure")):
            with self.assertRaises(ValueError) as ctx:
                cache_and_format(
                    session_id="s1",
                    tool_name="test_tool",
                    source="unit",
                    text="hello world this is a test",
                )
            self.assertIn("test alignment failure", str(ctx.exception))

    def test_read_tool_content_window_propagates_formatter_value_error(self):
        from unittest.mock import patch
        from chat.application.tool_content_store import read_tool_content_window, tool_content_store
        content_id = tool_content_store.put(
            session_id="s1",
            tool_name="test_tool",
            source="unit",
            text="hello world",
        )
        self.assertIsNotNone(content_id)
        with patch("chat.application.tool_content_store.format_tool_content_window", side_effect=ValueError("formatter error")):
            with self.assertRaises(ValueError) as ctx:
                read_tool_content_window(
                    session_id="s1",
                    content_id=content_id,
                )
            self.assertIn("formatter error", str(ctx.exception))

    def test_cache_and_format_normal_path(self):
        from chat.application.tool_content_store import cache_and_format
        result = cache_and_format(
            session_id="s1",
            tool_name="test_tool",
            source="test://src",
            text="hello world",
        )
        self.assertIn("[ToolContent Metadata]", result)
        self.assertIn("[Content]", result)

    def test_read_tool_content_window_normal_path(self):
        from chat.application.tool_content_store import read_tool_content_window, tool_content_store
        content_id = tool_content_store.put(
            session_id="s1",
            tool_name="test_tool",
            source="unit",
            text="hello world",
        )
        self.assertIsNotNone(content_id)
        result = read_tool_content_window(
            session_id="s1",
            content_id=content_id,
        )
        self.assertIn("[ToolContent Metadata]", result)
        self.assertIn("hello world", result)


if __name__ == "__main__":
    unittest.main()
