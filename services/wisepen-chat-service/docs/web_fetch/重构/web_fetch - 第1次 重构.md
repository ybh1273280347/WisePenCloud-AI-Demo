# web_fetch - 第1次 重构

> 从 `browser_tools - 第1次 重构.md` 拆分而来。本文只保留 WebFetchTool、本地反爬脚本和对应验证结果。

## 二、WebFetchTool — 网页抓取工具（原 BrowseUrlTool）

### 2.1 更名清单

| 旧名 | 新名 | 层级 |
|------|------|------|
| `BrowseUrlTool` | `WebFetchTool` | 工具类 |
| `browse_url_tool.py` | `web_fetch_tool.py` | 文件名 |
| `browse_url/` | `web_fetch/` | 目录名 |
| `SmartFetcher` | `FetchCoordinator` | 调度器类 |
| `smart_fetcher.py` | `fetch_coordinator.py` | 文件名 |
| `cleaner.py` | `content_cleaner.py` | 文件名 |
| `mode: "auto" / "browser"` | `force_browser: bool` | 参数签名 |

### 2.2 目录结构

```
web_fetch/
├── __init__.py                  # 导出 ContentCleaner, FetchCoordinator
├── content_cleaner.py           # HTML 清洗管道（新封装）
├── fetch_coordinator.py         # 三级降级调度器（原 SmartFetcher）
└── fetcher/
    ├── __init__.py
    ├── static_fetcher.py        # 静态 HTTP 抓取
    ├── steel_fetcher.py         # Steel 云浏览器抓取
    └── local_fetcher.py         # 本地 Node.js 脚本抓取
```

### 2.3 ContentCleaner 封装

旧代码：两个散落函数 `extract_main_content()` + `convert_to_markdown()`，在调度器里用 try-except 兜底。

新代码：`ContentCleaner` 类，对外只暴露 `clean(html) -> Optional[str]`。

```python
class ContentCleaner:
    def clean(self, html: str) -> Optional[str]:
        """清洗后内容过短 → 返回 None 触发降级；异常 → 降级返回原文，永不抛异常"""
```

职责内聚：
- 清洗后长度判断从调度器移入 Cleaner
- 异常兜底从调度器移入 Cleaner
- 调度器只做 `self._cleaner.clean(content)` 一行调用

### 2.4 `mode` → `force_browser`

```python
# 旧代码
async def fetch(self, url: str, mode: Literal["auto", "browser"] = "auto") -> Optional[str]:
    chain = self._whole_chain if mode == "auto" else self._browser_chain

# 新代码
async def fetch(self, url: str, *, force_browser: bool = False) -> Optional[str]:
    chain = self._browser_chain if force_browser else self._lightweight_chain
```

改进点：
- 布尔值语义精确，杜绝 `mode="interact"` 等无效值
- `*` 强制关键字参数，调用时 `fetch(url, force_browser=True)` 可读性更优
- 参数 schema 从 `enum` 改为 `boolean`

### 2.5 超时显式化

```python
# 旧代码：三个 fetcher 各自硬编码 timeout
self._static_fetcher = StaticFetcher()           # 默认 10s
self._steel_fetcher = SteelFetcher(...)           # 默认 60s
self._local_script_fetcher = LocalScriptFetcher() # 默认 120s

# 新代码：构造函数统一配置
def __init__(self, static_timeout=15.0, browser_timeout=60.0, ...):
    self._lightweight_chain = [
        (StaticFetcher(timeout=static_timeout), "html"),
        (SteelFetcher(timeout=browser_timeout), "html"),
        (LocalScriptFetcher(timeout=browser_timeout), "markdown"),
    ]
```

### 2.6 模块级常量

**fetch_coordinator.py：**
```python
_DEFAULT_STATIC_TIMEOUT = 15.0       # 静态 HTTP 抓取默认超时（秒）
_DEFAULT_BROWSER_TIMEOUT = 60.0      # 浏览器抓取默认超时（秒）
```

