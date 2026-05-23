import asyncio
import os
import threading
from typing import List, Literal, Optional

import yaml
from chat.core.config.bootstrap_settings import bootstrap_settings
from common.cloud.nacos_client import nacos_client_manager
from common.logger import log_error, log_event
from pydantic import BaseModel, Field, field_validator


class CoreSettings(BaseModel):
    APP_NAME: str
    SERVICE_NAME: str
    SERVICE_HOST: str
    SERVICE_PORT: int
    DEV: bool
    LOG_LEVEL: str
    DEFAULT_MODEL_ID: int = 1
    # 安全配置：与 APISIX 网关约定的防绕过 Token
    FROM_SOURCE_SECRET: str


class LlmSettings(BaseModel):
    # LLM 默认网关配置（作为 fallback，主对话链路已从 Provider 表动态获取）
    LLM_BASE_URL: str
    LLM_API_KEY: str
    # Memory 使用的模型
    MEMORY_LLM_MODEL: str = "gpt-4o"
    MEMORY_EMBEDDING_MODEL: str = "text-embedding-3-large"
    MEMORY_RERANKER_ZE_MODEL: str = "zerank-1"
    ZERO_ENTROPY_API_KEY: str
    # 摘要压缩使用的轻量级模型（调用成本低、速度快）
    SUMMARY_MODEL: str = "openai/gemini-3-flash-preview"


class MessagingSettings(BaseModel):
    # Kafka 配置
    KAFKA_BOOTSTRAP_SERVERS: str = "wisepen-dev-server:9094"
    KAFKA_TOKEN_CONSUMPTION_TOPIC: str = "wisepen-user-token-consumption-topic"


class StorageSettings(BaseModel):
    # Redis
    REDIS_URL: str
    # MongoDB
    MONGODB_URL: str
    MONGODB_DB_NAME: str
    # Qdrant (Mem0 长期语义记忆向量存储)
    QDRANT_HOST: str
    QDRANT_PORT: int = 6333
    QDRANT_PASSWORD: str


class WebSearchSettings(BaseModel):
    # Web Search 工具配置
    FOURGET_ENABLED: bool = True
    FOURGET_BASE_URL: str = "http://fourget:80"
    FOURGET_WEB_SCRAPER: str = "ddg"
    FOURGET_TIMEOUT: float = 8.0
    FOURGET_MAX_CONCURRENCY: int = 5
    FOURGET_MAX_RETRIES: int = 1
    FOURGET_RETRY_BACKOFF_SECONDS: float = 0.4
    SERPER_ENABLED: bool = True
    SEARXNG_ENABLED: bool = False
    SEARXNG_BASE_URL: str = "http://localhost:8080"
    SERPER_API_KEY: Optional[str] = "ce1fb242cbfc5dd0097d97f0ea866bf34f571ba3"
    SERPER_BASE_URL: str = "https://google.serper.dev"
    TAVILY_BASE_URL: str = "https://api.tavily.com"
    BRAVE_SEARCH_BASE_URL: str = "https://api.search.brave.com"
    SERPAPI_BASE_URL: str = "https://serpapi.com"
    EXA_BASE_URL: str = "https://api.exa.ai"
    PERPLEXITY_BASE_URL: str = "https://api.perplexity.ai"
    ANYSEARCH_BASE_URL: str = "https://api.anysearch.com"
    ANYSEARCH_TIMEOUT_SECONDS: float = 8.0
    ANYSEARCH_ZONE: Optional[str] = None
    WIKIPEDIA_BASE_URL_TEMPLATE: str = "https://{language}.wikipedia.org"


class SearchProviderCredentialSettings(BaseModel):
    SEARCH_PROVIDER_CREDENTIAL_MASTER_KEY: Optional[str] = None
    SEARCH_PROVIDER_CREDENTIAL_KEY_ID: Optional[str] = None
    SEARCH_PROVIDER_CREDENTIAL_HMAC_SECRET: Optional[str] = None


