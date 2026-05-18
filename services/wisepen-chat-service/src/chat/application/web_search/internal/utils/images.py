from typing import List, Sequence, Set, Tuple

from chat.application.algorithms.url import canonicalize_url
from chat.application.web_search.models import ImageResult


def deduplicate_images(
    images: Sequence[ImageResult],
) -> Tuple[ImageResult, ...]:
    seen: Set[str] = set()
    deduped: List[ImageResult] = []

    for image in images:
        key = canonicalize_url(image.url)
        if not key or key in seen:
            continue

        seen.add(key)
        deduped.append(image)

    return tuple(deduped)
