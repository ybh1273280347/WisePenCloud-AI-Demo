# src/chat/container.py

from typing import List

from dependency_injector import containers, providers
from v2.nacos import NacosNamingService

from chat.core.config.app_settings import settings
from chat.core.config.bootstrap_settings import bootstrap_settings
from chat.core.providers import (
    LiteLLMAdapter,
    Mem0Adapter,
    LocalFSSkillAssetLoader,
    OssSkillAssetLoader,
)
from chat.core.persistence import (
    MongoSessionRepository,
    MongoMessageRepository,
    MongoSkillRepository,
    RedisHotContext,
)
from chat.application.model_resolver import ModelResolver
from chat.application.chat_turn_coordinator import ChatTurnCoordinator
from chat.application.skill_matcher import KeywordSkillMatcher
from chat.application.skill_cache_refresher import SkillCacheRefresher
from chat.application.web_search import create_search_coordinator
from chat.application.tools import DocumentParseTool
from chat.application.document_parse.ocr import OcrProcessor
from chat.application.document_parse.factory import build_document_parse_service
from chat.application.document_parse.file_resolver import LocalDocumentFileResolver
from chat.application.web_fetch import ContentProcessor, FetchCoordinator
from chat.application.web_fetch.fetcher import LocalScriptFetcher, StaticFetcher, SteelFetcher, SteelFetcherConfig
from chat.application.tools import (
    ToolRegistry,
    SearchHistoricalMessagesTool,
    LoadSkillTool,
    LoadSkillAssetTool,
    WebSearchTool,
    WebFetchTool,
    ToolContentReadTool,
)
from common.clients.file_storage import FileStorageClient
from common.cloud.nacos_client import nacos_client_manager
from common.cloud.service_discovery import ServiceDiscovery
from common.http.rpc_client import RpcClient
from common.kafka.producer import KafkaProducerClient


async def _provide_nacos_naming() -> NacosNamingService:
    """延迟到首次 await，避免在 import 阶段触发 async Nacos 建连。"""
    return await nacos_client_manager.get_naming_client()


def _build_registry(tool_providers: List[providers.Provider]) -> ToolRegistry:
    """工厂函数：组装并返回已注册所有工具的 ToolRegistry 实例。"""
    registry = ToolRegistry()
    for provider in tool_providers:
        registry.register(provider)
    return registry


class Container(containers.DeclarativeContainer):
    """依赖注入容器，管理应用级组件生命周期。"""
    pass


def _register_core_providers(container_cls) -> None:
    container_cls.llm_provider = providers.Singleton(LiteLLMAdapter)
    container_cls.memory_provider = providers.Singleton(Mem0Adapter)
    container_cls.model_resolver = providers.Singleton(ModelResolver)
    container_cls.kafka_producer = providers.Singleton(
        KafkaProducerClient,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
    )


def _register_persistence_providers(container_cls) -> None:
    # 数据持久层：MongoDB + Redis
    container_cls.session_repo = providers.Singleton(MongoSessionRepository)
    container_cls.message_repo = providers.Singleton(MongoMessageRepository)
    container_cls.hot_context_repo = providers.Singleton(RedisHotContext)
    container_cls.skill_repo = providers.Singleton(MongoSkillRepository)


def _register_rpc_providers(container_cls) -> None:
    # 内部 RPC：Nacos 服务发现、通用 httpx 客户端、文件存储类型外观
    container_cls.service_discovery = providers.Singleton(
        ServiceDiscovery,
        naming_client_provider=providers.Object(_provide_nacos_naming),
        group_name=bootstrap_settings.NACOS_GROUP,
        default_strategy=settings.RPC_LB_STRATEGY,
        cache_ttl_seconds=settings.SERVICE_DISCOVERY_CACHE_TTL_SECONDS,
    )
    container_cls.rpc_client = providers.Singleton(
        RpcClient,
        discovery=container_cls.service_discovery,
        from_source_secret=settings.FROM_SOURCE_SECRET,
        timeout=settings.RPC_DEFAULT_TIMEOUT,
        retries=settings.RPC_DEFAULT_RETRIES,
        default_strategy=settings.RPC_LB_STRATEGY,
    )
    container_cls.file_storage_client = providers.Singleton(
        FileStorageClient,
        rpc=container_cls.rpc_client,
    )


