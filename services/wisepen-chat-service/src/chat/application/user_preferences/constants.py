from __future__ import annotations

from typing import FrozenSet

DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_LOCALE = "zh-CN"

ALLOWED_LOCALES: FrozenSet[str] = frozenset(
    {
        "zh-CN",
        "zh-TW",
        "zh-HK",
        "en-US",
        "en-GB",
        "ja-JP",
        "ko-KR",
    }
)
