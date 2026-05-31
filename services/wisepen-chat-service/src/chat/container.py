"""应用级依赖注入容器。管理所有组件的生命周期与装配关系。"""

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, List

import httpx
from dependency_injector import containers, providers
from docling.document_converter import DocumentConverter
from markitdown import MarkItDown
from pymongo import AsyncMongoClient
from qdrant_client import models
from steel import AsyncSteel
from v2.nacos import NacosNamingService
from zeroentropy import AsyncZeroEntropy

from chat.application.api_service.document_export import DocumentExportDownloadService
from chat.application.api_service.rag import RagApiService
from chat.application.api_service.search_provider import SearchProviderConfigApiService
from chat.application.chat_turn_coordinator import ChatTurnCoordinator
from chat.application.infra.document_temp_files.cleanup import (
    DOCUMENT_TEMP_FILE_TTL_SECONDS,
    DocumentTempFileCleanupService,
)
from chat.application.infra.document_temp_files.resolver import DocumentTempFileResolver
from chat.application.infra.document_temp_files.scheduler import (
    DocumentTempFileCleanupScheduler,
)
from chat.application.model_resolver import ModelResolver
from chat.application.rag.domain.answerability import EvidenceSufficiencyEvaluator
from chat.application.rag.domain.candidate_fusion import RagCandidateFusion
from chat.application.rag.domain.parent_aggregation import RagParentAggregator
from chat.application.rag.implementations.gc.scheduler import RagIndexGcScheduler
from chat.application.rag.implementations.gc.service import RagIndexGcService
from chat.application.rag.implementations.indexing.chunker import ChunkingConfig, RagChunker
from chat.application.rag.implementations.indexing.context_builder import (
    RagContextBuilder,
    RagContextBuilderConfig,
)
from chat.application.rag.implementations.indexing.index_builder import RagResourceIndexBuilder
from chat.application.rag.implementations.indexing.indexing_text_builder import RagIndexingTextBuilder
from chat.application.rag.implementations.indexing.processor import RagIndexProcessor
from chat.application.rag.implementations.indexing.runner import RagIndexWorkerRunner
from chat.application.rag.implementations.indexing.worker import RagIndexWorker
from chat.application.rag.implementations.persistence.elasticsearch.keyword_indexer import (
    ElasticsearchClientConfig,
    ElasticsearchKeywordIndexer,
    build_elasticsearch_client,
)
from chat.application.rag.implementations.persistence.mongodb.repositories.cache_repository import (
    MongoRagContextCacheRepository,
    MongoRagDenseEmbeddingCacheRepository,
    MongoRagQueryEmbeddingCacheRepository,
)
from chat.application.rag.implementations.persistence.mongodb.repositories.chunk_repository import (
    MongoChunkRepository,
)
from chat.application.rag.implementations.persistence.mongodb.repositories.manifest_repository import (
    MongoManifestRepository,
)
from chat.application.rag.implementations.persistence.mongodb.repositories.resource_repository import (
    MongoDocumentResourceRepository,
    MongoNoteResourceRepository,
)
from chat.application.rag.implementations.persistence.qdrant.collection import (
    QdrantCollectionConfig,
    QdrantCollectionManager,
    build_qdrant_client,
)
from chat.application.rag.implementations.persistence.qdrant.indexer import QdrantChunkIndexer
from chat.application.rag.implementations.persistence.redis.indexing_queue import RedisRagIndexingQueue
from chat.application.rag.implementations.providers.context_client import (
    LiteLLMContextClient,
    LiteLLMContextClientConfig,
)
from chat.application.rag.implementations.providers.dense import (
    CachedDenseEmbeddingClient,
    LiteLLMDenseEmbeddingClient,
    LiteLLMDenseEmbeddingClientConfig,
)
from chat.application.rag.implementations.resources.resource_handlers import (
    DocumentResourceHandler,
    NoteResourceHandler,
)
from chat.application.rag.implementations.resources.resource_service import ResourceService
from chat.application.rag.implementations.resources.version_service import (
    RagPipelineVersionConfig,
    RagVersionService,
)
from chat.application.rag.implementations.retrieval.context_assembler import RagContextAssembler
from chat.application.rag.implementations.retrieval.elasticsearch_retriever import (
    ElasticsearchKeywordRetriever,
)
from chat.application.rag.implementations.retrieval.evidence_assembler import RagEvidenceAssembler
from chat.application.rag.implementations.retrieval.manifest_resolver import RagManifestResolver
from chat.application.rag.implementations.retrieval.qdrant_retriever import QdrantChunkRetriever
from chat.application.rag.implementations.retrieval.reranker import ZeroEntropyReranker
from chat.application.rag.implementations.retrieval.retrieval_orchetrator import (
    RagRetrievalOrchestrator,
)
from chat.application.rag.implementations.retrieval.retrieval_pipeline import RagRetrievalPipeline
from chat.application.rag.service import RagService
from chat.application.skill_cache_refresher import SkillCacheRefresher
from chat.application.skill_matcher import KeywordSkillMatcher
from chat.application.tool_registry import ToolRegistry
from chat.application.tools.browser.browse_interact_tool import BrowseInteractTool
from chat.application.tools.document.document_convert_tool import DocumentConvertTool
from chat.application.tools.document.document_export_tool import DocumentExportTool
from chat.application.tools.document.document_parse_tool import DocumentParseTool
from chat.application.tools.document.services.document_convert import DocumentConvertService
from chat.application.tools.document.services.document_convert.converter import MarkdownConverter
from chat.application.tools.document.services.document_export.runtime.atomic_writer import AtomicExportWriter
from chat.application.tools.document.services.document_export.runtime.download_resolver import (
    DocumentDownloadResolver,
)
from chat.application.tools.document.services.document_export.runtime.playwright_pool import (
    PlaywrightBrowserPool,
)
from chat.application.tools.document.services.document_export.renderers.docx_renderer import DocxRenderer
from chat.application.tools.document.services.document_export.renderers.html_renderer import HtmlRenderer
from chat.application.tools.document.services.document_export.renderers.markdown_renderer import (
    MarkdownRenderer,
)
from chat.application.tools.document.services.document_export.renderers.pdf_renderer import PdfRenderer
from chat.application.tools.document.services.document_export.renderers.txt_renderer import TxtRenderer
from chat.application.tools.document.services.document_export.service import DocumentExportService
from chat.application.tools.document.services.document_export.utils.path import (
    document_export_output_path,
)
from chat.application.tools.document.services.document_parse import DocumentParseService
from chat.application.tools.document.services.document_parse.parser.epub import EpubParser
from chat.application.tools.document.services.document_parse.parser.office.fallback import (
    OfficeFallbackParser,
)
from chat.application.tools.document.services.document_parse.parser.office.parser import OfficeParser
from chat.application.tools.document.services.document_parse.parser.office.primary import (
    OfficePrimaryParser,
)
from chat.application.tools.document.services.document_parse.parser.pdf.marker import (
    MarkerPdfExtractor,
)
from chat.application.tools.document.services.document_parse.parser.pdf.page_classifier import (
    PageClassifier,
)
from chat.application.tools.document.services.document_parse.parser.pdf.parser import PdfParser
from chat.application.tools.document.services.document_parse.parser.pdf.table_extractor import (
    TableExtractor,
)
from chat.application.tools.document.services.document_parse.parser.spreadsheet import SpreadsheetParser
from chat.application.tools.document.services.document_parse.ocr.image_adapter import OcrImageAdapter
from chat.application.tools.document.services.document_parse.ocr.processor import OcrProcessor, OcrProcessorConfig
from chat.application.tools.evidence_access.evidence_rank_tool import EvidenceRankTool
from chat.application.tools.evidence_access.tool_content_batch_read_tool import (
    ToolContentBatchReadTool,
)
from chat.application.tools.evidence_access.tool_content_read_tool import ToolContentReadTool
from chat.application.tools.language.services.translation.model_provider import (
    OpusMtModelProvider,
)
from chat.application.tools.language.services.translation.opus_mt_engine import (
    OpusMtEngineConfig,
    OpusMtTranslationEngine,
)
from chat.application.tools.language.services.translation.service import TranslationAssistService
from chat.application.tools.language.translation_assist_tool import TranslationAssistTool
from chat.application.tools.math_solver.python_math_solver_tool import PythonMathSolverTool
from chat.application.tools.math_solver.sage_math_solver_tool import SageMathSolverTool
from chat.application.tools.math_solver.services.python_runtime.engine import PythonMathEngine
from chat.application.tools.math_solver.services.python_runtime.service import PythonMathSolverService
from chat.application.tools.math_solver.services.sage_runtime.client import SageRuntimeClient
from chat.application.tools.math_solver.services.sage_runtime.service import SageMathSolverService
from chat.application.tools.retrieval.rag_search_tool import RagSearchTool
from chat.application.tools.retrieval.search_history_tool import SearchHistoricalMessagesTool
from chat.application.tools.skill.load_skill_asset_tool import LoadSkillAssetTool
from chat.application.tools.skill.load_skill_tool import LoadSkillTool
from chat.application.tools.tool_content_store import tool_content_store
from chat.application.tools.web.services.common.file_handoff.store import (
    DEFAULT_HANDOFF_ROOT,
    TemporaryFileHandoffStore,
)
from chat.application.tools.web.services.common.file_type_detection.detector import FileTypeDetector
from chat.application.tools.web.services.common.file_type_detection.magika import MagikaDetector
from chat.application.tools.web.services.web_crawl import WebCrawlService
from chat.application.tools.web.services.web_fetch import FetchCoordinator
from chat.application.tools.web.services.web_fetch.fetcher.content_processor import ContentProcessor
from chat.application.tools.web.services.web_fetch.fetcher.local_fetcher import LocalScriptFetcher
from chat.application.tools.web.services.web_fetch.fetcher.static_fetcher import StaticFetcher
from chat.application.tools.web.services.web_fetch.fetcher.steel_fetcher import (
    SteelFetcher,
    SteelFetcherConfig,
)
from chat.application.tools.web.services.web_search.cache import SearchCache
from chat.application.tools.web.services.web_search.coordinator import SearchCoordinator
from chat.application.tools.web.services.web_search.enums import ProviderMode
from chat.application.tools.web.services.web_search.provider_policy.encryption import (
    SearchProviderCredentialCipher,
)
from chat.application.tools.web.services.web_search.provider_policy.persistence.repositories import (
    SearchProviderConfigRepository,
)
from chat.application.tools.web.services.web_search.provider_policy.service import (
    SearchProviderConfigService,
)
from chat.application.tools.web.services.web_search.provider_policy.validator import (
    SearchProviderConfigValidator,
)
from chat.application.tools.web.services.web_search.runner.custom import CustomProviderRunner
from chat.application.tools.web.services.web_search.runner.fourget import FourGetSearchRunner
from chat.application.tools.web.services.web_search.runner.serper import SerperSearchRunner
from chat.application.tools.web.services.web_search.runner.wikipedia import WikipediaRunner
from chat.application.tools.web.services.web_search.searcher.fourget import FourGetSearcher
from chat.application.tools.web.services.web_search.searcher.serper import SerperSearcher
from chat.application.tools.web.services.web_search.searcher.wikipedia import WikipediaSearcher
from chat.application.tools.web.web_crawl_tool import WebCrawlTool
from chat.application.tools.web.web_fetch_tool import WebFetchTool
from chat.application.tools.web.web_search_tool import WebSearchTool
from chat.core.config.app_settings import settings
from chat.core.config.bootstrap_settings import bootstrap_settings
from chat.core.config.tool_settings import tool_settings
from chat.core.persistence import (
    MongoMessageRepository,
    MongoSessionRepository,
    MongoSkillRepository,
    RedisHotContext,
)
from chat.core.providers import LiteLLMAdapter, LocalFSSkillAssetLoader, Mem0Adapter, OssSkillAssetLoader
from common.cloud.nacos_client import nacos_client_manager
from common.cloud.service_discovery import ServiceDiscovery
from common.clients.file_storage import FileStorageClient
from common.http.rpc_client import RpcClient
from common.kafka.producer import KafkaProducerClient

