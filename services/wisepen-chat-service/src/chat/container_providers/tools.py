from typing import Any, List

from chat.application.tools.common.tool_content_store import tool_content_store
from chat.application.tools import (
    AirQualityTool,
    AttachmentReadTool,
    BrowseInteractTool,
    CnCalendarTool,
    DocumentConvertTool,
    DocumentExportTool,
    DocumentParseTool,
    EvidenceRankTool,
    LoadSkillAssetTool,
    LoadSkillTool,
    PaperSearchTool,
    PythonMathSolverTool,
    ResolveTimeTool,
    SageMathSolverTool,
    SearchHistoricalMessagesTool,
    SoftwareEcosystemResearchTool,
    ToolContentBatchReadTool,
    ToolContentReadTool,
    ToolRegistry,
    TranslationAssistTool,
    WeatherTool,
    WebCrawlTool,
    WebFetchTool,
    WebSearchTool,
)
from dependency_injector import providers


def _build_registry(tool_instances: List[Any]) -> ToolRegistry:
    registry = ToolRegistry()

    for tool_instance in tool_instances:
        registry.register(tool_instance)

    return registry


def register_tool_providers(container_cls: Any) -> None:
    # 工具层：各 Tool 和 ToolRegistry 均为 Singleton，由容器统一管理生命周期。
    #
    # 工具注册约定：
    # 1. tool(name, *args, **kwargs) 等价于创建 providers.Singleton，
    #    设置 container_cls 上的 provider，并按顺序加入 tool_providers。
    # 2. 调用侧只维护 provider 名称和 providers.Singleton 参数。
    # 3. 不使用字符串依赖映射，不使用额外定义对象，不扫描工具类。
    tool_providers: List[providers.Provider] = []

    def tool(
        attr_name: str,
        *singleton_args: Any,
        **singleton_kwargs: Any,
    ) -> None:
        if not isinstance(attr_name, str) or not attr_name:
            raise TypeError("tool provider attr_name must be a non-empty string")
        if hasattr(container_cls, attr_name):
            raise RuntimeError(f"container already has provider attribute: {attr_name}")

        provider = providers.Singleton(*singleton_args, **singleton_kwargs)
        setattr(container_cls, attr_name, provider)
        tool_providers.append(provider)

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
        "attachment_read_tool",
        AttachmentReadTool,
        service=container_cls.attachment_read_service,
    )
    tool(
        "paper_search_tool",
        PaperSearchTool,
    )
    tool(
        "software_ecosystem_research_tool",
        SoftwareEcosystemResearchTool,
    )
    tool(
        "resolve_time_tool",
        ResolveTimeTool,
    )
    tool(
        "weather_tool",
        WeatherTool,
    )
    tool(
        "air_quality_tool",
        AirQualityTool,
    )
    tool(
        "cn_calendar_tool",
        CnCalendarTool,
    )
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
    tool(
        "python_math_solver_tool",
        PythonMathSolverTool,
    )
    tool(
        "sage_math_solver_tool",
        SageMathSolverTool,
    )
    tool(
        "translation_assist_tool",
        TranslationAssistTool,
    )
    tool(
        "browse_interact_tool",
        BrowseInteractTool,
    )
    tool(
        "tool_content_read_tool",
        ToolContentReadTool,
    )
    tool(
        "tool_content_batch_read_tool",
        ToolContentBatchReadTool,
    )
    tool(
        "evidence_rank_tool",
        EvidenceRankTool,
    )

    container_cls.tool_instances = providers.List(*tool_providers)
    container_cls.tool_registry = providers.Singleton(
        _build_registry,
        tool_instances=container_cls.tool_instances,
    )