**local_fetcher.py：**
```python
_MAX_PARENT_SEARCH_DEPTH = 10                    # 向上查找项目根目录的最大层级
_SUBPROCESS_BUFFER_LIMIT = 10 * 1024 * 1024      # 子进程 stdout/stderr 缓冲区上限（字节）
_ERROR_LOG_MAX_LENGTH = 500                       # 错误日志截断长度（字符）
```

### 2.7 注册更新

- `tools/__init__.py`：`BrowseUrlTool` → `WebFetchTool`
- `container.py`：`browse_url_tool` → `web_fetch_tool`

---

## 三、本地反爬脚本 — local_web_fetcher.js

### 3.1 改动总览

| 类别 | 旧脚本 | 新脚本 |
|------|--------|--------|
| 运行时 | 原生 `playwright` | `rebrowser-playwright`（内置反检测补丁） |
| 浏览器二进制 | Playwright 自带 `headless_shell` | `channel: 'chromium'` 启用 full Chromium new headless 模式 |
| 启动参数 | `--no-sandbox`, `--disable-setuid-sandbox` | 16 个参数，核心 `--disable-blink-features=AutomationControlled` + `--proxy-server=direct://` |
| 浏览器上下文 | `browser.newPage()` 直接创建 | `browser.newContext()` 隔离上下文，设置 locale / timezoneId / 随机视口 |
| JS 注入 | 无 | `page.addInitScript` 删除 `navigator.webdriver` / 修复 `window.chrome` / `permissions.query` / 清理 Playwright 全局变量 |
| 滚动行为 | 固定步长 `window.scrollBy(0, innerHeight)` | 随机步长 `innerHeight * (0.8 + random * 0.4)` + 随机延迟 |
| 异常现场 | 仅输出 error message | 额外尝试截图输出 base64 头到 stderr |
| 变量命名 | `e` / `i` | `error` / `step` |

### 3.2 运行时迁移：playwright → rebrowser-playwright

```javascript
// 旧代码
const { chromium } = require('playwright');

// 新代码
const { chromium } = require('rebrowser-playwright');
```

`rebrowser-playwright` 是 `playwright` 的补丁版本，内置以下反检测修复：
- 禁用 `Runtime.enable` CDP 命令（消除 CDP 信号泄漏）
- 修改默认 utility world 名称（消除 `__puppeteer_utility_world__` 指纹）
- 修复 sourceURL 泄漏

### 3.3 浏览器二进制：headless_shell → full Chromium

```javascript
// 旧代码：使用 headless_shell（精简版，硬编码 navigator.webdriver=true）
browser = await chromium.launch({ headless: true, args: BROWSER_ARGS });

// 新代码：使用 full Chromium new headless 模式
browser = await chromium.launch({ headless: true, channel: 'chromium', args: BROWSER_ARGS });
```

**为什么必须切换**：Playwright 默认 `headless: true` 使用 `chromium_headless_shell` 二进制，该精简版浏览器硬编码了 `navigator.webdriver = true`，且 `addInitScript` 的 JS 覆盖会被忽略或被高级检测识别。设置 `channel: 'chromium'` 后，Playwright 使用完整的 Chromium 二进制运行 new headless 模式，`--disable-blink-features=AutomationControlled` 和 `addInitScript` 覆盖才能正常生效。

### 3.4 启动参数详解

```javascript
const BROWSER_ARGS = [
  '--proxy-server=direct://',                        // 绕过系统代理，避免 net::ERR_CONNECTION_CLOSED
  '--disable-blink-features=AutomationControlled',   // 核心反检测：移除 navigator.webdriver=true 标志
  '--no-sandbox',                    // 容器环境必需
  '--disable-dev-shm-usage',         // 避免 /dev/shm 不足
  '--disable-infobars',              // 移除"Chrome 正受到自动化测试软件的控制"提示
  '--disable-background-networking', // 禁止后台网络活动（更新检查等）
  '--disable-component-extensions-with-background-pages',
  '--disable-default-apps',
  '--disable-extensions-http-throttling',
  '--disable-sync',
  '--disable-translate',
  '--metrics-recording-only',
  '--mute-audio',
  '--no-first-run',
  '--safebrowsing-disable-auto-update',
  '--lang=zh-CN',                    // 浏览器 locale 与上下文设置对齐
];
```

