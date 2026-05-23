import sys
import types
from dataclasses import dataclass
from types import SimpleNamespace
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT.parent / "wisepen-common" / "src"))


_TEST_SETTINGS = SimpleNamespace(
    APP_NAME="WisePen Chat Service",
    SERVICE_NAME="wisepen-chat-service",
    SERVICE_HOST="127.0.0.1",
    SERVICE_PORT=8000,
    DEV=True,
    LOG_LEVEL="INFO",
    DEFAULT_MODEL_ID=1,
    FROM_SOURCE_SECRET="test",
    LLM_BASE_URL="https://llm.example.test",
    LLM_API_KEY="test",
    MEMORY_LLM_MODEL="gpt-4o",
    MEMORY_EMBEDDING_MODEL="text-embedding-3-large",
    MEMORY_RERANKER_ZE_MODEL="zerank-1",
    ZERO_ENTROPY_API_KEY="test",
    SUMMARY_MODEL="openai/gemini-3-flash-preview",
    KAFKA_BOOTSTRAP_SERVERS="localhost:9092",
    KAFKA_TOKEN_CONSUMPTION_TOPIC="tokens",
    REDIS_URL="redis://localhost:6379/0",
    MONGODB_URL="mongodb://localhost:27017",
    MONGODB_DB_NAME="test",
    QDRANT_HOST="localhost",
    QDRANT_PORT=6333,
    QDRANT_PASSWORD="test",
    FOURGET_ENABLED=True,
    FOURGET_BASE_URL="http://fourget:80",
    FOURGET_WEB_SCRAPER="ddg",
    FOURGET_TIMEOUT=8.0,
    FOURGET_MAX_CONCURRENCY=5,
    FOURGET_MAX_RETRIES=1,
    FOURGET_RETRY_BACKOFF_SECONDS=0.4,
    SERPER_ENABLED=False,
    SEARXNG_ENABLED=False,
    SEARXNG_BASE_URL="http://localhost:8080",
    SERPER_API_KEY=None,
    SERPER_BASE_URL="https://google.serper.dev",
    TAVILY_BASE_URL="https://api.tavily.com",
    BRAVE_SEARCH_BASE_URL="https://api.search.brave.com",
    SERPAPI_BASE_URL="https://serpapi.com",
    EXA_BASE_URL="https://api.exa.ai",
    PERPLEXITY_BASE_URL="https://api.perplexity.ai",
    WIKIPEDIA_BASE_URL_TEMPLATE="https://{language}.wikipedia.org",
    SEARCH_PROVIDER_CREDENTIAL_MASTER_KEY=None,
    SEARCH_PROVIDER_CREDENTIAL_KEY_ID=None,
    SEARCH_PROVIDER_CREDENTIAL_HMAC_SECRET=None,
    TOOL_CONTACT_EMAIL=None,
    TOOL_USER_AGENT="WisePenCloud-AI/1.0",
    EXA_API_KEY="test-exa-key",
    ARXIV_API_BASE_URL="https://export.arxiv.org/api/query",
    ARXIV_RSS_BASE_URL="https://rss.arxiv.org/atom",
    CROSSREF_BASE_URL="https://api.crossref.org",
    DATACITE_BASE_URL="https://api.datacite.org",
    DOI_BASE_URL="https://doi.org",
    PAPER_SEARCH_ENABLE_EXA=True,
    PAPER_SEARCH_ENABLE_ARXIV_MONITOR=True,
    PAPER_SEARCH_ENABLE_ARXIV_HYDRATION=True,
    PAPER_SEARCH_ENABLE_DOI_HYDRATION=True,
    PAPER_SEARCH_ENABLE_EXA_FIND_SIMILAR=True,
    ARXIV_WATCH_CATEGORIES=["cs.CL", "cs.IR"],
    STEEL_BASE_URL="http://localhost:3000",
    STEEL_USE_PROXY=False,
    STEEL_REGION=None,
    ENABLE_OCR=True,
    OPEN_METEO_GEOCODING_URL="https://geocoding-api.open-meteo.com/v1/search",
    OPEN_METEO_FORECAST_URL="https://api.open-meteo.com/v1/forecast",
    OPEN_METEO_AIR_QUALITY_URL="https://air-quality-api.open-meteo.com/v1/air-quality",
    WEB_ACCESS_DOH_SERVERS=(
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
    ),
    RPC_LB_STRATEGY="weighted_random",
    RPC_DEFAULT_TIMEOUT=5.0,
    RPC_DEFAULT_RETRIES=2,
    SERVICE_DISCOVERY_CACHE_TTL_SECONDS=30.0,
    SAGE_MATH_WORKER_URL="http://sage-math-worker:8000",
    SAGE_MATH_WORKER_TIMEOUT_SECONDS=10,
    TRANSLATION_DEVICE="auto",
    GITHUB_TOKEN=None,
    GITHUB_API_BASE_URL="https://api.github.com",
    GITHUB_API_VERSION="2026-03-10",
    DEPS_DEV_API_BASE_URL="https://api.deps.dev/v3",
    PYPI_API_BASE_URL="https://pypi.org",
    NPM_REGISTRY_BASE_URL="https://registry.npmjs.org",
    OPENSFF_SCORECARD_API_BASE_URL="https://api.securityscorecards.dev",
)


def _install_module_stub(name: str, **attrs) -> None:
    if name in sys.modules:
        return

    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


class _ContentProcessor:
    pass


class _FetchCoordinator:
    pass


@dataclass(frozen=True, slots=True)
class _FetchResultItem:
    url: str
    success: bool
    content: str = ""
    document: object = None
    links: object = None
    final_url: str = ""
    status_code: int | None = None
    error: str = ""


class _LocalScriptFetcher:
    pass


class _SteelFetcher:
    pass


class _SteelFetcherConfig:
    pass


_install_module_stub(
    "chat.core.config.app_settings",
    settings=_TEST_SETTINGS,
    load_settings=lambda: _TEST_SETTINGS,
    build_tool_user_agent=lambda: (
        f"{_TEST_SETTINGS.TOOL_USER_AGENT} (mailto:{_TEST_SETTINGS.TOOL_CONTACT_EMAIL})"
        if _TEST_SETTINGS.TOOL_CONTACT_EMAIL
        else _TEST_SETTINGS.TOOL_USER_AGENT
    ),
)

_install_module_stub(
    "chat.application.tools.services.web_fetch.content_processor",
    ContentProcessor=_ContentProcessor,
)
_install_module_stub(
    "chat.application.tools.services.web_fetch.fetch_coordinator",
    FetchCoordinator=_FetchCoordinator,
    FetchResultItem=_FetchResultItem,
)
_install_module_stub(
    "chat.application.tools.services.web_fetch.fetcher.local_fetcher",
    LocalScriptFetcher=_LocalScriptFetcher,
)
_install_module_stub(
    "chat.application.tools.services.web_fetch.fetcher.steel_fetcher",
    SteelFetcher=_SteelFetcher,
    SteelFetcherConfig=_SteelFetcherConfig,
)