def _register_skill_providers(container_cls) -> None:
    # Skill 子系统：
    # - SkillRepository 只读 Mongo 里的 Skill 实体
    # - SkillAssetLoader：DEV=True 用 LocalFS+OSS 回退；DEV=False 直连裸 OSS
    container_cls.oss_skill_asset_loader = providers.Singleton(
        OssSkillAssetLoader,
        file_storage_client=container_cls.file_storage_client,
        cache_dir=settings.skill_oss_cache_path,
        cache_ttl_seconds=settings.SKILL_OSS_CACHE_TTL_SECONDS,
        gc_interval_seconds=settings.SKILL_OSS_CACHE_GC_INTERVAL_SECONDS,
    )
    if settings.DEV:
        # 开发态使用 LocalFSSkillAssetLoader
        container_cls.skill_asset_loader = providers.Singleton(
            LocalFSSkillAssetLoader,
            root_dir=str(settings.skill_assets_cache_path),
            oss_fallback=container_cls.oss_skill_asset_loader,
        )
    else:
        # 线上态使用 OssSkillAssetLoader
        container_cls.skill_asset_loader = container_cls.oss_skill_asset_loader
    container_cls.skill_matcher = providers.Singleton(
        KeywordSkillMatcher,
        skill_repo=container_cls.skill_repo,
    )
    container_cls.skill_cache_refresher = providers.Singleton(
        SkillCacheRefresher,
        matcher=container_cls.skill_matcher,
        ttl_seconds=settings.SKILL_CACHE_TTL_SECONDS,
    )


def _register_document_parse_providers(container_cls) -> None:
    # ── 文档解析 OCR ──
    container_cls.document_parse_ocr_processor = providers.Singleton(
        OcrProcessor,
        timeout=settings.OCR_TIMEOUT_SECONDS,
        enabled=settings.ENABLE_OCR,
        backend=settings.OCR_BACKEND,
        language=settings.OCR_LANGUAGE,
        worker_mode=settings.OCR_WORKER_MODE,
        worker_idle_ttl_seconds=settings.OCR_WORKER_IDLE_TTL_SECONDS,
        use_doc_orientation_classify=settings.OCR_USE_DOC_ORIENTATION_CLASSIFY,
        use_doc_unwarping=settings.OCR_USE_DOC_UNWARPING,
        use_textline_orientation=settings.OCR_USE_TEXTLINE_ORIENTATION,
    )

    container_cls.document_parse_service = providers.Singleton(
        build_document_parse_service,
        local_ocr_processor=container_cls.document_parse_ocr_processor,
    )
    container_cls.document_file_resolver = providers.Singleton(
        LocalDocumentFileResolver,
    )


