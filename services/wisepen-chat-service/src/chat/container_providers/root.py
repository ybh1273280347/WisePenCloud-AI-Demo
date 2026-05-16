from dependency_injector import containers

from chat.container_providers.application import register_application_providers
from chat.container_providers.attachment_read import register_attachment_read_providers
from chat.container_providers.core import register_core_providers
from chat.container_providers.document_convert import (
    register_document_convert_providers,
)
from chat.container_providers.document_export import register_document_export_providers
from chat.container_providers.document_parse import register_document_parse_providers
from chat.container_providers.persistence import register_persistence_providers
from chat.container_providers.rpc import register_rpc_providers
from chat.container_providers.skill import register_skill_providers
from chat.container_providers.tools import register_tool_providers
from chat.container_providers.web import register_web_providers


class Container(containers.DeclarativeContainer):
    """应用级依赖注入容器。"""

    pass


def build_container() -> Container:
    register_core_providers(Container)
    register_persistence_providers(Container)
    register_rpc_providers(Container)
    register_skill_providers(Container)
    register_document_parse_providers(Container)
    register_document_export_providers(Container)
    register_document_convert_providers(Container)
    register_attachment_read_providers(Container)
    register_web_providers(Container)
    register_tool_providers(Container)
    register_application_providers(Container)

    return Container()
