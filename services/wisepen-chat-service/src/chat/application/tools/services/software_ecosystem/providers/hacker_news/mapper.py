from __future__ import annotations

from typing import Any, Dict, Optional

from chat.application.tools.services.software_ecosystem.common.formatting import compact_text
from chat.application.tools.services.software_ecosystem.community.models import (
    CommunityDiscussionSignal,
)


def map_hacker_news_hit(item: Dict[str, Any]) -> CommunityDiscussionSignal | None:
    title = _as_str(item.get("title")) or _as_str(item.get("story_title"))
    if not title:
        return None
    url = _as_str(item.get("url")) or f"https://news.ycombinator.com/item?id={item.get('objectID')}"
    return CommunityDiscussionSignal(
        source="hacker_news",
        title=title,
        url=url,
        published_at=_as_str(item.get("created_at")),
        points=_as_int(item.get("points")),
        comments_count=_as_int(item.get("num_comments")),
        summary=compact_text(item.get("story_text") or item.get("comment_text"), max_chars=500),
        matched_terms=[],
    )


def _as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