from marker.converters.pdf import PdfConverter as MarkerPdfConverter
from marker.models import create_model_dict
from paddleocr import PPStructure


class Container(containers.DeclarativeContainer):
    """应用级依赖注入容器。"""
    pass


# ==============================================================================
#   Helper Resources / Factories
# ==============================================================================


async def _provide_nacos_naming() -> NacosNamingService:
    """延迟到首次 await，避免在 import 阶段触发 async Nacos 建连。"""
    return await nacos_client_manager.get_naming_client()


@asynccontextmanager
async def _web_search_http_client() -> AsyncIterator[httpx.AsyncClient]:
    client = httpx.AsyncClient()
    try:
        yield client
    finally:
        await client.aclose()


@asynccontextmanager
async def _static_fetch_http_client() -> AsyncIterator[httpx.AsyncClient]:
    client = httpx.AsyncClient(
        timeout=15.0,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        transport=httpx.AsyncHTTPTransport(retries=3),
        follow_redirects=False,
    )
    try:
        yield client
    finally:
        await client.aclose()


@asynccontextmanager
async def _steel_client(config: SteelFetcherConfig) -> AsyncIterator[AsyncSteel]:
    client = AsyncSteel(
        base_url=config.base_url,
        timeout=config.timeout,
        max_retries=0,
    )
    try:
        yield client
    finally:
        if not client.is_closed():
            await client.close()


