## `web_fetch` 重构意见

***

### 一、更名

`browse_url` → `web_fetch`&#x20;

**1. 工具类名**
`SmartFetcher` → `WebFetchTool`，与 `BrowseInteractTool` 保持命名风格一致，也让项目里所有 Tool 类可以通过 `*Tool` 后缀一眼识别。

**2. 方法名**
`browse_url()` → `fetch()`，语义更直接：给 URL，拿内容。

**3. 文件名**
`smart_fetcher.py` → `web_fetch.py`，文件名即工具名。

**4. 目录名（可选，但建议）**
`browse_url/` → `web_fetch/`。改动涉及 import 路径，但长痛不如短痛，趁早统一。

***

### 二、清洗模块封装：`Cleaner` 类确有必要

当前清洗管道是散落的函数调用：

```python
clean = extract_main_content(content)
final = html_to_markdown(clean)
```

你提出的封装方向完全正确。理由有三：

**1. 降低调度器的认知负载**
`SmartFetcher`（未来的 `WebFetchTool`）的核心职责是“调度抓取”。清洗逻辑混在调度器里，让类承担了两个不相关的职责。封装后调度器只负责 `for fetcher in chain: try → return`，清洗完全委托给 Cleaner。

**2. 异常兜底逻辑集中管理**
现在清洗异常在调度器里用一个 `try-except` 兜底。如果后续增加更多清洗步骤（例如白名单过滤、特定网站规则），调度器的异常处理会膨胀。封装成 Cleaner 后，所有清洗相关的异常在一处处理，对外只暴露 `clean(html) -> str`，永不抛异常。

**3. 可测试性**
函数式调用无法 mock，Cleaner 作为类可以注入依赖、单独测试清洗管道的行为。

**建议实现：**

```python
class ContentCleaner:
    """HTML 正文提取 + Markdown 转换"""

    def clean(self, html: str) -> str:
        """清洗 HTML 并返回 Markdown。永不抛异常，降级时返回原始文本。"""
        try:
            main = extract_main_content(html)
            return html_to_markdown(main)
        except Exception:
            return html.strip()
```

调度器里的清洗调用变成一行：

```python
if content_type == "html":
    final = self.cleaner.clean(content)
else:
    final = content.strip()
```

***

### 三、调度器瘦身：几个可以考虑的改动

**1. 模式参数改为布尔值**

当前 `mode: str = "auto"` 只有两个合法值。用布尔值语义更精确，且避免未来有人传 `mode="interact"` 这种无意义的值：

```python
async def fetch(self, url: str, *, force_browser: bool = False) -> Dict[str, Any]:
    chain = self._browser_chain if force_browser else self._lightweight_chain
```

`*` 强制关键字参数，调用时 `fetch(url, force_browser=True)` 可读性优于 `fetch(url, "browser")`。

**2. 调度器本身不需要是类**

`WebFetchTool` 如果只是组合三个 Fetcher + 一个 Cleaner，且自身无状态，可以考虑改为函数 + 依赖注入。但如果后续需要统计成功率、实现请求缓存等，类仍然合适。现阶段保留类没问题，只是留一个思考口子。

**3. 超时逻辑的默认值继承**

当前代码：

```python
browser_timeout = browser_timeout or (timeout + 20)
```

这个逻辑隐含了“浏览器超时应该比静态超时长”的假设，但没有给出理由。可以在构造函数里显式处理，让两个超时各自独立配置，去掉 `+20` 的隐式依赖：

```python
def __init__(self, static_timeout: int = 15, browser_timeout: int = 35):
    self.static_fetcher = StaticFetcher(timeout=static_timeout)
    self.steel_fetcher = SteelFetcher(timeout=browser_timeout)
    self.local_fetcher = LocalScriptFetcher(timeout=browser_timeout)
```

***

### 四、小修小补

**1. 返回结构的方法名字段**

当前成功了返回 `"method": "static"`，这很好。但失败了返回 `"method": "none"`，`"none"` 是个魔法字符串。可以考虑失败时直接省略 `method` 字段，或者用 `None`。

**2. 三级降级链的元组可以用** **`dataclass`**

当前：

```python
(self.static_fetcher, "html", "static")
```

如果未来链对象需要更多元数据（优先级权重、适用域名白名单等），元组会膨胀。现阶段三个元素还能接受，但可以留一个重构入口：

```python
@dataclass
class FetchStrategy:
    fetcher: Any
    content_type: str
    name: str
```

不过这是我过度设计的倾向，你现在不改完全没问题。

***

### 五、优先级建议

| 优先级 | 改动                          | 工作量  |
| --- | --------------------------- | ---- |
| P0  | 更名（类名+方法名+文件名）              | 半小时  |
| P1  | 清洗模块封装为 Cleaner 类           | 一小时  |
| P2  | `mode` 参数改为 `force_browser` | 十五分钟 |
| P2  | 超时逻辑显式化                     | 十分钟  |
| P3  | 魔法字符串清理                     | 五分钟  |

改名和封装 Cleaner 做完，代码对外接口和内部职责边界就都清晰了。剩下的都是锦上添花。
