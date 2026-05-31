import asyncio
from typing import Mapping
from urllib.parse import quote

import httpx

from chat.application.tools.web.services.web_search.errors import SearchProviderError
from chat.application.tools.web.services.web_search.models import SearchResponse, SearchResult
from chat.application.tools.web.services.web_search.searcher.base import SearcherName, WebSearcher
from chat.application.tools.web.services.web_search.utils.http_client import fetch_search_json
from chat.application.tools.web.services.web_search.utils.results import is_valid_result
from common.logger import log_fail, log_event

_WIKIPEDIA_API_PATH = "/w/api.php"
_WIKIPEDIA_SUMMARY_PATH = "/api/rest_v1/page/summary"


class WikipediaSearcher(WebSearcher):
    """English Wikipedia background text searcher."""

    def __init__(
            self,
            client: httpx.AsyncClient,
            *,
            base_url: str = "https://en.wikipedia.org",
            user_agent: str = "WisePenCloud-AI web_search/1.0",
            max_extract_chars: int = 800,
            concurrency: int = 4,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._api_url = f"{self._base_url}{_WIKIPEDIA_API_PATH}"
        self._summary_base_url = f"{self._base_url}{_WIKIPEDIA_SUMMARY_PATH}"
        self._max_extract_chars = max_extract_chars
        self._semaphore = asyncio.Semaphore(concurrency)
        self._headers = {
            "User-Agent": user_agent,
            "Api-User-Agent": user_agent,
            "Accept": "application/json",
        }

    @property
    def name(self) -> SearcherName:
        """返回组件名称。"""
        return SearcherName.WIKIPEDIA

    async def search(
            self,
            query: str,
            *,
            max_results: int = 3,
    ) -> SearchResponse:
        """纯文本 Grounding 核心链路"""

        # 基础页面命中阶段 (OpenSearch Page Hit)
        payload = await fetch_search_json(
            client=self._client,
            url=self._api_url,
            params={
                "action": "opensearch",
                "search": query,
                "limit": str(max_results),
                "namespace": "0",
                "redirects": "resolve",
                "format": "json",
            },
            headers=self._headers,
            query=query,
            timeout_seconds=5.0,
            semaphore=self._semaphore,
            provider_name=SearcherName.WIKIPEDIA.value,
        )

        # 防御性长度校验，防止解包 IndexError 暴毙
        if not isinstance(payload, (list, tuple)) or len(payload) < 4:
            log_fail(
                "Wikipedia 外部 payload 长度不足，解包",
                f"expected_len>=4, actual_type={type(payload).__name__}",
                query=query,
            )
            return SearchResponse(query=query, results=[], source=SearcherName.WIKIPEDIA.value)

        # 安全解包
        raw_titles, raw_snippets, raw_urls = payload[1], payload[2], payload[3]

        # 容器类型校验
        if (
                not isinstance(raw_titles, list)
                or not isinstance(raw_snippets, list)
                or not isinstance(raw_urls, list)
        ):
            log_event(
                "Wikipedia 外部数据容器类型畸形",
                titles=type(raw_titles).__name__,
                snippets=type(raw_snippets).__name__,
                urls=type(raw_urls).__name__,
                query=query,
            )
            return SearchResponse(query=query, results=[], source=SearcherName.WIKIPEDIA.value)

        # 矩阵长度对齐校验
        # 外部多维列表必须齐头并进，长度不一致直接判定为畸形数据，拒绝妥协
        if not (len(raw_titles) == len(raw_snippets) == len(raw_urls)):
            log_event(
                "Wikipedia 外部数据矩阵长度非对称",
                titles_len=len(raw_titles),
                snippets_len=len(raw_snippets),
                urls_len=len(raw_urls),
                query=query,
            )
            return SearchResponse(query=query, results=[], source=SearcherName.WIKIPEDIA.value)

        # 页面摘要增强阶段 (Summary Enrichment)
        tasks = [
            self._build_page_result(
                client=self._client,
                title=str(t).strip(),
                page_snippet=str(s).strip(),
                page_url=str(u).strip(),
                query=query,
            )
            for t, s, u in zip(raw_titles, raw_snippets, raw_urls)
            if str(t).strip()
        ]
        raw_results = await asyncio.gather(*tasks)

        # 链路唯一合规性闸口清洗
        results = [
            res for res in raw_results
            if isinstance(res, SearchResult) and is_valid_result(res)
        ]

        return SearchResponse(
            query=query,
            results=results,
            source=SearcherName.WIKIPEDIA,
        )

    async def _build_page_result(
            self,
            client: httpx.AsyncClient,
            title: str,
            page_snippet: str,
            page_url: str,
            query: str,
    ) -> SearchResult:
        """组合 OpenSearch 基础命中结果与可选的 REST Summary 增强摘要"""
        encoded_title = quote(title.replace(" ", "_"), safe="")
        endpoint = f"{self._summary_base_url}/{encoded_title}"

        fallback_result = SearchResult(
            title=title,
            url=page_url,
            snippet=page_snippet,
        )
        try:
            summary_data = await fetch_search_json(
                client=client,
                url=endpoint,
                params={},
                query=query,
                headers=self._headers,
                timeout_seconds=3.0,
                semaphore=self._semaphore,
                provider_name=SearcherName.WIKIPEDIA.value,
            )
        except SearchProviderError as e:
            # 读取真实状态码，如果是标准的未收录 404，降级使用基础命中数据
            original_exc = e.__cause__
            if isinstance(original_exc, httpx.HTTPStatusError) and original_exc.response.status_code == 404:
                return fallback_result
            raise

        if not isinstance(summary_data, dict):
            return fallback_result

        # 提取高价值深度摘要
        extract = str(
            summary_data.get("extract", "")
        ).strip() or page_snippet

        if len(extract) > self._max_extract_chars:
            extract = extract[:self._max_extract_chars].rstrip()

        # 提取高价值标准 Canonical URL
        canonical_url = ""
        content_urls = summary_data.get("content_urls")
        if isinstance(content_urls, Mapping):
            desktop = content_urls.get("desktop")
            if isinstance(desktop, Mapping):
                canonical_url = str(desktop.get("page", "")).strip()

        return SearchResult(
            title=str(summary_data.get("title", title)).strip(),
            url=canonical_url or page_url,
            snippet=extract,
        )