# ==============================================================================
#   对导入敏感的辅助函数（必须在 marker/paddleocr 导入之后定义）
# ==============================================================================

def _build_document_parse_components():
    ocr_processor_config = OcrProcessorConfig()
    ocr_processor = OcrProcessor(config=ocr_processor_config)
    ocr_image_adapter = OcrImageAdapter(local_ocr_processor=ocr_processor)

    docling_document_converter = DocumentConverter()
    markitdown_converter = MarkItDown()
    pp_structure_engine = PPStructure(show_log=False)

    office_primary_parser = OfficePrimaryParser(converter=docling_document_converter)
    office_fallback_parser = OfficeFallbackParser(converter=markitdown_converter)
    office_parser = OfficeParser(
        primary_parser=office_primary_parser,
        fallback_parser=office_fallback_parser,
    )
    pdf_converter = MarkerPdfConverter(
        artifact_dict=create_model_dict(),
    )
    pdf_parser = PdfParser(
        classifier=PageClassifier(),
        marker_extractor=MarkerPdfExtractor(converter=pdf_converter),
        ocr_adapter=ocr_image_adapter,
        table_extractor=TableExtractor(pp_structure_engine=pp_structure_engine),
    )
    epub_parser = EpubParser(converter=markitdown_converter)
    spreadsheet_parser = SpreadsheetParser()
    document_parse_service = DocumentParseService(
        pdf_parser=pdf_parser,
        office_parser=office_parser,
        epub_parser=epub_parser,
        spreadsheet_parser=spreadsheet_parser,
    )
    return {
        "ocr_processor_config": ocr_processor_config,
        "ocr_processor": ocr_processor,
        "ocr_image_adapter": ocr_image_adapter,
        "docling_document_converter": docling_document_converter,
        "markitdown_converter": markitdown_converter,
        "pp_structure_engine": pp_structure_engine,
        "office_primary_parser": office_primary_parser,
        "office_fallback_parser": office_fallback_parser,
        "office_parser": office_parser,
        "pdf_converter": pdf_converter,
        "pdf_parser": pdf_parser,
        "epub_parser": epub_parser,
        "spreadsheet_parser": spreadsheet_parser,
        "document_parse_service": document_parse_service,
    }


def _build_document_export_components():
    html_renderer = HtmlRenderer()
    browser_pool = PlaywrightBrowserPool(max_contexts=8)
    pdf_renderer = PdfRenderer(
        html_renderer=html_renderer,
        browser_pool=browser_pool,
    )
    docx_renderer = DocxRenderer(pandoc_bin="pandoc")
    markdown_renderer = MarkdownRenderer()
    txt_renderer = TxtRenderer()
    renderers = {
        renderer.target_format: renderer
        for renderer in [
            markdown_renderer,
            html_renderer,
            pdf_renderer,
            docx_renderer,
            txt_renderer,
        ]
    }
    atomic_writer = AtomicExportWriter()
    service = DocumentExportService(
        output_root=document_export_output_path(),
        markdown_renderer=markdown_renderer,
        renderers=renderers,
        atomic_writer=atomic_writer,
    )
    return {
        "html_renderer": html_renderer,
        "browser_pool": browser_pool,
        "pdf_renderer": pdf_renderer,
        "docx_renderer": docx_renderer,
        "markdown_renderer": markdown_renderer,
        "txt_renderer": txt_renderer,
        "renderers": renderers,
        "atomic_writer": atomic_writer,
        "service": service,
    }


def _build_registry(tool_instances: List[Any]) -> ToolRegistry:
    """构建工具注册表单例。"""
    registry = ToolRegistry()
    for tool_instance in tool_instances:
        registry.register(tool_instance)
    return registry


# ==============================================================================
#   1. 核心服务层 (Core Services)
# ==============================================================================

def _register_core(container_cls: Any) -> None:

    # ----- LLM & Memory -----
    container_cls.llm_provider = providers.Singleton(LiteLLMAdapter)

    container_cls.memory_provider = providers.Singleton(Mem0Adapter)

    container_cls.model_resolver = providers.Singleton(ModelResolver)

    # ----- Kafka -----
    container_cls.kafka_producer = providers.Singleton(
        KafkaProducerClient,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
    )


# ==============================================================================
#   2. 数据持久化层 (Persistence - MongoDB / Redis)
# ==============================================================================

def _register_persistence(container_cls: Any) -> None:

    # ----- MongoDB -----
    container_cls.mongo_client = providers.Singleton(
        AsyncMongoClient,
        settings.MONGODB_URL,
    )

    container_cls.session_repo = providers.Singleton(MongoSessionRepository)

    container_cls.message_repo = providers.Singleton(MongoMessageRepository)

    container_cls.skill_repo = providers.Singleton(MongoSkillRepository)

    # ----- Redis -----
    container_cls.hot_context_repo = providers.Singleton(RedisHotContext)


