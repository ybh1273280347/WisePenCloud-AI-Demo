可以。基于你这个 `FetchCoordinator`，我建议把 **SteelFetcher + FetchCoordinator** 一起升级成“基础设施适配器 + 调度器”的模式：

* `SteelFetcher`：只负责调用 Steel、管理 SDK client 生命周期、处理 Steel 级别异常、返回 Markdown。
* `FetchCoordinator`：只负责编排抓取链路、判断内容长度、调用 `ContentProcessor`、统一降级。
* `ContentProcessor`：继续负责 HTML / 文档解析、正文清洗、反爬页过滤等内容质量判断。你现在的 `ContentProcessor` 已经承担了这层职责，例如它会根据输入是 `str | bytes` 分流处理，并对 HTML、PDF、DOCX、XLSX、PPTX 做解析与降级判断 。

官方 SDK 层面，Steel Python SDK 支持异步客户端 `AsyncSteel`，且请求/响应都有类型定义；`scrape` 的 `format` 参数支持 `"markdown"`，默认是 `html`，所以继续显式传 `format=["markdown"]` 是正确的。Steel SDK 还默认对连接错误、408、409、429、5xx 自动重试 2 次，并建议显式关闭底层 HTTP 资源，可用 `.close()` 或 context manager。([GitHub][1])

---

# 最佳实践版 SteelFetcher

这个版本适合被 `FetchCoordinator` 长期复用。

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import steel
from steel import AsyncSteel

from common.logger import log_fail, log_ok


@dataclass(frozen=True)
class SteelFetcherConfig:
    """Steel scrape 抓取配置"""

    base_url: str
    timeout: float = 60.0
    max_retries: int = 2

    # Steel scrape 参数
    use_proxy: bool = False
    delay_ms: float = 0.0
    region: Optional[dict[str, Any]] = None

    # 输出处理
    strip_output: bool = True


class SteelFetcher:
    """通过 Steel API 的 scrape 接口获取页面 Markdown 内容。

    职责边界：
    - 负责调用 Steel scrape；
    - 负责 SDK client 生命周期；
    - 负责 Steel 异常分类日志；
    - 不做内容质量判断，不做反爬关键词过滤；
    - 内容过滤交给 FetchCoordinator / ContentProcessor。
    """

    def __init__(self, config: SteelFetcherConfig):
        self._config = config
        self._client = AsyncSteel(
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )
        self._closed = False

        log_ok(
            "Steel 抓取器初始化",
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=config.max_retries,
            use_proxy=config.use_proxy,
            delay_ms=config.delay_ms,
        )

    async def fetch(self, url: str) -> Optional[str]:
        if self._closed:
            log_fail("Steel 浏览器抓取", "client 已关闭", url=url)
            return None

        try:
            kwargs: dict[str, Any] = {
                "url": url,
                "format": ["markdown"],
                "use_proxy": self._config.use_proxy,
            }

            if self._config.delay_ms > 0:
                kwargs["delay"] = self._config.delay_ms

            if self._config.region is not None:
                kwargs["region"] = self._config.region

            response = await self._client.scrape(**kwargs)

            markdown = response.content.markdown if response.content else None
            if markdown is None:
                log_fail("Steel 浏览器抓取", "响应中无 markdown 内容", url=url)
                return None

            if self._config.strip_output:
                markdown = markdown.strip()

            if not markdown:
                log_fail("Steel 浏览器抓取", "markdown 内容为空", url=url)
                return None

            return markdown

        except steel.RateLimitError as e:
            log_fail("Steel 浏览器抓取", f"触发限流: {e}", url=url)
            return None

        except steel.APITimeoutError as e:
            log_fail("Steel 浏览器抓取", f"请求超时: {e}", url=url)
            return None

        except steel.APIConnectionError as e:
            log_fail("Steel 浏览器抓取", f"连接失败: {e}", url=url)
            return None

        except steel.APIStatusError as e:
            log_fail(
                "Steel 浏览器抓取",
                f"HTTP {e.status_code}: {e.response}",
                url=url,
            )
            return None

        except steel.APIError as e:
            log_fail("Steel 浏览器抓取", f"Steel API 错误: {e}", url=url)
            return None

        except Exception as e:
            log_fail("Steel 浏览器抓取", e, url=url)
            return None

    async def close(self) -> None:
        if self._closed:
            return

        await self._client.close()
        self._closed = True

    async def __aenter__(self) -> "SteelFetcher":
        if self._closed:
            raise RuntimeError("SteelFetcher 已关闭，不能重新进入上下文")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
