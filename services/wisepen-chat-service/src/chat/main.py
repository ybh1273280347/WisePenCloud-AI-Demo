from __future__ import annotations

import os
from contextlib import asynccontextmanager

import uvicorn
from beanie import init_beanie
from chat.api.endpoints import chat as chat_endpoints
from chat.api.endpoints import chat_file as chat_file_endpoints
from chat.api.endpoints import document_export as document_export_endpoints
from chat.api.endpoints import memory as memory_endpoints
from chat.api.endpoints import model as model_endpoints
from chat.api.endpoints import search_provider as search_provider_endpoints
from chat.api.endpoints import session as session_endpoints
from chat.api.endpoints import user_preferences as user_preferences_endpoints
from chat.api.router import api_router
from chat.container import (
    container,  # noqa: F401 — 触发 dependency_injector wiring，不可删除
)
from chat.core.config.app_settings import settings
from chat.core.config.bootstrap_settings import bootstrap_settings
from chat.domain.entities import (
    ChatMessage,
    ChatSession,
    Model,
    ModelProviderMapping,
    Provider,
    UserSearchProviderConfig,
    UserPreferences,
    Skill,
)
from common.cloud.nacos_client import nacos_client_manager
from common.logger import log_error, log_event, setup_logging_intercept
from common.web.exception_handlers import setup_global_exception_handlers
from common.web.middleware import SecurityHeaderMiddleware
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pymongo import AsyncMongoClient

setup_logging_intercept(bootstrap_settings.LOG_LEVEL)


os.environ["no_proxy"] = "localhost,127.0.0.1,wisepen-dev-server"
os.environ["NO_PROXY"] = "localhost,127.0.0.1,wisepen-dev-server"

DOCUMENT_MODELS = [
    ChatSession,
    ChatMessage,
    Provider,
    Model,
    ModelProviderMapping,
    Skill,
    UserSearchProviderConfig,
    UserPreferences,
]

SHUTDOWN_RESOURCES = (
    ("Skill cache refresher", lambda: container.skill_cache_refresher().stop),
    ("Kafka Producer", lambda: container.kafka_producer().stop),
    ("SkillAssetLoader", lambda: container.skill_asset_loader().stop),
    ("OCR Processor", lambda: container.ocr_processor().close),
    ("Browser Pool", lambda: container.document_export_browser_pool().stop),
    ("BrowseInteractTool", lambda: container.browse_interact_tool().close),
    ("WebSearchCoordinator", lambda: container.web_search_coordinator().close),
    ("Static Fetcher", lambda: container.static_fetcher().close),
    ("Steel Fetcher", lambda: container.steel_fetcher().close),
    ("LocalScriptFetcher", lambda: container.local_script_fetcher().close),
    ("PaperSearchTool", lambda: container.paper_search_tool().close),
    ("GitHubSearchTool", lambda: container.github_search_tool().close),
    ("PackageIntelligenceTool", lambda: container.package_intelligence_tool().close),
    ("WeatherTool", lambda: container.weather_tool().close),
    ("AirQualityTool", lambda: container.air_quality_tool().close),
    ("MathComputeTool", lambda: container.math_compute_tool().close),
    ("TranslationAssistTool", lambda: container.translation_assist_tool().close),
    ("EvidenceRankTool", lambda: container.evidence_rank_tool().close),
    ("RpcClient", lambda: container.rpc_client().aclose),
    ("ServiceDiscovery", lambda: container.service_discovery().close),
)


async def stop_resource(name: str, close_call) -> None:
    try:
        await close_call()
    except Exception as e:
        log_error(f"{name} 关闭", e)


async def init_database() -> None:
    mongo_client = AsyncMongoClient(settings.MONGODB_URL)
    await init_beanie(
        database=mongo_client[settings.MONGODB_DB_NAME],
        document_models=DOCUMENT_MODELS,
    )
    log_event("Beanie 初始化", db=settings.MONGODB_DB_NAME)


async def start_application() -> None:
    log_event(f"{settings.APP_NAME} 启动")

    # 初始化 Beanie
    await init_database()

    # 注册 Nacos 服务
    try:
        await nacos_client_manager.register_instance()
    except Exception as e:
        log_error("Nacos 服务注册", e)

    # 启动 Kafka Producer
    await container.kafka_producer().start()

    # 启动 Skill cache refresher
    await container.skill_cache_refresher().start()

    # 启动 Skill 资产加载器
    try:
        await container.skill_asset_loader().start()
    except Exception as e:
        log_error("SkillAssetLoader 启动", e)

    log_event(f"{settings.APP_NAME} 就绪", port=settings.SERVICE_PORT)


async def shutdown_application() -> None:
    log_event(f"{settings.APP_NAME} 关闭")

    for name, close_call_factory in SHUTDOWN_RESOURCES:
        await stop_resource(name, close_call_factory())

    try:
        await nacos_client_manager.deregister_instance()
    except Exception as e:
        log_error("Nacos 服务注销", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- 启动阶段 ---
    await start_application()

    # --- 运行阶段 ---
    try:
        yield
    finally:
        # --- 关闭阶段 ---
        await shutdown_application()


container.wire(
    modules=[
        chat_endpoints,
        session_endpoints,
        memory_endpoints,
        model_endpoints,
        search_provider_endpoints,
        user_preferences_endpoints,
        document_export_endpoints,
        chat_file_endpoints,
    ]
)  # 注入依赖到路由模块
app = FastAPI(title=settings.APP_NAME, lifespan=lifespan, docs_url="/docs")

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册安全中间件：校验 X-From-Source，解析 X-User-Id 等网关透传 Headers
app.add_middleware(
    SecurityHeaderMiddleware, from_source_secret=settings.FROM_SOURCE_SECRET
)

# 注册全局异常处理器：ServiceException / PermissionException / RequestValidationError 统一转为 R 格式
setup_global_exception_handlers(app, is_dev=settings.DEV)

# 挂载业务路由
app.include_router(document_export_endpoints.router)
app.include_router(api_router, prefix="/chat")

if __name__ == "__main__":
    uvicorn.run(
        "chat.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
        reload=False,
        workers=1,
        env_file="./.env",
        ws="websockets-sansio",
    )
