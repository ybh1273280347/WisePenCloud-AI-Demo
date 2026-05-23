from chat.application.web_search.internal.cache import SearchCache
from chat.application.web_search.internal.search_coordinator import SearchCoordinator
from chat.application.web_search.internal.searcher.fourget_searcher import FourGetSearcher
from chat.application.web_search.internal.searcher.searxng_searcher import SearXNGSearcher
from chat.application.web_search.internal.searcher.serper_searcher import SerperSearcher


def create_search_coordinator() -> SearchCoordinator:
    from chat.core.config.app_settings import settings

    return SearchCoordinator(
        cache=SearchCache(),
        fourget_searcher=FourGetSearcher(
            base_url=settings.FOURGET_BASE_URL,
            timeout=settings.FOURGET_TIMEOUT,
            web_scraper=settings.FOURGET_WEB_SCRAPER,
            max_concurrency=settings.FOURGET_MAX_CONCURRENCY,
            max_retries=settings.FOURGET_MAX_RETRIES,
            retry_backoff_seconds=settings.FOURGET_RETRY_BACKOFF_SECONDS,
        ),
        searxng_searcher=SearXNGSearcher(
            base_url=settings.SEARXNG_BASE_URL,
        ),
        serper_searcher=SerperSearcher(
            api_key=settings.SERPER_API_KEY or "",
            base_url=settings.SERPER_BASE_URL,
        ),
        fourget_enabled=settings.FOURGET_ENABLED,
        searxng_enabled=settings.SEARXNG_ENABLED,
        serper_enabled=settings.SERPER_ENABLED and bool(settings.SERPER_API_KEY),
    )