```

---

# 最佳实践版 FetchCoordinator

主要优化点：

1. 用 `Strategy` 明确每条链路的 fetcher 与内容类型；
2. 用 `Literal["raw", "markdown"]` 避免字符串拼错；
3. 增加 URL scheme 校验，只允许 `http/https`；
4. 增加可选并发限制，防止批量抓取时同时打爆 Steel / 本地浏览器；
5. 增加 `close()` 和 async context manager；
6. 统一处理 markdown 长度判断；
7. 避免把内容质量逻辑散落到 fetcher 内部；
8. 保持当前链路顺序：普通模式 `Static → Steel → LocalScript`，强制浏览器 `Steel → LocalScript`。

```python
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Literal, Optional, Protocol
from urllib.parse import urlparse

from chat.application.web_fetch.fetcher import StaticFetcher, LocalScriptFetcher
from chat.application.web_fetch.steel_fetcher import SteelFetcher, SteelFetcherConfig
from chat.application.web_fetch.content_processor import ContentProcessor
from common.logger import log_ok, log_fail


ContentKind = Literal["raw", "markdown"]


class Fetcher(Protocol):
    async def fetch(self, url: str):
        ...


@dataclass(frozen=True)
class FetchStrategy:
    fetcher: Fetcher
    content_kind: ContentKind
    name: str


@dataclass(frozen=True)
class FetchCoordinatorConfig:
    steel_base_url: str

    min_content_length: int = 400
    last_resort_min_length: int = 50

    static_timeout: float = 15.0
    browser_timeout: float = 60.0

    # Steel 参数
    steel_max_retries: int = 2
    steel_use_proxy: bool = False
    steel_delay_ms: float = 0.0

    # 并发保护。None 表示不限制。
    max_concurrency: Optional[int] = None

    # URL 安全策略
    allowed_schemes: tuple[str, ...] = ("http", "https")


