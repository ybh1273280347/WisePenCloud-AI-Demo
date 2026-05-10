```text
请重构 src/chat/container.py，采用“方案 A：在 container.py 同文件内使用注册函数”，不要拆新文件，避免多处 import 修改。

背景：
当前 Container class body 中注册了大量 provider，导致 review 困难。
目标是把 provider 装配按业务域拆成多个 register_xxx_providers(Container) 函数，但仍保留在 container.py 内。
这是对原代码的结构整理，不要按其他文件的新风格重写；请保留当前 container.py 中辅助函数的下划线命名风格，例如 _provide_nacos_naming、_build_registry。

核心要求：
1. 不拆分到 container_modules。
2. 不新增多个文件。
3. 不改变外部导入方式。
4. 仍然保留：
   container = Container()
5. 最终 provider 仍挂在 Container 上，外部访问路径不变，例如：
   container.web_fetch_tool
   container.fetch_coordinator
   container.tool_registry
   container.chat_turn_coordinator
6. 不改变已有 provider 名称，除非当前明显拼写错误。
7. 不改变工具注册顺序。
8. 不改变工具 name / description / schema。
9. 不改变业务行为。
10. 不要使用 Optional 依赖兜底。
11. 不要把纯函数 helper 注册进 container。
12. 不要使用 getattr(settings, "...", default)。
13. 保持项目 typing 风格：
    - 继续使用 typing.List
    - 不要把 Optional 改成 T | None
    - Union 才用 A | B

请按以下方式重构。

一、保留当前模块级 helper

继续保留：

async def _provide_nacos_naming() -> NacosNamingService:
    ...

def _build_registry(tool_providers: List[providers.Provider]) -> ToolRegistry:
    ...

不要改名成无下划线。
不要移动到其他文件。

二、Container 类体简化

把 Container 类改成只保留类定义和 docstring：

class Container(containers.DeclarativeContainer):
    """依赖注入容器，管理应用级组件生命周期。"""
    pass

不要继续在 class body 中堆所有 provider。

三、在 Container 类定义之后增加注册函数

按业务域新增以下模块级函数，函数名保留下划线前缀，表示 container.py 内部装配函数：

def _register_core_providers(container_cls: type[Container]) -> None:
    ...

def _register_persistence_providers(container_cls: type[Container]) -> None:
    ...

def _register_rpc_providers(container_cls: type[Container]) -> None:
    ...

def _register_skill_providers(container_cls: type[Container]) -> None:
    ...

def _register_web_providers(container_cls: type[Container]) -> None:
    ...

def _register_tool_providers(container_cls: type[Container]) -> None:
    ...

def _register_application_providers(container_cls: type[Container]) -> None:
    ...

注意：
- 因为函数定义在 Container 后面，type annotation 如果麻烦，可以不标注 container_cls 类型，直接写 def _register_core_providers(container_cls) -> None。
- 不要为了类型标注引入复杂 forward reference。
- 当前任务重点是结构清晰，不是类型系统重构。

四、注册函数职责

1. _register_core_providers

注册：
- llm_provider
- memory_provider
- model_resolver
- kafka_producer

对应原逻辑：
container_cls.llm_provider = providers.Singleton(LiteLLMAdapter)
container_cls.memory_provider = providers.Singleton(Mem0Adapter)
container_cls.model_resolver = providers.Singleton(ModelResolver)
container_cls.kafka_producer = providers.Singleton(
    KafkaProducerClient,
    bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
)

2. _register_persistence_providers

注册：
- session_repo
- message_repo
- hot_context_repo
- skill_repo

对应原逻辑：
container_cls.session_repo = providers.Singleton(MongoSessionRepository)
container_cls.message_repo = providers.Singleton(MongoMessageRepository)
container_cls.hot_context_repo = providers.Singleton(RedisHotContext)
container_cls.skill_repo = providers.Singleton(MongoSkillRepository)

3. _register_rpc_providers

注册：
- service_discovery
- rpc_client
- file_storage_client

保持原配置不变：

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

4. _register_skill_providers

注册：
- oss_skill_asset_loader
- skill_asset_loader
- skill_matcher
- skill_cache_refresher

保持 DEV 分支：

container_cls.oss_skill_asset_loader = providers.Singleton(
    OssSkillAssetLoader,
    file_storage_client=container_cls.file_storage_client,
    cache_dir=settings.skill_oss_cache_path,
    cache_ttl_seconds=settings.SKILL_OSS_CACHE_TTL_SECONDS,
    gc_interval_seconds=settings.SKILL_OSS_CACHE_GC_INTERVAL_SECONDS,
)

if settings.DEV:
    container_cls.skill_asset_loader = providers.Singleton(
        LocalFSSkillAssetLoader,
        root_dir=str(settings.skill_assets_cache_path),
        oss_fallback=container_cls.oss_skill_asset_loader,
    )
else:
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

5. _register_web_providers

只注册 web search / web fetch 底层能力，不注册 Tool。

注册：
- web_search_coordinator
- content_processor
- static_fetcher
- steel_fetcher_config
- steel_fetcher
- local_script_fetcher
- fetch_coordinator

保持当前依赖注入语义：

container_cls.web_search_coordinator = providers.Singleton(
    create_search_coordinator,
)

container_cls.content_processor = providers.Singleton(
    ContentProcessor,
    min_content_length=settings.WEB_FETCH_MIN_CONTENT_LENGTH,
    document_min_content_length=settings.WEB_FETCH_DOCUMENT_MIN_CONTENT_LENGTH,
    max_document_size=settings.WEB_FETCH_MAX_DOCUMENT_SIZE,
)

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

注意：
- fetch_coordinator 必须在 _register_tool_providers 执行前注册完成。
- 不要在 WebFetchTool / WebSearchTool 内部兜底创建 coordinator。

6. _register_tool_providers

注册所有 Tool 和 ToolRegistry。

注册：
- search_history_tool
- load_skill_tool
- load_skill_asset_tool
- web_search_tool
- web_fetch_tool
- browse_interact_tool
- tool_content_read_tool
- tool_providers
- tool_registry

保持工具注册顺序不变：

container_cls.search_history_tool = providers.Singleton(
    SearchHistoricalMessagesTool,
    message_repo=container_cls.message_repo,
)

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

container_cls.browse_interact_tool = providers.Singleton(
    BrowseInteractTool,
)

container_cls.tool_content_read_tool = providers.Singleton(
    ToolContentReadTool,
)

container_cls.tool_providers = providers.List(
    container_cls.search_history_tool,
    container_cls.load_skill_tool,
    container_cls.load_skill_asset_tool,
    container_cls.web_search_tool,
    container_cls.web_fetch_tool,
    container_cls.browse_interact_tool,
    container_cls.tool_content_read_tool,
)

container_cls.tool_registry = providers.Singleton(
    _build_registry,
    tool_providers=container_cls.tool_providers,
)

7. _register_application_providers

注册：
- chat_turn_coordinator

保持原依赖不变：

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

五、注册调用顺序

在所有 _register_xxx_providers 函数定义之后，container = Container() 之前，按顺序调用：

_register_core_providers(Container)
_register_persistence_providers(Container)
_register_rpc_providers(Container)
_register_skill_providers(Container)
_register_web_providers(Container)
_register_tool_providers(Container)
_register_application_providers(Container)

顺序不能乱：
- skill 依赖 persistence 和 rpc
- web tool 依赖 web providers
- application 依赖 tools、core、persistence、skill

最后保留：

container = Container()

六、必须修复当前顺序问题

当前代码中 web_search_tool 使用 fetch_coordinator，但 fetch_coordinator 在后面才定义。
重构后必须保证：
- _register_web_providers 先注册 fetch_coordinator
- _register_tool_providers 后注册 web_search_tool / web_fetch_tool

七、不要做

1. 不要创建 chat/container_modules。
2. 不要拆文件。
3. 不要改外部 import。
4. 不要删除 container = Container()。
5. 不要改变 provider 名称。
6. 不要改变 ToolRegistry 工具顺序。
7. 不要把纯函数注册到 container，例如：
   - normalize_text
   - detect_page_block
   - create_content_chunks
   - format_windowed_content
   - is_document_url
8. 不要注册 ToolContentStore。
9. 不要恢复工具类内部 Optional 依赖兜底。
10. 不要使用 getattr(settings, "...", default)。
11. 不要新增默认模块实例，例如 default_content_processor。
12. 不要按其他模块的新风格强行改命名；container.py 内部 helper 继续使用下划线前缀。

八、验收标准

1. container.py 中 Container class body 不再堆大量 provider。
2. provider 仍能通过原名称访问。
3. web_search_tool 不再引用未定义的 fetch_coordinator。
4. WebFetchTool / WebSearchTool 的依赖由 container 注入。
5. 应用启动时 container = Container() 正常。
6. 工具注册顺序不变。
7. 没有新增跨文件导入修改。
```

补充一句给 Codex 的判断标准：

```text
这次不是重构 DI 框架，也不是拆成子容器；只是把同一个 container.py 内的 provider 装配按业务域函数化，降低 review 难度，并修复 provider 顺序问题。
```
