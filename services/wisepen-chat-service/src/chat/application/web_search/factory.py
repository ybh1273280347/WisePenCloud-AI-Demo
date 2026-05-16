from chat.application.web_search.cache import SearchCache
from chat.application.web_search.search_coordinator import SearchCoordinator
from chat.application.web_search.searcher.searxng_searcher import SearXNGSearcher
from chat.application.web_search.searcher.serper_searcher import SerperSearcher


def create_search_coordinator() -> SearchCoordinator:
    from chat.core.config.app_settings import settings

    return SearchCoordinator(
        cache=SearchCache(),
        searxng_searcher=SearXNGSearcher(
            base_url=settings.SEARXNG_BASE_URL,
        ),
        serper_searcher=SerperSearcher(
            api_key=settings.SERPER_API_KEY or "",
            base_url=settings.SERPER_BASE_URL,
        ),
        serper_enabled=settings.SERPER_ENABLED and bool(settings.SERPER_API_KEY),
    )
