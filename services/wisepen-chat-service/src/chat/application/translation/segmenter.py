from __future__ import annotations

import re
from typing import List


_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[。！？!?；;.\n])")


def split_text_for_translation(text: str, max_chars: int) -> List[str]:
    paragraphs = [paragraph.strip() for paragraph in text.splitlines() if paragraph.strip()]
    segments: List[str] = []

    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            segments.append(paragraph)
            continue

        sentences = [
            sentence.strip()
            for sentence in _SENTENCE_SPLIT_PATTERN.split(paragraph)
            if sentence.strip()
        ]
        if not sentences:
            sentences = [paragraph]

        buffer = ""
        for sentence in sentences:
            if len(sentence) > max_chars:
                if buffer:
                    segments.append(buffer)
                    buffer = ""
                segments.extend(_hard_split(sentence, max_chars))
                continue

            if not buffer or len(buffer) + len(sentence) <= max_chars:
                buffer += sentence
            else:
                segments.append(buffer)
                buffer = sentence

        if buffer:
            segments.append(buffer)

    return segments


def _hard_split(text: str, max_chars: int) -> List[str]:
    return [
        text[start : start + max_chars]
        for start in range(0, len(text), max_chars)
        if text[start : start + max_chars]
    ]