# ==============================================================================
#   3. 内部 RPC 与服务发现 (RPC / Service Discovery)
# ==============================================================================

def _register_rpc(container_cls: Any) -> None:

    # ----- Service Discovery -----
    container_cls.service_discovery = providers.Singleton(
        ServiceDiscovery,
        naming_client_provider=providers.Object(_provide_nacos_naming),
        group_name=bootstrap_settings.NACOS_GROUP,
        default_strategy=settings.RPC_LB_STRATEGY,
        cache_ttl_seconds=settings.SERVICE_DISCOVERY_CACHE_TTL_SECONDS,
    )

    # ----- RPC Client -----
    container_cls.rpc_client = providers.Singleton(
        RpcClient,
        discovery=container_cls.service_discovery,
        from_source_secret=settings.FROM_SOURCE_SECRET,
        timeout=settings.RPC_DEFAULT_TIMEOUT,
        retries=settings.RPC_DEFAULT_RETRIES,
        default_strategy=settings.RPC_LB_STRATEGY,
    )

    # ----- File Storage -----
    container_cls.file_storage_client = providers.Singleton(
        FileStorageClient,
        rpc=container_cls.rpc_client,
    )


# ==============================================================================
#   4. Skill 插件系统 (Skill System)
# ==============================================================================

def _register_skill(container_cls: Any) -> None:

    # ----- Asset Loaders -----
    container_cls.oss_skill_asset_loader = providers.Singleton(
        OssSkillAssetLoader,
        file_storage_client=container_cls.file_storage_client,
    )

    if settings.DEV:
        container_cls.skill_asset_loader = providers.Singleton(
            LocalFSSkillAssetLoader,
            oss_fallback=container_cls.oss_skill_asset_loader,
        )
    else:
        container_cls.skill_asset_loader = container_cls.oss_skill_asset_loader

    # ----- Matcher & Cache -----
    container_cls.skill_matcher = providers.Singleton(
        KeywordSkillMatcher,
        skill_repo=container_cls.skill_repo,
    )

    container_cls.skill_cache_refresher = providers.Singleton(
        SkillCacheRefresher,
        matcher=container_cls.skill_matcher,
    )


# ==============================================================================
#   5. 文档解析 (Document Parse)
# ==============================================================================

def _register_document_parse(container_cls: Any) -> None:
    components = _build_document_parse_components()

    container_cls.ocr_processor_config = providers.Object(components["ocr_processor_config"])

    container_cls.ocr_processor = providers.Object(components["ocr_processor"])

    container_cls.ocr_image_adapter = providers.Object(components["ocr_image_adapter"])

    container_cls.docling_document_converter = providers.Object(
        components["docling_document_converter"]
    )

    container_cls.markitdown_converter = providers.Object(components["markitdown_converter"])

    container_cls.pp_structure_engine = providers.Object(components["pp_structure_engine"])

    container_cls.office_primary_parser = providers.Object(components["office_primary_parser"])

    container_cls.office_fallback_parser = providers.Object(components["office_fallback_parser"])

    container_cls.office_parser = providers.Object(components["office_parser"])

    container_cls.pdf_converter = providers.Object(components["pdf_converter"])

    container_cls.pdf_parser = providers.Object(components["pdf_parser"])

    container_cls.epub_parser = providers.Object(components["epub_parser"])

    container_cls.spreadsheet_parser = providers.Object(components["spreadsheet_parser"])

    container_cls.document_parse_service = providers.Object(
        components["document_parse_service"]
    )

    container_cls.document_file_resolver = providers.Singleton(DocumentTempFileResolver)

    container_cls.document_temp_file_cleanup_service = providers.Singleton(
        DocumentTempFileCleanupService,
    )

    container_cls.document_temp_file_cleanup_scheduler = providers.Singleton(
        DocumentTempFileCleanupScheduler,
        cleanup_service=container_cls.document_temp_file_cleanup_service,
        interval_seconds=DOCUMENT_TEMP_FILE_TTL_SECONDS,
    )


# ==============================================================================
#   6. 文档导出 (Document Export)
# ==============================================================================

def _register_document_export(container_cls: Any) -> None:
    components = _build_document_export_components()

    container_cls.document_export_html_renderer = providers.Object(components["html_renderer"])

    container_cls.document_export_browser_pool = providers.Object(components["browser_pool"])

    container_cls.document_export_pdf_renderer = providers.Object(components["pdf_renderer"])

    container_cls.document_export_docx_renderer = providers.Object(components["docx_renderer"])

    container_cls.document_export_markdown_renderer = providers.Object(
        components["markdown_renderer"]
    )

    container_cls.document_export_txt_renderer = providers.Object(components["txt_renderer"])

    container_cls.document_export_renderers = providers.Object(components["renderers"])

    container_cls.document_export_atomic_writer = providers.Object(components["atomic_writer"])

    container_cls.document_export_service = providers.Object(components["service"])

    container_cls.document_export_download_resolver = providers.Singleton(
        DocumentDownloadResolver,
        output_root=document_export_output_path(),
    )

    container_cls.document_export_download_service = providers.Singleton(
        DocumentExportDownloadService,
        resolver=container_cls.document_export_download_resolver,
        session_repo=container_cls.session_repo,
    )


# ==============================================================================
#   7. 文档格式转换 (Document Convert)
# ==============================================================================

def _register_document_convert(container_cls: Any) -> None:
    markdown_converter = MarkdownConverter(
        markitdown=MarkItDown(),
        parse_service=container_cls.document_parse_service(),
        markdown_renderer=container_cls.document_export_markdown_renderer(),
    )

    container_cls.document_convert_markdown_converter = providers.Object(markdown_converter)

    container_cls.document_convert_service = providers.Singleton(
        DocumentConvertService,
        markdown_converter=container_cls.document_convert_markdown_converter,
        export_service=container_cls.document_export_service,
        temp_file_resolver=container_cls.document_file_resolver,
    )


# ==============================================================================
#   8. Web 搜索与抓取 (Web Search / Fetch / Crawl)
# ==============================================================================

