from __future__ import annotations

import re
from difflib import SequenceMatcher


def normalize_title_key(title: str) -> str:
    text = (title or "").lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def fuzzy_title_match(left: str, right: str, *, threshold: float = 0.86) -> bool:
    left_key = normalize_title_key(left)
    right_key = normalize_title_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    if SequenceMatcher(None, left_key, right_key).ratio() >= threshold:
        return True

    left_tokens = set(left_key.split())
    right_tokens = set(right_key.split())
    if not left_tokens or not right_tokens:
        return False

    overlap = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return (overlap / union) >= threshold
