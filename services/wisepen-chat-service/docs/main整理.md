可以进一步优化，但我建议仍然**只在 `main.py` 内做最小解耦**，不拆新文件。

现在这版已经比之前好很多了：`lifespan()` 只剩启动、运行、关闭三段，方向正确。剩下的问题是：`main.py` 里仍然有几类职责混在一起，可以用**函数分层 + 常量收敛**再清一点。

# 主要耦合点

## 1. `start_application()` 仍然混了太多启动职责

现在它同时负责：

```text
浏览器配置
Beanie 初始化
Nacos 注册
Kafka 启动
Skill cache refresher 启动
Skill asset loader 启动
```

建议拆成：

```python
async def init_database() -> None:
    ...


async def register_service() -> None:
    ...


async def start_background_resources() -> None:
    ...


async def start_application() -> None:
    log_event(f"{settings.APP_NAME} 启动")

    setup_browser_automation_profile(container)
    await init_database()
    await register_service()
    await start_background_resources()

    log_event(f"{settings.APP_NAME} 就绪", port=settings.SERVICE_PORT)
```

这样 `start_application()` 只表达启动流程，不塞具体细节。

---

## 2. `shutdown_application()` 可以提取关闭资源列表

现在关闭阶段虽然用了 `stop_resource()`，但还是一行一行写：

```python
await stop_resource("Skill cache refresher", container.skill_cache_refresher().stop)
await stop_resource("Kafka Producer", container.kafka_producer().stop)
...
```

这已经可以接受，但如果你关注解耦，可以进一步做成：

```python
async def stop_container_resources() -> None:
    resources = [
        ("Skill cache refresher", container.skill_cache_refresher().stop),
        ("Kafka Producer", container.kafka_producer().stop),
        ("SkillAssetLoader", container.skill_asset_loader().stop),
        ("OCR Processor", container.web_fetch_ocr_processor().close),
        ("Steel 抓取器", container.steel_fetcher().close),
        ("RpcClient", container.rpc_client().aclose),
        ("ServiceDiscovery", container.service_discovery().close),
    ]

    for name, close_call in resources:
        await stop_resource(name, close_call)
```

然后：

```python
async def shutdown_application() -> None:
    log_event(f"{settings.APP_NAME} 关闭")

    await stop_container_resources()
    await deregister_service()
```

注意：这个 list 要放在函数内部，不要做成模块级常量。否则模块 import 时可能提前实例化 provider。

---

## 3. Beanie document models 可以提成常量

现在：

```python
document_models=[ChatSession, ChatMessage, Provider, Model, ModelProviderMapping, Skill]
```

这行会越来越长。建议提成模块级常量：

```python
DOCUMENT_MODELS = [
    ChatSession,
    ChatMessage,
    Provider,
    Model,
    ModelProviderMapping,
    Skill,
]
```

然后：

```python
await init_beanie(
    database=mongo_client[settings.MONGODB_DB_NAME],
    document_models=DOCUMENT_MODELS,
)
```

这个是稳定配置，不会引入新抽象成本。

---

## 4. `container.wire(...)` 可以提成函数

现在模块底部直接：

```python
container.wire(modules=[chat_endpoints, session_endpoints, memory_endpoints, model_endpoints])
```

可以改成：

```python
WIRE_MODULES = [
    chat_endpoints,
    session_endpoints,
    memory_endpoints,
    model_endpoints,
]


def wire_container() -> None:
    container.wire(modules=WIRE_MODULES)
```

底部：

```python
wire_container()
```

好处是 app 初始化区更清晰，也方便之后增加 endpoint 模块。

---

## 5. App 创建和配置可以提成 `create_app()`

这一步仍然不拆文件，只在 `main.py` 里做：

```python
def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME, lifespan=lifespan, docs_url="/docs")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(
        SecurityHeaderMiddleware,
        from_source_secret=settings.FROM_SOURCE_SECRET,
    )

    setup_global_exception_handlers(app, is_dev=settings.DEV)
    app.include_router(api_router, prefix="/chat")

    return app
```

底部变成：

```python
wire_container()
app = create_app()
```

这样 `main.py` 仍是唯一入口，但结构更清楚：

```text
日志桥接
import
环境变量
常量
生命周期 helper
start/shutdown/lifespan
wire_container
create_app
app
uvicorn
```

---

# 推荐整理后的结构

不需要完全照抄，但大概可以整理成这样：

```python
DOCUMENT_MODELS = [
    ChatSession,
    ChatMessage,
    Provider,
    Model,
    ModelProviderMapping,
    Skill,
]

WIRE_MODULES = [
    chat_endpoints,
    session_endpoints,
    memory_endpoints,
    model_endpoints,
]


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


async def register_service() -> None:
    try:
        await nacos_client_manager.register_instance()
    except Exception as e:
        log_error("Nacos 服务注册", e)


async def deregister_service() -> None:
    try:
        await nacos_client_manager.deregister_instance()
    except Exception as e:
        log_error("Nacos 服务注销", e)


async def start_background_resources() -> None:
    await container.kafka_producer().start()
    await container.skill_cache_refresher().start()

    try:
        await container.skill_asset_loader().start()
    except Exception as e:
        log_error("SkillAssetLoader 启动", e)


async def stop_container_resources() -> None:
    resources = [
        ("Skill cache refresher", container.skill_cache_refresher().stop),
        ("Kafka Producer", container.kafka_producer().stop),
        ("SkillAssetLoader", container.skill_asset_loader().stop),
        ("OCR Processor", container.web_fetch_ocr_processor().close),
        ("Steel 抓取器", container.steel_fetcher().close),
        ("RpcClient", container.rpc_client().aclose),
        ("ServiceDiscovery", container.service_discovery().close),
    ]

    for name, close_call in resources:
        await stop_resource(name, close_call)


async def start_application() -> None:
    log_event(f"{settings.APP_NAME} 启动")

    setup_browser_automation_profile(container)
    await init_database()
    await register_service()
    await start_background_resources()

    log_event(f"{settings.APP_NAME} 就绪", port=settings.SERVICE_PORT)


async def shutdown_application() -> None:
    log_event(f"{settings.APP_NAME} 关闭")

    await stop_container_resources()
    await deregister_service()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_application()

    try:
        yield
    finally:
        await shutdown_application()


def wire_container() -> None:
    container.wire(modules=WIRE_MODULES)


def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME, lifespan=lifespan, docs_url="/docs")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(SecurityHeaderMiddleware, from_source_secret=settings.FROM_SOURCE_SECRET)
    setup_global_exception_handlers(app, is_dev=settings.DEV)
    app.include_router(api_router, prefix="/chat")

    return app


wire_container()
app = create_app()
```

# 我建议的修改边界

可以做：

```text
1. 提取 init_database
2. 提取 register_service / deregister_service
3. 提取 start_background_resources
4. 提取 stop_container_resources
5. 提取 wire_container
6. 提取 create_app
7. 提取 DOCUMENT_MODELS / WIRE_MODULES
```

不要做：

```text
1. 不拆新文件
2. 不改启动顺序
3. 不改关闭顺序
4. 不改中间件配置
5. 不改路由 prefix
6. 不自动遍历 container
7. 不引入 ResourceManager
8. 不处理 Mongo close
```

这样属于**最小重构范围内的解耦**，不会改变项目原始入口风格。