def _register_web(container_cls: Any) -> None:

    # ----- 搜索引擎 -----
    container_cls.web_search_http_client = providers.Resource(_web_search_http_client)

    container_cls.web_search_cache = providers.Singleton(SearchCache)

    container_cls.fourget_searcher = providers.Singleton(
        FourGetSearcher,
        client=container_cls.web_search_http_client,
        base_url=settings.FOURGET_BASE_URL,
        user_agent=tool_settings.WEB_SEARCH_USER_AGENT,
        timeout=tool_settings.FOURGET_TIMEOUT,
        scraper=tool_settings.FOURGET_WEB_SCRAPER,
        max_concurrency=tool_settings.FOURGET_MAX_CONCURRENCY,
    )

    container_cls.serper_searcher = providers.Singleton(
        SerperSearcher,
        client=container_cls.web_search_http_client,
        api_key=tool_settings.SERPER_API_KEY or "",
        base_url=settings.SERPER_BASE_URL,
    )

    container_cls.wikipedia_searcher = providers.Singleton(
        WikipediaSearcher,
        client=container_cls.web_search_http_client,
        base_url=settings.WIKIPEDIA_BASE_URL_TEMPLATE,
        user_agent=tool_settings.WEB_SEARCH_USER_AGENT,
    )

    # ----- 搜索 Runner -----
    container_cls.fourget_runner = providers.Singleton(
        FourGetSearchRunner,
        searcher=container_cls.fourget_searcher,
        cache=container_cls.web_search_cache,
        provider_mode=ProviderMode.DEFAULT,
    )

    container_cls.serper_runner = providers.Singleton(
        SerperSearchRunner,
        searcher=container_cls.serper_searcher,
        cache=container_cls.web_search_cache,
        provider_mode=ProviderMode.DEFAULT,
    )

    container_cls.wikipedia_runner = providers.Singleton(
        WikipediaRunner,
        searcher=container_cls.wikipedia_searcher,
    )

    container_cls.custom_provider_runner_factory = providers.Factory(
        CustomProviderRunner,
        client=container_cls.web_search_http_client,
        cache=container_cls.web_search_cache,
    )

    # ----- 搜索 Coordinator -----
    container_cls.web_search_coordinator = providers.Singleton(
        SearchCoordinator,
        fourget_runner=container_cls.fourget_runner,
        serper_runner=container_cls.serper_runner,
        wikipedia_runner=container_cls.wikipedia_runner,
        custom_runner_factory=container_cls.custom_provider_runner_factory.provider,
    )

    # ----- 内容处理器 -----
    container_cls.content_processor = providers.Singleton(
        ContentProcessor,
        min_content_length=tool_settings.FETCH_MIN_CONTENT_LENGTH,
    )

    # ----- 文件处理 -----
    container_cls.magika_detector = providers.Singleton(MagikaDetector)

    container_cls.filetype_detector = providers.Singleton(
        FileTypeDetector,
        magika_detector=container_cls.magika_detector,
    )

    container_cls.file_handoff_store = providers.Singleton(
        TemporaryFileHandoffStore,
        root_dir=DEFAULT_HANDOFF_ROOT,
    )

    # ----- 静态 FETCH -----
    container_cls.static_fetch_http_client = providers.Resource(_static_fetch_http_client)

    container_cls.static_fetcher = providers.Singleton(
        StaticFetcher,
        client=container_cls.static_fetch_http_client,
        max_response_bytes=tool_settings.STATIC_FETCH_MAX_RESPONSE_BYTES,
        filetype_detector=container_cls.filetype_detector,
        processor=container_cls.content_processor,
    )

    # ----- Steel Fetcher -----
    container_cls.steel_fetcher_config = providers.Singleton(
        SteelFetcherConfig,
        base_url=settings.STEEL_BASE_URL,
        timeout=tool_settings.STEEL_TIMEOUT,
        delay_ms=tool_settings.STEEL_DELAY_MS,
    )

    container_cls.steel_client = providers.Resource(
        _steel_client,
        config=container_cls.steel_fetcher_config,
    )

    container_cls.steel_fetcher = providers.Singleton(
        SteelFetcher,
        config=container_cls.steel_fetcher_config,
        client=container_cls.steel_client,
        concurrency=tool_settings.STEEL_CONCURRENCY,
        processor=container_cls.content_processor,
    )

    # ----- Local Script Fetcher -----
    container_cls.local_script_fetcher = providers.Singleton(
        LocalScriptFetcher,
        timeout=tool_settings.LOCAL_SCRIPT_TIMEOUT,
        worker_count=tool_settings.LOCAL_SCRIPT_WORKER_COUNT,
        restart_after=tool_settings.LOCAL_SCRIPT_RESTART_AFTER,
    )

    # ----- Fetch Coordinator -----
    container_cls.fetch_coordinator = providers.Singleton(
        FetchCoordinator,
        static_fetcher=container_cls.static_fetcher,
        steel_fetcher=container_cls.steel_fetcher,
        local_fetcher=container_cls.local_script_fetcher,
        min_content_length=tool_settings.FETCH_MIN_CONTENT_LENGTH,
        last_resort_min_length=tool_settings.FETCH_LAST_RESORT_MIN_LENGTH,
        cache_ttl_seconds=tool_settings.FETCH_CACHE_TTL_SECONDS,
        cache_max_items=tool_settings.FETCH_CACHE_MAX_ITEMS,
    )

    # ----- Web Crawl -----
    container_cls.web_crawl_service = providers.Singleton(
        WebCrawlService,
        fetch_coordinator=container_cls.fetch_coordinator,
        file_handoff_store=container_cls.file_handoff_store,
    )


# ==============================================================================
#   9. RAG 检索增强生成 (RAG Pipeline)
# ==============================================================================

