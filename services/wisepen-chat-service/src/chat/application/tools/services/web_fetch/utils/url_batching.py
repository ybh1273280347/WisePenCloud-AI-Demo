import re
from typing import Any, List

MAX_FETCH_URLS = 20

_URL_PATTERN = re.compile(r"https?://[^\s<>'\"\]\[]+")

_TRAILING_PUNCTUATION = ".,;:!?，。；：！？"


class UrlBatchInputError(ValueError):
    pass


def normalize_urls(raw_urls: Any, *, max_urls: int = MAX_FETCH_URLS) -> List[str]:
    if isinstance(raw_urls, str):
        raw_items = [raw_urls]
    elif isinstance(raw_urls, list):
        raw_items = raw_urls
    else:
        raise UrlBatchInputError("urls must be a list of URL strings.")

    urls: List[str] = []
    seen = set()

    for item in raw_items:
        if not isinstance(item, str):
            raise UrlBatchInputError("every urls item must be a string.")

        text = item.strip()
        if not text:
            continue

        matches = _URL_PATTERN.findall(text)
        candidates = matches if matches else [text]

        for candidate in candidates:
            url = candidate.strip().rstrip(_TRAILING_PUNCTUATION)
            if not url:
                continue

            if url in seen:
                continue

            seen.add(url)
            urls.append(url)

            if len(urls) > max_urls:
                raise UrlBatchInputError(
                    f"too many URLs; maximum allowed is {max_urls}."
                )

    return urls
