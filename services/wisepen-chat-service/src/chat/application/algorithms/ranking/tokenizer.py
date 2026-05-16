from __future__ import annotations

import re
from typing import List

import jieba

_EN_STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "to",
    "and",
    "or",
    "in",
    "on",
    "for",
    "with",
    "by",
    "is",
    "are",
    "was",
    "were",
    "be",
    "as",
    "at",
}

_ZH_STOPWORDS = {"的", "了", "和", "是", "在", "与", "及", "或"}

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_EN = re.compile(r"[a-z0-9][a-z0-9_\-\.]*")


def tokenize_for_bm25(text: str) -> List[str]:
    normalized = (text or "").strip().lower()

    tokens = _EN.findall(normalized)

    chinese_text = " ".join(_CJK.findall(normalized))
    if chinese_text:
        tokens += [token for token in jieba.cut_for_search(chinese_text)]

    return [
        token
        for token in tokens
        if token.strip() and token not in _EN_STOPWORDS and token not in _ZH_STOPWORDS
    ]