def _register_rag(container_cls: Any) -> None:

    # ----- 1. 版本控制 -----
    container_cls.rag_pipeline_version_config = providers.Singleton(
        RagPipelineVersionConfig,
        chunker_version=settings.RAG_CHUNKER_VERSION,
        semantic_indexing_text_version=settings.RAG_SEMANTIC_INDEXING_TEXT_VERSION,
        keyword_indexing_version=settings.RAG_KEYWORD_INDEXING_VERSION,
        identifier_extractor_version=settings.RAG_IDENTIFIER_EXTRACTOR_VERSION,
        dense_embedding_model_version=settings.RAG_DENSE_EMBEDDING_MODEL_VERSION,
        sparse_embedding_model_version=settings.RAG_SPARSE_EMBEDDING_MODEL_VERSION,
        contextual_indexing_version=settings.RAG_CONTEXTUAL_INDEXING_VERSION,
        context_model_version=settings.RAG_CONTEXT_MODEL_VERSION,
        context_prompt_version=settings.RAG_CONTEXT_PROMPT_VERSION,
    )

    container_cls.rag_version_service = providers.Singleton(
        RagVersionService,
        pipeline_config=container_cls.rag_pipeline_version_config,
    )

    # ----- 2. MongoDB 仓储 -----
    container_cls.rag_note_resource_repository = providers.Singleton(
        MongoNoteResourceRepository,
    )

    container_cls.rag_document_resource_repository = providers.Singleton(
        MongoDocumentResourceRepository,
    )

    container_cls.rag_manifest_repository = providers.Singleton(MongoManifestRepository)

    container_cls.rag_chunk_repository = providers.Singleton(
        MongoChunkRepository,
        mongo_client=container_cls.mongo_client,
    )

    container_cls.rag_context_cache_repository = providers.Singleton(
        MongoRagContextCacheRepository,
    )

    container_cls.rag_dense_embedding_cache_repository = providers.Singleton(
        MongoRagDenseEmbeddingCacheRepository,
    )

    container_cls.rag_query_embedding_cache_repository = providers.Singleton(
        MongoRagQueryEmbeddingCacheRepository,
    )

    # ----- 3. Redis 索引队列 -----
    container_cls.rag_indexing_queue = providers.Singleton(RedisRagIndexingQueue)

    # ----- 4. Qdrant 向量检索 -----
    container_cls.rag_qdrant_client = providers.Singleton(build_qdrant_client)

    container_cls.rag_dense_embedding_client_config = providers.Singleton(
        LiteLLMDenseEmbeddingClientConfig,
        model=settings.RAG_DENSE_EMBEDDING_MODEL,
        api_base=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        dimensions=settings.RAG_DENSE_VECTOR_SIZE,
    )

    container_cls.rag_qdrant_collection_config = providers.Singleton(
        QdrantCollectionConfig,
        collection_name=settings.RAG_QDRANT_COLLECTION_NAME,
        dense_vector_size=settings.RAG_DENSE_VECTOR_SIZE,
        dense_distance=models.Distance.COSINE,
    )

    container_cls.rag_qdrant_collection_manager = providers.Singleton(
        QdrantCollectionManager,
        client=container_cls.rag_qdrant_client,
        config=container_cls.rag_qdrant_collection_config,
    )

    container_cls.rag_qdrant_chunk_indexer = providers.Singleton(
        QdrantChunkIndexer,
        client=container_cls.rag_qdrant_client,
        config=container_cls.rag_qdrant_collection_config,
    )

    container_cls.rag_dense_embedding_client = providers.Singleton(
        LiteLLMDenseEmbeddingClient,
        config=container_cls.rag_dense_embedding_client_config,
    )

    container_cls.rag_qdrant_chunk_retriever = providers.Singleton(
        QdrantChunkRetriever,
        client=container_cls.rag_qdrant_client,
        config=container_cls.rag_qdrant_collection_config,
        dense_embedding_client=container_cls.rag_dense_embedding_client,
        query_embedding_cache_repository=container_cls.rag_query_embedding_cache_repository,
        query_embedding_model_version=settings.RAG_DENSE_EMBEDDING_MODEL_VERSION,
    )

    # ----- 5. Elasticsearch 关键词索引 -----
    container_cls.rag_elasticsearch_client_config = providers.Singleton(
        ElasticsearchClientConfig,
        uris=settings.ELASTICSEARCH_URIS,
        username=settings.ELASTICSEARCH_USERNAME,
        password=settings.ELASTICSEARCH_PASSWORD,
    )

    container_cls.rag_elasticsearch_client = providers.Singleton(
        build_elasticsearch_client,
        config=container_cls.rag_elasticsearch_client_config,
    )

    container_cls.rag_elasticsearch_keyword_indexer = providers.Singleton(
        ElasticsearchKeywordIndexer,
        client=container_cls.rag_elasticsearch_client,
        index_name=settings.RAG_ELASTICSEARCH_INDEX_NAME,
    )

    container_cls.rag_elasticsearch_keyword_retriever = providers.Singleton(
        ElasticsearchKeywordRetriever,
        client=container_cls.rag_elasticsearch_client,
        index_name=settings.RAG_ELASTICSEARCH_INDEX_NAME,
    )

    # ----- 6. 切块 (Chunking) -----
    container_cls.rag_chunking_config = providers.Singleton(ChunkingConfig)

    container_cls.rag_chunker = providers.Singleton(
        RagChunker,
        config=container_cls.rag_chunking_config,
    )

    # ----- 7. LLM 上下文增强 (Contextual Indexing) -----
    container_cls.rag_context_client_config = providers.Singleton(
        LiteLLMContextClientConfig,
        model=settings.RAG_CONTEXT_MODEL,
        api_base=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        max_tokens=settings.RAG_CONTEXT_MAX_TOKENS,
        temperature=settings.RAG_CONTEXT_TEMPERATURE,
    )

    container_cls.rag_context_client = providers.Singleton(
        LiteLLMContextClient,
        config=container_cls.rag_context_client_config,
    )

    container_cls.rag_context_builder_config = providers.Singleton(RagContextBuilderConfig)

    container_cls.rag_context_builder = providers.Singleton(
        RagContextBuilder,
        context_client=container_cls.rag_context_client,
        config=container_cls.rag_context_builder_config,
        cache_repository=container_cls.rag_context_cache_repository,
    )

    container_cls.rag_indexing_text_builder = providers.Singleton(RagIndexingTextBuilder)

    # ----- 8. 密集向量嵌入 (Dense Embedding) -----


    container_cls.rag_cached_dense_embedding_client = providers.Singleton(
        CachedDenseEmbeddingClient,
        inner_client=container_cls.rag_dense_embedding_client,
        model_version=settings.RAG_DENSE_EMBEDDING_MODEL_VERSION,
    )

    # ----- 9. 索引构建流水线 (Ingestion Pipeline) -----
    container_cls.rag_note_resource_handler = providers.Singleton(
        NoteResourceHandler,
        repository=container_cls.rag_note_resource_repository,
    )

    container_cls.rag_document_resource_handler = providers.Singleton(
        DocumentResourceHandler,
        repository=container_cls.rag_document_resource_repository,
    )

    container_cls.rag_resource_service = providers.Singleton(
        ResourceService,
        handlers=providers.List(
            container_cls.rag_note_resource_handler,
            container_cls.rag_document_resource_handler,
        ),
        manifest_repository=container_cls.rag_manifest_repository,
        version_service=container_cls.rag_version_service,
        index_message_repository=container_cls.rag_indexing_queue,
    )

    container_cls.rag_resource_index_builder = providers.Singleton(
        RagResourceIndexBuilder,
        chunker=container_cls.rag_chunker,
        context_builder=container_cls.rag_context_builder,
        indexing_text_builder=container_cls.rag_indexing_text_builder,
        chunk_repository=container_cls.rag_chunk_repository,
        dense_embedding_client=container_cls.rag_dense_embedding_client,
        qdrant_collection_manager=container_cls.rag_qdrant_collection_manager,
        qdrant_chunk_indexer=container_cls.rag_qdrant_chunk_indexer,
        elasticsearch_keyword_indexer=container_cls.rag_elasticsearch_keyword_indexer,
        manifest_repository=container_cls.rag_manifest_repository,
    )

    container_cls.rag_index_processor = providers.Singleton(
        RagIndexProcessor,
        resource_service=container_cls.rag_resource_service,
        version_service=container_cls.rag_version_service,
        index_builder=container_cls.rag_resource_index_builder,
        manifest_repository=container_cls.rag_manifest_repository,
    )

    container_cls.rag_index_worker = providers.Singleton(
        RagIndexWorker,
        indexing_queue_repository=container_cls.rag_indexing_queue,
        processor=container_cls.rag_index_processor,
        consumer_group=settings.RAG_INDEX_CONSUMER_GROUP,
        consumer_name=settings.SERVICE_NAME,
    )

    container_cls.rag_index_worker_runner = providers.Singleton(
        RagIndexWorkerRunner,
        indexing_queue=container_cls.rag_indexing_queue,
        worker=container_cls.rag_index_worker,
        consumer_group=settings.RAG_INDEX_CONSUMER_GROUP,
    )

    # ----- 10. 多路检索 & 重排 -----
    container_cls.rag_manifest_resolver = providers.Singleton(
        RagManifestResolver,
        manifest_repository=container_cls.rag_manifest_repository,
    )

    container_cls.rag_retrieval_service = providers.Singleton(
        RagRetrievalOrchestrator,
        manifest_resolver=container_cls.rag_manifest_resolver,
        qdrant_retriever=container_cls.rag_qdrant_chunk_retriever,
        elasticsearch_retriever=container_cls.rag_elasticsearch_keyword_retriever,
    )

    container_cls.rag_candidate_fusion = providers.Singleton(RagCandidateFusion)

    container_cls.rag_evidence_assembler = providers.Singleton(
        RagEvidenceAssembler,
        chunk_repository=container_cls.rag_chunk_repository,
    )

    container_cls.rag_parent_aggregator = providers.Singleton(RagParentAggregator)

    # ----- 11. ZeroEntropy 重排 -----
    container_cls.rag_zero_entropy_client = providers.Singleton(
        AsyncZeroEntropy,
        api_key=settings.ZERO_ENTROPY_API_KEY,
        base_url=settings.ZERO_ENTROPY_BASE_URL,
        timeout=settings.ZERO_ENTROPY_TIMEOUT_SECONDS,
    )

    container_cls.rag_reranker = providers.Singleton(
        ZeroEntropyReranker,
        client=container_cls.rag_zero_entropy_client,
        model=settings.RAG_RERANKER_ZE_MODEL,
    )

    # ----- 12. 完备性评估 -----
    container_cls.rag_evidence_sufficiency_evaluator = providers.Singleton(
        EvidenceSufficiencyEvaluator,
    )

    # ----- 13. 检索管线总控 -----
    container_cls.rag_retrieval_pipeline = providers.Singleton(
        RagRetrievalPipeline,
        retrieval_orchestrator=container_cls.rag_retrieval_service,
        candidate_fusion=container_cls.rag_candidate_fusion,
        evidence_assembler=container_cls.rag_evidence_assembler,
        reranker=container_cls.rag_reranker,
        sufficiency_evaluator=container_cls.rag_evidence_sufficiency_evaluator,
        parent_aggregator=container_cls.rag_parent_aggregator,
    )

    container_cls.rag_context_assembler = providers.Singleton(RagContextAssembler)

    container_cls.rag_service = providers.Singleton(
        RagService,
        resource_service=container_cls.rag_resource_service,
        version_service=container_cls.rag_version_service,
        manifest_repository=container_cls.rag_manifest_repository,
        retrieval_pipeline=container_cls.rag_retrieval_pipeline,
        context_assembler=container_cls.rag_context_assembler,
    )

    # ----- 14. GC 清理常驻 Worker -----
    container_cls.rag_index_gc_service = providers.Singleton(
        RagIndexGcService,
        manifest_repository=container_cls.rag_manifest_repository,
        chunk_repository=container_cls.rag_chunk_repository,
        qdrant_chunk_indexer=container_cls.rag_qdrant_chunk_indexer,
        elasticsearch_keyword_indexer=container_cls.rag_elasticsearch_keyword_indexer,
    )

    container_cls.rag_index_gc_scheduler = providers.Singleton(
        RagIndexGcScheduler,
        gc_service=container_cls.rag_index_gc_service,
    )


