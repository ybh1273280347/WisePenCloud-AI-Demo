from typing import List, Sequence, Set, Tuple

from chat.application.web_search.models import ImageResult
from chat.application.web_search.utils.urls import normalize_url_for_dedup

def deduplicate_images(
    images: Sequence[ImageResult],
) -> Tuple[ImageResult, ...]:
    seen: Set[str] = set()
    deduped: List[ImageResult] = []

    for image in images:
        key = normalize_url_for_dedup(image.url)
        if not key or key in seen:
            continue

        seen.add(key)
        deduped.append(image)

    return tuple(deduped)