### 3.5 navigator.webdriver 隐藏

这是反检测的核心难点，经历了三次迭代才找到正确方案：

**方案一（失败）：`Object.defineProperty` 覆盖实例**

```javascript
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
```

失败原因：`webdriver` 定义在 `Navigator.prototype` 上，覆盖实例属性后原型链上的原始 getter 仍然存在，高级检测（如 bot.sannysoft.com 的 "WebDriver Advanced"）通过 `Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver')` 仍能发现原始 getter。

**方案二（失败）：`Object.defineProperty` 覆盖原型**

```javascript
Object.defineProperty(Navigator.prototype, 'webdriver', { get: () => undefined, configurable: true });
```

失败原因：覆盖后的 getter 返回 `() => undefined`，检测脚本通过 `Function.prototype.toString.call(getter)` 可以识别出这不是原生代码（`[native code]`），从而判定为篡改。

**方案三（成功）：删除原型属性 + 重新赋值原型链**

```javascript
const newProto = navigator.__proto__;
delete newProto.webdriver;
navigator.__proto__ = newProto;
```

成功原因：直接从原型对象上删除 `webdriver` 属性，然后重新赋值 `navigator.__proto__`，使属性完全消失而非被替换。检测脚本无论通过 `navigator.webdriver`、`'webdriver' in navigator` 还是 `Object.getOwnPropertyDescriptor` 都无法找到该属性。

### 3.6 page.addInitScript 注入清单

```javascript
await page.addInitScript(() => {
  // 1. 删除 navigator.webdriver（见 3.5 详解）
  const newProto = navigator.__proto__;
  delete newProto.webdriver;
  navigator.__proto__ = newProto;

  // 2. 修复 permissions.query 行为
  const originalQuery = window.navigator.permissions.query;
  window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters)
  );

  // 3. 确保 chrome 对象存在（部分检测脚本检查 window.chrome）
  if (!window.chrome) {
    window.chrome = {
      runtime: {},
      loadTimes: function () {},
      csi: function () {},
      app: {},
    };
  }

  // 4. 清理 Playwright 注入的全局变量
  if (window.__pwInitScripts !== undefined) delete window.__pwInitScripts;
  if (window.__playwright__binding__ !== undefined) delete window.__playwright__binding__;
});
```

关键点：使用 `page.addInitScript` 而非 `context.addInitScript`，确保脚本在页面最早的生命周期执行，在浏览器构建初始 DOM 之前就完成注入。

### 3.7 随机滚动行为

```javascript
for (let step = 0; step < SCROLL_STEPS; step++) {
  await page.evaluate(() => {
    const scrollY = Math.floor(window.innerHeight * (0.8 + Math.random() * 0.4));
    window.scrollBy({ top: scrollY, behavior: 'smooth' });
  });
  await page.waitForTimeout(SCROLL_DELAY_MS + Math.floor(Math.random() * 500));
}
```

- 随机步长：触发懒加载 + 更接近真人
- 随机延迟：降低"等间隔操作"特征

### 3.8 异常现场截图

```javascript
catch (error) {
  process.stderr.write(`Error: ${error.message}\n`);
  try {
    if (browser && browser.contexts().length > 0) {
      const errorPage = browser.contexts()[0].pages()[0];
      if (errorPage) {
        const screenshot = await errorPage.screenshot({ type: 'jpeg', quality: 30 });
        process.stderr.write(`[screenshot] ${screenshot.toString('base64').slice(0, 200)}...\n`);
      }
    }
  } catch (screenshotError) { /* best-effort */ }
  process.exit(1);
}
```

截图输出到 stderr（base64 头 200 字符），不污染 stdout 的 Markdown 输出。

### 3.9 模块级常量

```javascript
const NAVIGATION_TIMEOUT_MS = 60000;   // 页面导航超时（毫秒）
const SCROLL_STEPS = 3;                // 页面滚动次数
const SCROLL_DELAY_MS = 1000;          // 滚动步间延迟（毫秒）
const POST_SCROLL_IDLE_MS = 2000;      // 滚动完成后等待渲染的空闲时间（毫秒）
```