# ==============================================================================
#   10. 工具注册 (Tool Registry)
# ==============================================================================

def _register_tools(container_cls: Any) -> None:
    tool_providers: List[providers.Provider] = []

    def tool(attr_name: str, *singleton_args: Any, **singleton_kwargs: Any) -> None:
        provider = providers.Singleton(*singleton_args, **singleton_kwargs)
        setattr(container_cls, attr_name, provider)
        tool_providers.append(provider)

    # ----- 核心系统工具 -----
    tool(
        "search_history_tool",
        SearchHistoricalMessagesTool,
        message_repo=container_cls.message_repo,
    )

    tool(
        "load_skill_tool",
        LoadSkillTool,
        skill_repo=container_cls.skill_repo,
    )

    tool(
        "load_skill_asset_tool",
        LoadSkillAssetTool,
        skill_repo=container_cls.skill_repo,
        skill_asset_loader=container_cls.skill_asset_loader,
    )

    # ----- 联网与知识检索 -----
    tool(
        "web_search_tool",
        WebSearchTool,
        coordinator=container_cls.web_search_coordinator,
    )

    tool(
        "web_fetch_tool",
        WebFetchTool,
        fetcher=container_cls.fetch_coordinator,
        file_handoff_store=container_cls.file_handoff_store,
    )

    tool(
        "web_crawl_tool",
        WebCrawlTool,
        service=container_cls.web_crawl_service,
    )

    tool(
        "rag_search_tool",
        RagSearchTool,
        rag_service=container_cls.rag_service,
    )

    tool("evidence_rank_tool", EvidenceRankTool)

    # ----- 数理计算工具 -----
    container_cls.python_math_engine = providers.Singleton(PythonMathEngine)

    container_cls.python_math_solver_service = providers.Singleton(
        PythonMathSolverService,
        engine=container_cls.python_math_engine,
    )

    tool(
        "python_math_solver_tool",
        PythonMathSolverTool,
        service=container_cls.python_math_solver_service,
    )

    container_cls.sage_http_client = providers.Singleton(
        httpx.AsyncClient,
        timeout=tool_settings.SAGE_MATH_WORKER_TIMEOUT_SECONDS,
    )

    container_cls.sage_runtime_client = providers.Singleton(
        SageRuntimeClient,
        http_client=container_cls.sage_http_client,
        base_url=settings.SAGE_MATH_WORKER_URL,
    )

    container_cls.sage_math_solver_service = providers.Singleton(
        SageMathSolverService,
        client=container_cls.sage_runtime_client,
    )

    tool(
        "sage_math_solver_tool",
        SageMathSolverTool,
        service=container_cls.sage_math_solver_service,
    )

    # ----- 翻译工具 -----
    container_cls.translation_opus_config = providers.Singleton(OpusMtEngineConfig)

    container_cls.translation_model_provider = providers.Singleton(
        OpusMtModelProvider,
        config=container_cls.translation_opus_config,
    )

    container_cls.translation_engine = providers.Singleton(
        OpusMtTranslationEngine,
        model_provider=container_cls.translation_model_provider,
    )

    container_cls.translation_assist_service = providers.Singleton(
        TranslationAssistService,
        engine=container_cls.translation_engine,
    )

    tool(
        "translation_assist_tool",
        TranslationAssistTool,
        service=container_cls.translation_assist_service,
    )

    tool(
        "browse_interact_tool",
        BrowseInteractTool,
        timeout=tool_settings.BROWSER_INTERACT_TIMEOUT_SECONDS,
        headless=tool_settings.BROWSER_INTERACT_HEADLESS,
        disable_sandbox=tool_settings.BROWSER_INTERACT_DISABLE_SANDBOX,
        disable_dev_shm_usage=tool_settings.BROWSER_INTERACT_DISABLE_DEV_SHM_USAGE,
    )

    # ----- 文档处理工具 -----
    tool(
        "document_parse_tool",
        DocumentParseTool,
        parse_service=container_cls.document_parse_service,
        temp_file_resolver=container_cls.document_file_resolver,
    )

    tool(
        "document_export_tool",
        DocumentExportTool,
        export_service=container_cls.document_export_service,
        content_store=providers.Object(tool_content_store),
    )

    tool(
        "document_convert_tool",
        DocumentConvertTool,
        convert_service=container_cls.document_convert_service,
    )

    tool("tool_content_read_tool", ToolContentReadTool)

    tool("tool_content_batch_read_tool", ToolContentBatchReadTool)

    # ----- 工具注册表单例 -----
    container_cls.tool_instances = providers.List(*tool_providers)

    container_cls.tool_registry = providers.Singleton(
        _build_registry,
        tool_instances=container_cls.tool_instances,
    )


