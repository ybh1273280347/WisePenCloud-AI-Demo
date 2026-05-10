from __future__ import annotations

import unittest
import sys
import types
from types import SimpleNamespace

settings_module = types.ModuleType("chat.core.config.app_settings")
settings_module.settings = SimpleNamespace(
    TOOL_CONTENT_STORE_TTL_SECONDS=60,
    TOOL_CONTENT_STORE_MAX_TOTAL_CHARS=10_000,
    TOOL_RESULT_MAX_CHARS=24,
)
sys.modules["chat.core.config.app_settings"] = settings_module

from chat.application.tool_content_store import ToolContentStore, _create_content_chunks


class ToolContentStoreOffsetTest(unittest.TestCase):
    def test_chunks_use_stable_contiguous_offsets_for_repeated_text(self) -> None:
        text = "\n\n".join(["repeat paragraph line"] * 6)

        chunks = _create_content_chunks(text, chunk_size=24)

        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0].start_offset, 0)
        self.assertEqual(chunks[-1].end_offset, len(text))

        previous_end = 0
        for chunk in chunks:
            self.assertEqual(chunk.start_offset, previous_end)
            self.assertEqual(
                text[chunk.start_offset:chunk.end_offset],
                text[previous_end:chunk.end_offset],
            )
            previous_end = chunk.end_offset

    def test_read_window_next_offset_does_not_skip_boundary_characters(self) -> None:
        text = "\n\n".join(["repeat paragraph line"] * 6)
        store = ToolContentStore(
            ttl_seconds=60,
            max_total_chars=10_000,
            default_chunk_size=24,
        )

        content_id = store.put(
            session_id="session-1",
            tool_name="unit_test",
            source="memory://offset-stability",
            text=text,
        )

        self.assertIsNotNone(content_id)

        item = store.get(content_id=content_id or "", session_id="session-1")
        self.assertIsNotNone(item)
        self.assertGreater(len(item.chunks), 1)

        offset = 0
        for chunk in item.chunks:
            window = store.read_window(
                content_id=content_id or "",
                session_id="session-1",
                offset=offset,
                limit=24,
            )

            self.assertIsNotNone(window)
            self.assertEqual(window.offset, chunk.start_offset)
            self.assertEqual(window.next_offset, chunk.end_offset if window.truncated else None)
            offset = window.next_offset or chunk.end_offset

    def test_repeated_ab_pattern_sequential_read_does_not_jump_back(self) -> None:
        text = "A\n\nB\n\nA\n\nB"
        store = ToolContentStore(
            ttl_seconds=60,
            max_total_chars=10_000,
            default_chunk_size=4,
        )

        content_id = store.put(
            session_id="session-1",
            tool_name="unit_test",
            source="memory://ab-repeat",
            text=text,
        )
        self.assertIsNotNone(content_id)

        seen_offsets: list[int] = []
        offset = 0
        for _ in range(20):
            window = store.read_window(
                content_id=content_id or "",
                session_id="session-1",
                offset=offset,
                limit=4,
            )
            if window is None or window.error:
                break
            seen_offsets.append(window.offset)
            if window.next_offset is None:
                break
            offset = window.next_offset

        for i in range(1, len(seen_offsets)):
            self.assertGreater(seen_offsets[i], seen_offsets[i - 1],
                               f"offset must be monotonically increasing, but {seen_offsets[i]} <= {seen_offsets[i - 1]}")

    def test_chunk_offsets_monotonic_with_duplicate_short_sentences(self) -> None:
        text = "xy\n\nxy\n\nxy\n\nxy"
        chunks = _create_content_chunks(text, chunk_size=4)

        offsets = [c.start_offset for c in chunks]
        for i in range(1, len(offsets)):
            self.assertGreater(offsets[i], offsets[i - 1],
                               f"chunk offsets must be monotonically increasing, but chunk {i} start {offsets[i]} <= chunk {i - 1} start {offsets[i - 1]}")


if __name__ == "__main__":
    unittest.main()