### 3.10 测试套件

`test_local_web_fetcher.js` 覆盖 6 个核心场景：

| 用例 | 覆盖点 |
|------|--------|
| `antiBot` | navigator.webdriver 隐藏（bot.sannysoft.com） |
| `staticPage` | 静态页面抓取 + Markdown 输出质量（无 `<html>/<body>` 残留） |
| `spa` | SPA 页面 JS 渲染能力 |
| `chineseContent` | 中文页面编码与内容完整性 |
| `noUrlArg` | 无 URL 参数时优雅退出（exit 1 + Usage 提示） |
| `deadDomain` | 无效域名异常处理（exit 1 + 无正文输出） |

---

## 四、Code Smell 合规审查

全部 12 条原则 + 7 条个人偏好审查通过：

| 原则 | 状态 | 备注 |
|------|------|------|
| 1. 拒绝过度抽象 | ✅ | 三个 Fetcher 无 ABC，元组链调度 |
| 2. if-elif ≤ 3 分支 | ✅ | `fetch()` 最多 3 个分支 |
| 3. 方法行数限制 | ✅ | 核心 ≤30，非核心 ≤20 |
| 4. 文件职责单一 | ✅ | Tool / Coordinator / Cleaner / Fetcher 各自独立 |
| 5. 消除魔法数字 | ✅ | 全部提取为模块级常量并附注释 |
| 6. 不要静态方法 | ✅ | 无 `@staticmethod` |
| 7. 拒绝 `_` 占位符 | ✅ | `for _ in range(...)` → `for level in range(...)` |
| 8. 数据模型显式 | ✅ | 不适用（无 Pydantic） |
| 9. 元组策略链 | ✅ | `_lightweight_chain` / `_browser_chain` |
| 10. 日志分级 | ✅ | `log_fail` / `log_error` / `log_ok` 各司其职 |
| 11. 排版硬约束 | ✅ | 无单行 if，间距规范 |
| 12.1 `get` 取值 | ✅ | `kwargs.get()` / `context.get()` |
| 12.2 typing 注解 | ✅ | 全部使用 `typing` 导入 |
| 12.3 嵌套 ≤ 2 层 | ✅ | `List[Tuple]` 只一层 |
| 12.4 Literal ≤ 3 | ✅ | 已改为 `bool` |
| 12.5 无 lambda | ✅ | |
| 12.6 参数展开清晰 | ✅ | `httpx.AsyncClient` 逐行展开 |
| 12.7 文档风格 | ✅ | 不适用 |

---

## 文件变更清单

### 新增文件

| 文件 | 说明 |
|------|------|
| 	ools/web_fetch_tool.py | WebFetchTool 工具类 |
| web_fetch/__init__.py | 包导出 |
| web_fetch/content_cleaner.py | ContentCleaner 类 |
| web_fetch/fetch_coordinator.py | FetchCoordinator 调度器 |
| web_fetch/fetcher/__init__.py | fetcher 包导出 |
| web_fetch/fetcher/static_fetcher.py | 静态 HTTP 抓取器 |
| web_fetch/fetcher/steel_fetcher.py | Steel 云浏览器抓取器 |
| web_fetch/fetcher/local_fetcher.py | 本地 Node.js 脚本抓取器 |
| scripts/test_local_web_fetcher.js | 反爬脚本测试套件 |

### 修改文件

| 文件 | 变更 |
|------|------|
| scripts/local_web_fetcher.js | 反爬增强 + rebrowser-playwright 迁移。 |
| 	ools/__init__.py | BrowseUrlTool → WebFetchTool |
| container.py | rowse_url_tool → web_fetch_tool |

### 删除文件

| 文件 | 说明 |
|------|------|
| 	ools/browse_url_tool.py | 已替换为 web_fetch_tool.py |
| rowse_url/ 整个目录 | 已替换为 web_fetch/ 目录 |