# ==============================================================================
#   11. 应用层服务 (Application Services)
# ==============================================================================

def _register_application(container_cls: Any) -> None:

    # ----- Search Provider Config -----
    container_cls.search_provider_config_repository = providers.Singleton(
        SearchProviderConfigRepository,
    )

    container_cls.search_provider_credential_cipher = providers.Singleton(
        SearchProviderCredentialCipher,
        master_key=tool_settings.SEARCH_PROVIDER_CREDENTIAL_MASTER_KEY,
        key_id=tool_settings.SEARCH_PROVIDER_CREDENTIAL_KEY_ID,
    )

    container_cls.search_provider_config_validator = providers.Singleton(
        SearchProviderConfigValidator,
        client=container_cls.web_search_http_client,
        cache=container_cls.web_search_cache,
    )

    container_cls.search_provider_config_service = providers.Singleton(
        SearchProviderConfigService,
        repository=container_cls.search_provider_config_repository,
        cipher=container_cls.search_provider_credential_cipher,
        validator=container_cls.search_provider_config_validator,
    )

    container_cls.search_provider_config_api_service = providers.Singleton(
        SearchProviderConfigApiService,
        service=container_cls.search_provider_config_service,
    )

    container_cls.rag_api_service = providers.Singleton(
        RagApiService,
        rag_service=container_cls.rag_service,
    )

    # ----- Chat Coordinator -----
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
        search_provider_config_service=container_cls.search_provider_config_service,
    )


# ==============================================================================
#   Build Entry
# ==============================================================================

def build_container() -> Container:
    _register_core(Container)
    _register_persistence(Container)
    _register_rpc(Container)
    _register_skill(Container)
    _register_document_parse(Container)
    _register_document_export(Container)
    _register_document_convert(Container)
    _register_web(Container)
    _register_rag(Container)
    _register_tools(Container)
    _register_application(Container)

    return Container()


container = build_container()

__all__ = [
    "Container",
    "container",
]
