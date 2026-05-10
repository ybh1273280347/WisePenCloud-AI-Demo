请清理项目中的依赖注入行为，统一依赖装配原则。重点是 WebSearchTool / WebFetchTool / FetchCoordinator / ContentProcessor 等工具链相关对象。

总体原则：

1. 不是所有单例都必须注册到 container。
   只有满足以下条件之一的对象才需要注册：
   - 生命周期需要由应用统一管理；
   - 构造依赖较多，涉及装配；
   - 被多个上层组件共享；
   - 初始化成本较高；
   - 需要在测试中替换实现；
   - 属于工具、协调器、外部服务 client、repository、provider 等应用级组件。

2. 如果一个对象是单例，并且涉及依赖装配，应该注册到 container。
   例如：
   - WebSearchTool 依赖 SearchCoordinator；
   - WebFetchTool 依赖 FetchCoordinator；
   - FetchCoordinator 依赖 StaticFetcher / SteelFetcher / LocalScriptFetcher / ContentProcessor；
   - ContentProcessor 依赖 DocumentParser；
   - ToolRegistry 依赖多个 Tool provider。
   这些应该交给 container 统一创建和注入。

3. 业务类构造函数中不要出现“依赖可选 + 内部兜底创建”的写法。
   禁止类似：

   def __init__(
       self,
       coordinator: Optional[SearchCoordinator] = None,
       fetcher: Optional[FetchCoordinator] = None,
   ):
       self._coordinator = coordinator or create_search_coordinator()
       self._fetcher = fetcher or FetchCoordinator(settings.STEEL_BASE_URL)

   原因：
   - 这会绕过 container；
   - 隐藏真实依赖；
   - 测试和生产装配路径不一致；
   - 配置来源变得分散；
   - 初始化副作用不可控；
   - 破坏“container 保证注册成功”的前提。

4. 构造函数应该显式声明必需依赖。
   推荐：

   class WebSearchTool(BaseTool):
       def __init__(self, coordinator: SearchCoordinator):
           self._coordinator = coordinator

   class WebFetchTool(BaseTool):
       def __init__(self, fetcher: FetchCoordinator):
           self._fetcher = fetcher

   不要写 Optional，不要内部 fallback。

5. 可选参数只用于真正的可选配置，不用于必需依赖。
   例如 timeout、limit、开关类配置可以有默认值；
   但 coordinator、fetcher、repository、client、processor 这类依赖不应该 Optional。

6. settings 只应该在 container 或底层配置类中使用。
   不建议工具类直接从 settings 拼装依赖。
   推荐：
   - container 读取 settings；
   - container 构造 provider；
   - provider 注入业务类。
   业务类拿到的是已经装配好的依赖。

7. 工具类不负责创建协调器。
   工具类只负责：
   - 定义 name / description / parameters_schema；
   - 校验工具参数；
   - 调用已注入的 application service / coordinator；
   - 格式化工具返回。
   工具类不应该自己 new coordinator，不应该直接读取 settings 组装复杂依赖。

8. Coordinator 可以创建强绑定的内部 fetcher，但如果这些 fetcher 也需要配置、复用或测试替换，则优先由 container 注入。
   第一版推荐：
   - StaticFetcher 注册为 provider；
   - SteelFetcher 注册为 provider；
   - LocalScriptFetcher 注册为 provider；
   - ContentProcessor 注册为 provider；
   - FetchCoordinator 由 container 注入上述依赖。

   如果暂时不想改太多，至少要保证：
   - WebFetchTool 不自己创建 FetchCoordinator；
   - WebSearchTool 不自己创建 SearchCoordinator；
   - 复杂装配集中在 container。

9. 小型无状态 helper 不需要注册。
   不要把这些放进 container：
   - normalize_text
   - looks_like_html
   - detect_doc_type
   - create_content_chunks
   - format_windowed_content
   - URL 判断函数
   - 页面阻断检测函数
   - parse_int
   - log helper
   这些是纯函数或模块级工具，直接 import 使用即可。

10. 模块级全局实例要谨慎。
    不要为了单例随手写：
    default_content_processor = ContentProcessor()
    default_fetch_coordinator = FetchCoordinator(...)
    这类对象如果涉及装配，应交给 container。
    例外：
    - 纯内存 store，且确实需要跨工具共享，例如 tool_content_store；
    - 无外部依赖、无复杂配置、语义上就是进程内共享状态。
    但对于 coordinator / processor / tool，不建议模块级实例。

11. ToolContentStore 当前可以保留模块级实例。
    因为它是进程内内容缓存，语义上就是共享 store：
    tool_content_store = ToolContentStore()
    但 ToolContentReadTool 仍然可以直接 import 使用，除非后续要把 store 也放进 container 统一管理。
    第一版不强制迁移 ToolContentStore。