class FetchCoordinator:
    """网页抓取调度器：按优先级依次尝试多种抓取策略，自动降级。

    抓取链路：
        普通模式    → StaticFetcher → SteelFetcher → LocalScriptFetcher
        强制浏览器  → SteelFetcher  → LocalScriptFetcher

    内容类型：
        raw      → 交给 ContentProcessor 处理
        markdown → 已是 Markdown，只做基础长度校验
    """

    def __init__(self, config: FetchCoordinatorConfig):
        if config.last_resort_min_length <= 0:
            raise ValueError("last_resort_min_length 必须大于 0")

        if config.min_content_length < config.last_resort_min_length:
            raise ValueError("min_content_length 不应小于 last_resort_min_length")

        self._config = config
        self._closed = False

        self._static_fetcher = StaticFetcher(timeout=config.static_timeout)

        self._steel_fetcher = SteelFetcher(
            SteelFetcherConfig(
                base_url=config.steel_base_url,
                timeout=config.browser_timeout,
                max_retries=config.steel_max_retries,
                use_proxy=config.steel_use_proxy,
                delay_ms=config.steel_delay_ms,
            )
        )

        self._local_script_fetcher = LocalScriptFetcher(timeout=config.browser_timeout)

        self._processor = ContentProcessor(
            min_content_length=config.min_content_length,
        )

        self._lightweight_chain: tuple[FetchStrategy, ...] = (
            FetchStrategy(self._static_fetcher, "raw", "StaticFetcher"),
            FetchStrategy(self._steel_fetcher, "markdown", "SteelFetcher"),
            FetchStrategy(self._local_script_fetcher, "markdown", "LocalScriptFetcher"),
        )

        self._browser_chain: tuple[FetchStrategy, ...] = (
            FetchStrategy(self._steel_fetcher, "markdown", "SteelFetcher"),
            FetchStrategy(self._local_script_fetcher, "markdown", "LocalScriptFetcher"),
        )

        self._semaphore = (
            asyncio.Semaphore(config.max_concurrency)
            if config.max_concurrency is not None and config.max_concurrency > 0
            else None
        )

        log_ok(
            "网页抓取调度器初始化",
            min_content_length=config.min_content_length,
            last_resort_min_length=config.last_resort_min_length,
            static_timeout=config.static_timeout,
            browser_timeout=config.browser_timeout,
            steel_use_proxy=config.steel_use_proxy,
            steel_delay_ms=config.steel_delay_ms,
            max_concurrency=config.max_concurrency,
        )

    async def fetch(self, url: str, *, force_browser: bool = False) -> Optional[str]:
        if self._closed:
            log_fail("网页抓取", "FetchCoordinator 已关闭", url=url)
            return None

        if not self._is_allowed_url(url):
            log_fail("网页抓取", "URL scheme 不被允许", url=url)
            return None

        if self._semaphore is None:
            return await self._fetch_inner(url, force_browser=force_browser)

        async with self._semaphore:
            return await self._fetch_inner(url, force_browser=force_browser)

    async def _fetch_inner(self, url: str, *, force_browser: bool) -> Optional[str]:
        chain = self._browser_chain if force_browser else self._lightweight_chain

        for index, strategy in enumerate(chain):
            is_last = index == len(chain) - 1
            min_length = (
                self._config.last_resort_min_length
                if is_last
                else self._config.min_content_length
            )

            result = await self._try_strategy(
                strategy=strategy,
                url=url,
                min_length=min_length,
            )

            if result is not None:
                log_ok(
                    "网页抓取",
                    url=url,
                    fetcher=strategy.name,
                    force_browser=force_browser,
                    length=len(result),
                )
                return result

        log_fail("网页抓取", "所有抓取器均失败", url=url, force_browser=force_browser)
        return None

    async def _try_strategy(
        self,
        *,
        strategy: FetchStrategy,
        url: str,
        min_length: int,
    ) -> Optional[str]:
        try:
            content = await strategy.fetcher.fetch(url)
        except Exception as e:
            log_fail("网页抓取", e, url=url, fetcher=strategy.name)
            return None

        if not content:
            log_fail("网页抓取", "抓取内容为空", url=url, fetcher=strategy.name)
            return None

        if strategy.content_kind == "markdown":
            return self._accept_markdown(
                content=str(content),
                url=url,
                fetcher_name=strategy.name,
                min_length=min_length,
            )

        processed = self._processor.process(content)
        if processed is None:
            log_fail("网页抓取", "内容处理失败，触发降级", url=url, fetcher=strategy.name)
            return None

        return self._accept_markdown(
            content=processed,
            url=url,
            fetcher_name=strategy.name,
            min_length=min_length,
        )

    def _accept_markdown(
        self,
        *,
        content: str,
        url: str,
        fetcher_name: str,
        min_length: int,
    ) -> Optional[str]:
        markdown = content.strip()

        if len(markdown) < min_length:
            log_fail(
                "网页抓取",
                f"内容过短({len(markdown)}字符)，阈值{min_length}，触发降级",
                url=url,
                fetcher=fetcher_name,
            )
            return None

        return markdown

    def _is_allowed_url(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in self._config.allowed_schemes and bool(parsed.netloc)

    async def close(self) -> None:
        if self._closed:
            return

        self._closed = True

        for fetcher in (
            self._static_fetcher,
            self._steel_fetcher,
            self._local_script_fetcher,
        ):
            close_method = getattr(fetcher, "close", None)
            if close_method is None:
                continue

            try:
                result = close_method()
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                log_fail("网页抓取调度器关闭", e, fetcher=fetcher.__class__.__name__)

    async def __aenter__(self) -> "FetchCoordinator":
        if self._closed:
            raise RuntimeError("FetchCoordinator 已关闭，不能重新进入上下文")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
```

---

# 推荐初始化方式

普通模式，保守成本：

```python
coordinator = FetchCoordinator(
    FetchCoordinatorConfig(
        steel_base_url=steel_base_url,
        min_content_length=400,
        last_resort_min_length=50,
        static_timeout=15.0,
        browser_timeout=60.0,
        steel_use_proxy=False,
        steel_delay_ms=0,
        max_concurrency=5,
    )
)
```

作为复杂网页兜底链路时，可以稍微增强 Steel：

```python
coordinator = FetchCoordinator(
    FetchCoordinatorConfig(
        steel_base_url=steel_base_url,
        min_content_length=400,
        last_resort_min_length=50,
        static_timeout=15.0,
        browser_timeout=90.0,
        steel_use_proxy=True,
        steel_delay_ms=1000,
        max_concurrency=3,
    )
)
```

使用 context manager：

```python
async with FetchCoordinator(
    FetchCoordinatorConfig(
        steel_base_url=steel_base_url,
        max_concurrency=5,
    )
) as coordinator:
    markdown = await coordinator.fetch(url)
```

---

# 这组优化里最关键的几个决策

## 1. 不把内容质量判断塞进 SteelFetcher

Steel 官方 `scrape` 返回 Markdown，但它不保证这段 Markdown 一定是“有效正文”。所以 SteelFetcher 只做：

```text
API 调用
异常分类
空内容判断
strip
```

真正的内容质量，例如：

```text
太短
疑似反爬页
HTML 清洗
文档解析
```

继续留给 `FetchCoordinator` 和 `ContentProcessor`。

---

## 2. FetchCoordinator 应该负责长度阈值

你原来的设计里已经有这个策略：

```python
min_length = self._last_resort_min_length if is_last else self._min_content_length
```

这很好，建议保留并强化。

原因是最后一个抓取器往往是最后机会，适当降低阈值可以提高召回率。
这个策略属于“调度策略”，不应该下沉到 fetcher。

---

## 3. SteelFetcher 必须支持 close

官方文档明确说底层 HTTP 连接默认会在 client 被垃圾回收时关闭，但也支持 `.close()` 或 context manager 手动关闭。对于服务型程序，显式关闭是更好的实践。([GitHub][1])

所以 `FetchCoordinator.close()` 应该向下关闭所有支持 `close()` 的 fetcher。

---

## 4. 不建议在 FetchCoordinator 里写复杂重试

Steel SDK 自己默认会对连接错误、408、409、429、5xx 自动重试 2 次。([GitHub][1])
如果 coordinator 再包一层重试，容易变成：

```text
Static 重试 N 次
Steel SDK 内部重试 2 次
Coordinator 外部再重试 N 次
```

这会让单个 URL 的耗时不可控。
更好的做法是：**每个 fetcher 尝试一次，失败就降级到下一条链路**。

---

## 5. `force_browser` 语义保持不变

当前语义是：

```text
force_browser=False → Static → Steel → LocalScript
force_browser=True  → Steel → LocalScript
```

我建议保持这个语义。
它非常清晰：`force_browser=True` 直接跳过静态请求。

---

# 是否应该把 LocalScript 放到 Steel 前面？

可以，但我不建议在这个版本里默认调换。

你当前链路是：

```text
StaticFetcher → SteelFetcher → LocalScriptFetcher
```

这个顺序隐含了一个策略：

```text
静态最快
Steel 云浏览器作为主浏览器兜底
本地脚本作为最后兜底
```

如果你的目标是“省 Steel 成本”，可以改成：

```text
StaticFetcher → LocalScriptFetcher → SteelFetcher
```

但这属于业务策略，不是代码最佳实践。
所以我建议把顺序保留在 coordinator 里，后续只改 chain 定义即可，不牵动 fetcher 实现。

---

# 最终结论

这两个组件一起优化时，最佳实践不是让每个类都变复杂，而是让职责边界更硬：

```text
SteelFetcher:
    Steel SDK 适配器
    负责 client 生命周期、scrape 调用、Steel 异常分类
    返回 Optional[str]

FetchCoordinator:
    抓取链路调度器
    负责 force_browser、降级顺序、长度阈值、统一日志、关闭下游 fetcher

ContentProcessor:
    内容处理器
    负责 HTML / 文档解析、正文清洗、反爬内容识别、最小长度过滤
```

这版改完后，你的架构会更干净：
**Steel SDK 怎么用、抓取链路怎么排、内容质量怎么判断** 三件事互不污染。

[1]: https://github.com/steel-dev/steel-python "GitHub - steel-dev/steel-python: The official Python library for the Steel API · GitHub"
