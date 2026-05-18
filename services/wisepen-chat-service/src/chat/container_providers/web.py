from typing import Any

from chat.application.tools.services.web_crawl import WebCrawlService
from chat.application.tools.services.web_fetch import FetchCoordinator
from chat.application.tools.services.web_fetch.content_processor import ContentProcessor
from chat.application.tools.services.web_fetch.config import (
    STEEL_CONCURRENCY,
    STEEL_DELAY_MS,
    STEEL_MAX_RETRIES,
    WEB_FETCH_BROWSER_TIMEOUT,
    WEB_FETCH_CACHE_MAX_ITEMS,
    WEB_FETCH_CACHE_TTL_SECONDS,
    WEB_FETCH_LAST_RESORT_MIN_LENGTH,
    WEB_FETCH_LOCAL_WORKER_COUNT,
    WEB_FETCH_LOCAL_WORKER_RESTART_AFTER,
    WEB_FETCH_LOCAL_WORKER_TIMEOUT,
    WEB_FETCH_MAX_DOCUMENT_SIZE,
    WEB_FETCH_MIN_CONTENT_LENGTH,
    WEB_FETCH_STATIC_TIMEOUT,
)
from chat.application.tools.services.web_fetch.fetcher.local_fetcher import (
    LocalScriptFetcher,
)
from chat.application.tools.services.web_fetch.fetcher.static_fetcher import StaticFetcher
from chat.application.tools.services.web_fetch.fetcher.steel_fetcher import (
    SteelFetcher,
    SteelFetcherConfig,
)
from chat.application.web_search.internal.factory import create_search_coordinator
from chat.core.config.app_settings import settings
from dependency_injector import providers


def register_web_providers(container_cls: Any) -> None:
    # 搜索
    container_cls.web_search_coordinator = providers.Singleton(
        create_search_coordinator,
    )

    # 内容处理
    container_cls.content_processor = providers.Singleton(
        ContentProcessor,
        min_content_length=WEB_FETCH_MIN_CONTENT_LENGTH,
    )

    # 抓取器
    container_cls.static_fetcher = providers.Singleton(
        StaticFetcher,
        timeout=WEB_FETCH_STATIC_TIMEOUT,
        max_response_bytes=WEB_FETCH_MAX_DOCUMENT_SIZE,
        content_detector=container_cls.content_detector,
    )
    container_cls.steel_fetcher_config = providers.Singleton(
        SteelFetcherConfig,
        base_url=settings.STEEL_BASE_URL,
        timeout=WEB_FETCH_BROWSER_TIMEOUT,
        max_retries=STEEL_MAX_RETRIES,
        use_proxy=settings.STEEL_USE_PROXY,
        delay_ms=STEEL_DELAY_MS,
        region=settings.STEEL_REGION,
    )
    container_cls.steel_fetcher = providers.Singleton(
        SteelFetcher,
        config=container_cls.steel_fetcher_config,
        concurrency=STEEL_CONCURRENCY,
    )
    container_cls.local_script_fetcher = providers.Singleton(
        LocalScriptFetcher,
        timeout=WEB_FETCH_LOCAL_WORKER_TIMEOUT,
        worker_count=WEB_FETCH_LOCAL_WORKER_COUNT,
        restart_after=WEB_FETCH_LOCAL_WORKER_RESTART_AFTER,
    )

    # Coordinator
    container_cls.fetch_coordinator = providers.Singleton(
        FetchCoordinator,
        fetchers=providers.List(
            container_cls.static_fetcher,
            container_cls.steel_fetcher,
            container_cls.local_script_fetcher,
        ),
        processor=container_cls.content_processor,
        min_content_length=WEB_FETCH_MIN_CONTENT_LENGTH,
        last_resort_min_length=WEB_FETCH_LAST_RESORT_MIN_LENGTH,
        cache_ttl_seconds=WEB_FETCH_CACHE_TTL_SECONDS,
        cache_max_items=WEB_FETCH_CACHE_MAX_ITEMS,
    )
    container_cls.web_crawl_service = providers.Singleton(
        WebCrawlService,
        fetch_coordinator=container_cls.fetch_coordinator,
        file_handoff_store=container_cls.file_handoff_store,
    )