class PaperSearchSettings(BaseModel):
    TOOL_CONTACT_EMAIL: Optional[str] = "jzsun24@m.fudan.edu.cn"
    TOOL_USER_AGENT: str = "WisePenCloud-AI/1.0"

    EXA_API_KEY: Optional[str] = "e4734bd6-3a94-458b-a90f-d5091aed436f"
    ARXIV_API_BASE_URL: str = "https://export.arxiv.org/api/query"
    ARXIV_RSS_BASE_URL: str = "https://rss.arxiv.org/atom"
    CROSSREF_BASE_URL: str = "https://api.crossref.org"
    DATACITE_BASE_URL: str = "https://api.datacite.org"
    DOI_BASE_URL: str = "https://doi.org"

    PAPER_SEARCH_ENABLE_EXA: bool = True
    PAPER_SEARCH_ENABLE_ARXIV_MONITOR: bool = True
    PAPER_SEARCH_ENABLE_ARXIV_HYDRATION: bool = True
    PAPER_SEARCH_ENABLE_DOI_HYDRATION: bool = True
    PAPER_SEARCH_ENABLE_EXA_FIND_SIMILAR: bool = True

    ARXIV_WATCH_CATEGORIES: List[str] = Field(
        default_factory=lambda: ["cs.CL", "cs.IR", "cs.LG", "cs.AI", "stat.ML"]
    )

    @field_validator("ARXIV_WATCH_CATEGORIES", mode="before")
    @classmethod
    def _parse_arxiv_watch_categories(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


class WebFetchSettings(BaseModel):
    # Web Fetch 工具配置
    STEEL_BASE_URL: str = "http://localhost:3000"
    STEEL_USE_PROXY: bool = False
    STEEL_REGION: Optional[str] = None


class OcrSettings(BaseModel):
    ENABLE_OCR: bool = True


class ExternalStateSettings(BaseModel):
    OPEN_METEO_GEOCODING_URL: str = "https://geocoding-api.open-meteo.com/v1/search"
    OPEN_METEO_FORECAST_URL: str = "https://api.open-meteo.com/v1/forecast"
    OPEN_METEO_AIR_QUALITY_URL: str = (
        "https://air-quality-api.open-meteo.com/v1/air-quality"
    )


class SecurityNetworkSettings(BaseModel):
    WEB_ACCESS_DOH_SERVERS: tuple[str, ...] = (
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
        "https://cloudflare-dns.com/dns-query",
        "https://dns.google/dns-query",
    )


class RpcSettings(BaseModel):
    # ---- 内部 RPC / 服务发现（wisepen-common 基建） ----
    # Nacos 服务发现客户端侧负载均衡策略：weighted_random | round_robin | random
    RPC_LB_STRATEGY: Literal["weighted_random", "round_robin", "random"] = (
        "weighted_random"
    )
    # 单次请求超时（秒）
    RPC_DEFAULT_TIMEOUT: float = 5.0
    # 单次调用最多额外重试次数（故障转移跨实例）；真实请求次数 = retries + 1
    RPC_DEFAULT_RETRIES: int = 2
    # ServiceDiscovery 本地缓存兜底 TTL（秒），即便订阅通道断连也会周期性强制 list
    SERVICE_DISCOVERY_CACHE_TTL_SECONDS: float = 30.0


class MathReasoningSettings(BaseModel):
    SAGE_MATH_WORKER_URL: str = "http://sage-math-worker:8000"
    SAGE_MATH_WORKER_TIMEOUT_SECONDS: int | float = 10


class TranslationSettings(BaseModel):
    TRANSLATION_DEVICE: str = "auto"


class GitHubSearchSettings(BaseModel):
    GITHUB_TOKEN: Optional[str] = None
    GITHUB_API_BASE_URL: str = "https://api.github.com"
    GITHUB_API_VERSION: str = "2026-03-10"


class PackageIntelligenceSettings(BaseModel):
    DEPS_DEV_API_BASE_URL: str = "https://api.deps.dev/v3"
    PYPI_API_BASE_URL: str = "https://pypi.org"
    NPM_REGISTRY_BASE_URL: str = "https://registry.npmjs.org"
    OPENSFF_SCORECARD_API_BASE_URL: str = "https://api.securityscorecards.dev"


class AppSettings(
    CoreSettings,
    LlmSettings,
    MessagingSettings,
    StorageSettings,
    WebSearchSettings,
    SearchProviderCredentialSettings,
    PaperSearchSettings,
    WebFetchSettings,
    OcrSettings,
    ExternalStateSettings,
    SecurityNetworkSettings,
    RpcSettings,
    MathReasoningSettings,
    TranslationSettings,
    GitHubSearchSettings,
    PackageIntelligenceSettings,
):
    """全量应用配置，字段保持扁平化，外部继续使用 settings.FIELD_NAME 访问。"""


def _load_env_overrides() -> dict[str, str]:
    return {
        key: value
        for key in AppSettings.model_fields
        if (value := os.getenv(key)) is not None
    }


def _run_async(coro):
    """在新线程的独立事件循环中执行协程，兼容 uvicorn 启动时已有运行中事件循环的场景。"""
    result, e = None, None

    def _target():
        nonlocal result, e
        try:
            result = asyncio.run(coro)
        except Exception as exc:
            e = exc

    t = threading.Thread(target=_target)
    t.start()
    t.join()
    if e:
        raise e
    return result


def load_settings() -> AppSettings:
    try:
        log_event("从 Nacos 拉取核心业务配置")
        raw_yaml = _run_async(nacos_client_manager.pull_config())
        config_dict = yaml.safe_load(raw_yaml) if raw_yaml else {}
        env_config = _load_env_overrides()
        # Precedence: Nacos > container environment > AppSettings/bootstrap defaults.
        full_config = {
            **bootstrap_settings.model_dump(),
            **env_config,
            **config_dict,
        }
        if "DEV" not in full_config:
            full_config["DEV"] = bootstrap_settings.IS_DEV
        return AppSettings(**full_config)
    except Exception as e:
        log_error("Nacos 配置拉取或解析", e)
        raise


settings = load_settings()


def build_tool_user_agent() -> str:
    if settings.TOOL_CONTACT_EMAIL:
        return f"{settings.TOOL_USER_AGENT} (mailto:{settings.TOOL_CONTACT_EMAIL})"
    return settings.TOOL_USER_AGENT