def _register_web_providers(container_cls) -> None:
    # ── 搜索 ──
    container_cls.web_search_coordinator = providers.Singleton(
        create_search_coordinator,
    )

    # ── 内容处理 ──
    container_cls.content_processor = providers.Singleton(
        ContentProcessor,
        min_content_length=settings.WEB_FETCH_MIN_CONTENT_LENGTH,
    )

    # ── 抓取器 ──
    container_cls.static_fetcher = providers.Singleton(
        StaticFetcher,
        timeout=settings.WEB_FETCH_STATIC_TIMEOUT,
        max_response_bytes=settings.WEB_FETCH_MAX_DOCUMENT_SIZE,
    )
    container_cls.steel_fetcher_config = providers.Singleton(
        SteelFetcherConfig,
        base_url=settings.STEEL_BASE_URL,
        timeout=settings.WEB_FETCH_BROWSER_TIMEOUT,
    )
    container_cls.steel_fetcher = providers.Singleton(
        SteelFetcher,
        config=container_cls.steel_fetcher_config,
    )
    container_cls.local_script_fetcher = providers.Singleton(
        LocalScriptFetcher,
        timeout=settings.WEB_FETCH_BROWSER_TIMEOUT,
    )

    # ── Coordinator ──
    container_cls.fetch_coordinator = providers.Singleton(
        FetchCoordinator,
        static_fetcher=container_cls.static_fetcher,
        steel_fetcher=container_cls.steel_fetcher,
        local_script_fetcher=container_cls.local_script_fetcher,
        processor=container_cls.content_processor,
        min_content_length=settings.WEB_FETCH_MIN_CONTENT_LENGTH,
        last_resort_min_length=settings.WEB_FETCH_LAST_RESORT_MIN_LENGTH,
        cache_ttl_seconds=settings.WEB_FETCH_CACHE_TTL_SECONDS,
        cache_max_items=settings.WEB_FETCH_CACHE_MAX_ITEMS,
    )


def _register_tool_providers(container_cls) -> None:
    # 工具层：各 Tool 和 ToolRegistry 均为 Singleton，由容器统一管理生命周期
    # SearchHistoricalMessagesTool
    container_cls.search_history_tool = providers.Singleton(
        SearchHistoricalMessagesTool,
        message_repo=container_cls.message_repo,
    )
    # LoadSkillTool / LoadSkillAssetTool
    container_cls.load_skill_tool = providers.Singleton(
        LoadSkillTool,
        skill_repo=container_cls.skill_repo,
    )
    container_cls.load_skill_asset_tool = providers.Singleton(
        LoadSkillAssetTool,
        skill_repo=container_cls.skill_repo,
        skill_asset_loader=container_cls.skill_asset_loader,
    )
    container_cls.web_search_tool = providers.Singleton(
        WebSearchTool,
        coordinator=container_cls.web_search_coordinator,
        fetcher=container_cls.fetch_coordinator,
    )
    container_cls.web_fetch_tool = providers.Singleton(
        WebFetchTool,
        fetcher=container_cls.fetch_coordinator,
    )
    container_cls.document_parse_tool = providers.Singleton(
        DocumentParseTool,
        parse_service=container_cls.document_parse_service,
        file_resolver=container_cls.document_file_resolver,
    )

    # ToolContentReadTool
    container_cls.tool_content_read_tool = providers.Singleton(
        ToolContentReadTool,
    )
    container_cls.tool_providers = providers.List(
        container_cls.search_history_tool,
        container_cls.load_skill_tool,
        container_cls.load_skill_asset_tool,
        container_cls.web_search_tool,
        container_cls.web_fetch_tool,
        container_cls.document_parse_tool,
        container_cls.tool_content_read_tool,
    )
    container_cls.tool_registry = providers.Singleton(
        _build_registry,
        tool_providers=container_cls.tool_providers,
    )


def _register_application_providers(container_cls) -> None:
    # 应用层组件
    container_cls.chat_turn_coordinator = providers.Factory(
        ChatTurnCoordinator,
        llm=container_cls.llm_provider,
        memory=container_cls.memory_provider,
        model_resolver=container_cls.model_resolver,
        session_repo=container_cls.session_repo,
        message_repo=container_cls.message_repo,
        hot_context_repo=container_cls.hot_context_repo,
        tool_registry=container_cls.tool_registry,
        kafka_producer=container_cls.kafka_producer,
        skill_matcher=container_cls.skill_matcher,
    )


_register_core_providers(Container)
_register_persistence_providers(Container)
_register_rpc_providers(Container)
_register_skill_providers(Container)
_register_document_parse_providers(Container)
_register_web_providers(Container)
_register_tool_providers(Container)
_register_application_providers(Container)

# 全局容器实例
container = Container()