12. container 是装配唯一入口。
    应保证生产路径中所有工具依赖都来自 container。
    启动时如果 provider 缺失，应尽早失败，而不是业务类内部 fallback 创建一个默认对象。

具体修改要求：

一、WebSearchTool

如果当前存在：

def __init__(self, coordinator: Optional[SearchCoordinator] = None):
    self._coordinator = coordinator or create_search_coordinator()

改为：

def __init__(self, coordinator: SearchCoordinator):
    self._coordinator = coordinator

container 中注册：

search_coordinator = providers.Singleton(create_search_coordinator)

web_search_tool = providers.Singleton(
    WebSearchTool,
    coordinator=search_coordinator,
)

二、WebFetchTool

如果当前存在：

def __init__(self, fetcher: Optional[FetchCoordinator] = None):
    self._fetcher = fetcher or FetchCoordinator(settings.STEEL_BASE_URL)

改为：

def __init__(self, fetcher: FetchCoordinator):
    self._fetcher = fetcher

container 中注册 FetchCoordinator，并注入 WebFetchTool。

三、ContentProcessor

ContentProcessor 不要做模块级 default_content_processor。

注册到 container：

content_processor = providers.Singleton(
    ContentProcessor,
    min_content_length=settings.WEB_FETCH_MIN_CONTENT_LENGTH,
    document_min_content_length=settings.WEB_FETCH_DOCUMENT_MIN_CONTENT_LENGTH,
    max_document_size=settings.WEB_FETCH_MAX_DOCUMENT_SIZE,
)

四、FetchCoordinator

推荐构造函数显式接收已装配依赖：

def __init__(
    self,
    static_fetcher: StaticFetcher,
    steel_fetcher: SteelFetcher,
    local_script_fetcher: LocalScriptFetcher,
    processor: ContentProcessor,
    min_content_length: int,
    last_resort_min_length: int,
    cache_ttl_seconds: int,
    cache_max_items: int,
):
    ...

不再在 FetchCoordinator 内部创建 StaticFetcher / SteelFetcher / LocalScriptFetcher / ContentProcessor。

container 中注册：

static_fetcher = providers.Singleton(
    StaticFetcher,
    timeout=settings.WEB_FETCH_STATIC_TIMEOUT,
    max_response_bytes=settings.WEB_FETCH_MAX_DOCUMENT_SIZE,
)

steel_fetcher_config = providers.Singleton(
    SteelFetcherConfig,
    base_url=settings.STEEL_BASE_URL,
    timeout=settings.WEB_FETCH_BROWSER_TIMEOUT,
)

steel_fetcher = providers.Singleton(
    SteelFetcher,
    config=steel_fetcher_config,
)

local_script_fetcher = providers.Singleton(
    LocalScriptFetcher,
    timeout=settings.WEB_FETCH_BROWSER_TIMEOUT,
)

fetch_coordinator = providers.Singleton(
    FetchCoordinator,
    static_fetcher=static_fetcher,
    steel_fetcher=steel_fetcher,
    local_script_fetcher=local_script_fetcher,
    processor=content_processor,
    min_content_length=settings.WEB_FETCH_MIN_CONTENT_LENGTH,
    last_resort_min_length=settings.WEB_FETCH_LAST_RESORT_MIN_LENGTH,
    cache_ttl_seconds=settings.WEB_FETCH_CACHE_TTL_SECONDS,
    cache_max_items=settings.WEB_FETCH_CACHE_MAX_ITEMS,
)

web_fetch_tool = providers.Singleton(
    WebFetchTool,
    fetcher=fetch_coordinator,
)

五、保留合理默认值的位置

可以保留默认值的地方：
- dataclass config，例如 SteelFetcherConfig.timeout = 60.0；
- 纯函数参数，例如 limit 默认值；
- 低层类的简单配置默认值，用于单独测试。

但生产装配应由 container 显式传入 settings，不依赖业务类 fallback。

六、不要做

- 不要把所有类都注册成 Singleton。
- 不要把纯函数注册到 container。
- 不要在工具类内部创建 coordinator。
- 不要在 coordinator 内部偷偷创建复杂依赖，如果这些依赖已经由 container 管理。
- 不要使用 Optional[Dependency] = None + dependency or DefaultDependency() 作为生产装配方式。
- 不要用 getattr(settings, "...", default) 做配置兜底。
- 不要改变工具 name / description / schema。
- 不要改变工具 execute 的返回格式。
- 不要改变 FetchCoordinator.fetch 的降级链语义。
- 不要改变 ToolContentStore 当前模块级共享 store，除非有明确需求。