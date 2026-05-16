from typing import Any

from chat.core.config.app_settings import settings
from chat.core.config.bootstrap_settings import bootstrap_settings
from common.clients.file_storage import FileStorageClient
from common.cloud.nacos_client import nacos_client_manager
from common.cloud.service_discovery import ServiceDiscovery
from common.http.rpc_client import RpcClient
from dependency_injector import providers
from v2.nacos import NacosNamingService


async def _provide_nacos_naming() -> NacosNamingService:
    """延迟到首次 await，避免在 import 阶段触发 async Nacos 建连。"""
    return await nacos_client_manager.get_naming_client()


def register_rpc_providers(container_cls: Any) -> None:
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
