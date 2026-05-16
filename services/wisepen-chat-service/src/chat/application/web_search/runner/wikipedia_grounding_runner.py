from __future__ import annotations

import asyncio
import re
from typing import Dict, List, Optional
from urllib.parse import quote

import httpx
from chat.application.web_search.cache import SearchCache, make_search_cache_key
from chat.application.web_search.planning import (
    GROUNDING_BUDGET,
    WikipediaGroundingResult,
    WikipediaKeyword,
)
from chat.application.web_search.planning.planner import detect_query_language
from common.logger import log_event, log_fail

_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()
_GROUNDING_KEYWORD_PARALLEL_LIMIT = 3


def _wikipedia_base_url(language: str) -> str:
    from chat.core.config.app_settings import settings

    return settings.WIKIPEDIA_BASE_URL_TEMPLATE.format(language=language).rstrip("/")


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).strip()


async def _search_page(
    *,
    keyword: str,
    language: str,
    user_agent: str,
    timeout: float,
) -> Optional[Dict[str, str]]:
    endpoint = f"{_wikipedia_base_url(language)}/w/rest.php/v1/search/page"
    headers = {
        "User-Agent": user_agent,
        "Api-User-Agent": user_agent,
        "Accept": "application/json",
    }
    params = {
        "q": keyword,
        "limit": "1",
    }

    try:
        client = await _get_client(timeout)
        response = await client.get(endpoint, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()

    except Exception as e:
        log_fail(
            "Wikipedia grounding search/page",
            repr(e),
            keyword=keyword,
            language=language,
        )
        return None

    pages = data.get("pages") or []
    if not pages:
        log_fail(
            "Wikipedia grounding",
            "无搜索结果",
            keyword=keyword,
            language=language,
        )
        return None

    page = pages[0]
    log_event(
        "Wikipedia grounding 搜索命中",
        keyword=keyword,
        language=language,
        title=page.get("title", ""),
        key=page.get("key", ""),
        excerpt=(page.get("excerpt") or "")[:100],
        description=(page.get("description") or "")[:100],
    )
    return page


async def _fetch_summary(
    *,
    title: str,
    language: str,
    user_agent: str,
    timeout: float,
) -> Optional[str]:
    encoded = quote(title.replace(" ", "_"), safe="/:_-()")
    endpoint = f"{_wikipedia_base_url(language)}/api/rest_v1/page/summary/{encoded}"
    headers = {
        "User-Agent": user_agent,
        "Api-User-Agent": user_agent,
        "Accept": "application/json",
    }

    try:
        client = await _get_client(timeout)
        response = await client.get(endpoint, headers=headers)
        response.raise_for_status()
        data = response.json()

    except Exception as e:
        log_fail(
            "Wikipedia grounding summary",
            repr(e),
            title=title,
            language=language,
        )
        return None

    extract = data.get("extract") or ""

    if extract:
        log_event(
            "Wikipedia grounding summary 获取成功",
            keyword=title,
            language=language,
            extract_length=len(extract),
            extract_preview=extract[:120],
        )
    else:
        log_event(
            "Wikipedia grounding summary 无 extract",
            keyword=title,
            language=language,
        )

    return extract


async def run_wikipedia_grounding(
    *,
    keywords: List[WikipediaKeyword],
    cache: SearchCache,
    mode: str,
    user_agent: str = "WisePenCloud-AI web_search/1.0 (contact@example.com)",
    timeout: float = 5.0,
) -> List[WikipediaGroundingResult]:
    keyword_values = [kw.text for kw in keywords]

    log_event(
        "Wikipedia grounding 入口",
        mode=mode,
        keyword_count=len(keywords),
        keyword_values=keyword_values,
    )

    if not keywords:
        log_event(
            "Wikipedia grounding 跳过",
            reason="empty_keywords",
            mode=mode,
        )
        return []

    budget = GROUNDING_BUDGET.get(mode, GROUNDING_BUDGET["normal"])
    max_chars = budget["max_extract_chars_per_keyword"]

    semaphore = asyncio.Semaphore(_GROUNDING_KEYWORD_PARALLEL_LIMIT)

    async def run_one(keyword: WikipediaKeyword) -> Optional[WikipediaGroundingResult]:
        async with semaphore:
            return await _run_one_keyword_grounding(
                keyword=keyword,
                cache=cache,
                max_chars=max_chars,
                user_agent=user_agent,
                timeout=timeout,
            )

    raw_results = await asyncio.gather(
        *(run_one(keyword) for keyword in keywords),
        return_exceptions=True,
    )
    results: List[WikipediaGroundingResult] = []
    for item in raw_results:
        if isinstance(item, WikipediaGroundingResult):
            results.append(item)
        elif isinstance(item, Exception):
            log_fail(
                "Wikipedia grounding keyword",
                repr(item),
            )

    log_event(
        "Wikipedia grounding 完成",
        keywords=len(keywords),
        results=len(results),
    )

    return results


async def _run_one_keyword_grounding(
    *,
    keyword: WikipediaKeyword,
    cache: SearchCache,
    max_chars: int,
    user_agent: str,
    timeout: float,
) -> Optional[WikipediaGroundingResult]:
    language = keyword.language or detect_query_language(keyword.text)

    cache_key = make_search_cache_key(
        source="wikipedia",
        query=keyword.text,
        max_results=1,
        with_images=False,
        language=language,
        purpose="grounding",
    )

    cached = await cache.get(cache_key)
    if cached is not None:
        cached_result = cached.response.results[0] if cached.response.results else None
        if cached_result and cached_result.snippet:
            log_event(
                "Wikipedia grounding 缓存命中",
                keyword=keyword.text,
                language=language,
                title=cached_result.title,
                extract_preview=cached_result.snippet[:120],
            )
            return WikipediaGroundingResult(
                keyword=keyword,
                title=cached_result.title,
                extract=cached_result.snippet[:max_chars],
                url=cached_result.url,
                language=language,
                cache_hit=True,
            )

        log_event(
            "Wikipedia grounding 缓存无有效结果",
            keyword=keyword.text,
            language=language,
        )
        return None

    page = await _search_page(
        keyword=keyword.text,
        language=language,
        user_agent=user_agent,
        timeout=timeout,
    )

    if page is None:
        return None

    title = str(page.get("title") or page.get("key") or "")
    key = str(page.get("key") or title)
    encoded_key = quote(key.replace(" ", "_"), safe="/:_-()")
    url = f"{_wikipedia_base_url(language)}/wiki/{encoded_key}"

    extract = await _fetch_summary(
        title=title,
        language=language,
        user_agent=user_agent,
        timeout=timeout,
    )

    if not extract:
        excerpt = _strip_html(str(page.get("excerpt") or ""))
        description = str(page.get("description") or "")
        extract = excerpt or description
        if extract:
            log_event(
                "Wikipedia grounding fallback 摘要",
                keyword=keyword.text,
                language=language,
                title=title,
                source="excerpt" if excerpt else "description",
                extract_preview=extract[:120],
            )

    if not extract:
        return None

    extract = extract[:max_chars]

    from chat.application.web_search.models.common import SearchResponse, SearchResult

    cache_response = SearchResponse(
        query=keyword.text,
        results=(SearchResult(title=title, url=url, snippet=extract),),
        source=f"wikipedia:{language}",
    )
    await cache.set(cache_key, cache_response)

    log_event(
        "Wikipedia grounding 结果构建",
        keyword=keyword.text,
        language=language,
        title=title,
        extract_length=len(extract),
        extract_preview=extract[:120],
    )

    return WikipediaGroundingResult(
        keyword=keyword,
        title=title,
        extract=extract,
        url=url,
        language=language,
        cache_hit=False,
    )


async def close_wikipedia_grounding_client() -> None:
    global _client

    client = _client
    _client = None
    if client is not None:
        await client.aclose()
    log_event("Wikipedia grounding client 关闭", closed=client is not None)


async def _get_client(timeout: float) -> httpx.AsyncClient:
    global _client

    if _client is not None and not _client.is_closed:
        return _client

    async with _client_lock:
        if _client is not None and not _client.is_closed:
            return _client

        _client = httpx.AsyncClient(timeout=timeout)
        return _client
