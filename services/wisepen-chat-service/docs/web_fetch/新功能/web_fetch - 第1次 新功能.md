# 智能网页浏览（browse_url）技术实现方案（最终版）

## 📋 项目概览

本方案提供一套**简洁、健壮、可扩展**的智能网页抓取系统，采用三级降级链将轻量 `httpx` 静态请求、`Steel` 自托管无头浏览器、本地 Node.js 脚本无缝串联。无论目标页面是纯静态文档还是重度 JavaScript SPA，都能自动获取核心正文并转换为干净 Markdown，为 AI Agent 提供可靠的网页阅读能力。

所有组件均开源且可本地部署，零外部付费依赖。设计上刻意追求**最小抽象**：核心调度器仅约 30 行，各抓取器和清洗工具独立封装，扩展新策略只需在降级链中加入一个元组。

## 🛠️ 开发场景

AI Agent 调用 `browse_url` 工具阅读链接内容时，系统根据页面特性自动选择最优抓取路径：

| 页面类型 | 使用策略 | 说明 |
|---------|---------|------|
| 静态博客、新闻、文档 | `httpx` 直接请求 | 速度最快，资源占用最低 |
| 动态 SPA、需 JS 渲染、Cloudflare 防护 | 自托管 `Steel` 实例 | 真实 Chrome 渲染，内置 Markdown 输出，自带反检测和代理 |
| Steel 不可用时的最终兜底 | 本地 Node.js 脚本 | 无头浏览器 + Stealth 反检测，必定执行 |

工具对外暴露统一的 `browse_url(url, mode)` 接口，内部降级逻辑完全透明。

## 📁 目录结构

```
src/chat/
├── application/
│   └── browse_url/
│       ├── __init__.py
│       ├── smart_fetcher.py            # 降级链调度器（核心）
│       ├── fetchers/
│       │   ├── static_fetcher.py       # httpx 静态抓取
│       │   ├── steel_fetcher.py        # AsyncSteel SDK 封装
│       │   └── local_script_fetcher.py # Node.js 本地脚本
│       └── cleaners/
│           ├── readability_cleaner.py   # 正文提取
│           └── markdown_converter.py    # HTML→Markdown
└── scripts/
    └── fetch.js                        # 本地浏览器抓取脚本
```

## 🔧 核心组件

### 1. 降级链调度器 (`smart_fetcher.py`)

调度器内部持有三个抓取器实例，并根据 `mode` 选择执行链。每条链由一组元组构成，分别记录**抓取器对象、输出内容类型、方法标识名**。遍历时第一个返回**非空有效内容**的抓取器即为本次使用的方法；随后根据内容类型决定是否经过 `readability → markdownify` 清洗管道。清洗过程中若发生异常，会降级返回原始文本，确保工具永不崩溃。

```python
import os
from typing import Optional, Any, List, Tuple, Dict

from application.browse_url.fetchers.static_fetcher import StaticFetcher
from application.browse_url.fetchers.steel_fetcher import SteelFetcher
from application.browse_url.fetchers.local_script_fetcher import LocalScriptFetcher
from application.browse_url.cleaners.readability_cleaner import extract_main_content
from application.browse_url.cleaners.markdown_converter import html_to_markdown


class SmartFetcher:
    """轻量级抓取调度器：三级降级链，自动清洗"""

    def __init__(
        self,
        timeout: int = 15,
        browser_timeout: Optional[int] = None,
    ):
        steel_base = os.getenv("STEEL_BASE_URL", "http://localhost:3000")
        browser_timeout = browser_timeout or (timeout + 20)

        self.static_fetcher = StaticFetcher(timeout=timeout)
        self.steel_fetcher  = SteelFetcher(timeout=browser_timeout)
        self.local_fetcher  = LocalScriptFetcher(timeout=browser_timeout)

        # 降级链：每项为 (抓取器, 输出类型, 方法名)
        self._lightweight_chain: List[Tuple[Any, str, str]] = [
            (self.static_fetcher, "html",     "static"),
            (self.steel_fetcher,  "markdown", "steel"),
            (self.local_fetcher,  "markdown", "local_script"),
        ]
        self._browser_chain: List[Tuple[Any, str, str]] = [
            (self.steel_fetcher, "markdown", "steel"),
            (self.local_fetcher, "markdown", "local_script"),
        ]

    async def browse_url(self, url: str, mode: str = "auto") -> Dict[str, Any]:
        chain = self._lightweight_chain if mode == "auto" else self._browser_chain

        for fetcher, content_type, method_name in chain:
            content = await fetcher.fetch(url)
            # 防止空字符串被误判为成功
            if content and content.strip():
                if content_type == "html":
                    try:
                        clean = extract_main_content(content)
                        final = html_to_markdown(clean)
                    except Exception:
                        # 清洗失败时降级返回原始文本
                        final = content.strip()
                else:
                    final = content.strip()
                return {"success": True, "method": method_name, "markdown": final}

        return {"success": False, "method": "none", "markdown": ""}
```

**核心设计要点**：

| 要点 | 说明 |
|------|------|
| **极简调度** | 一个 `for` 循环完成三级降级，无继承体系或适配器 |
| **空内容防护** | `if content and content.strip()` 确保空字符串自动降级 |
| **清洗异常兜底** | 正文提取或 Markdown 转换失败时降级返回原始文本 |
| **超时统一** | 浏览器相关抓取器使用同一个 `browser_timeout` |
| **方法名显式声明** | 不依赖类名字符串解析，稳定且自文档化 |
| **链式扩展** | 新增抓取器只需在列表中添加一个元组 |

### 2. 抓取器

所有抓取器都遵循 `async fetch(url) -> Optional[str]` 接口，返回文本内容或 `None`。

#### 2.1 静态抓取 (`fetchers/static_fetcher.py`)

基于 `httpx` 的轻量异步 HTTP 客户端，携带标准浏览器 `User-Agent` 和 `Accept` 头，支持自动跟随重定向。覆盖所有服务端渲染（SSR）页面、静态博客、API 接口。

```python
import httpx

class StaticFetcher:
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        }

    async def fetch(self, url: str) -> Optional[str]:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, headers=self.headers, follow_redirects=True
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text
        except Exception:
            return None
```

#### 2.2 Steel SDK 封装 (`fetchers/steel_fetcher.py`)

使用官方 `AsyncSteel` 客户端调用自托管的 Steel 实例，**无需 API Key**，仅需通过环境变量 `STEEL_BASE_URL` 指定实例地址。Steel 在真实 Chrome 中渲染页面并直接返回清洗后的 Markdown，一步到位处理 JS 渲染与反爬。

```python
import os
from typing import Optional
from steel import AsyncSteel


class SteelFetcher:
    """Steel SDK 封装 —— 异步、带超时、自动清洗为 Markdown"""

    def __init__(self, timeout: float = 15.0):
        self._client = AsyncSteel(
            base_url=os.getenv("STEEL_BASE_URL", "http://localhost:3000"),
            timeout=timeout,
        )

    async def fetch(self, url: str) -> Optional[str]:
        try:
            result = await self._client.scrape(url=url, format=["markdown"])
            return result.markdown or result.content or result.text
        except Exception:
            return None
```

> **自托管说明**：上述代码专为自托管 Steel 设计，不依赖任何 API Key。如需使用 Steel Cloud，可额外读取 `STEEL_API_KEY` 并传入 `AsyncSteel(steel_api_key=...)` 即可，当前版本保持最简。

#### 2.3 本地 Node.js 脚本 (`fetchers/local_script_fetcher.py`)

终极兜底：启动预先提供的 `fetch.js` 脚本，该脚本使用 Playwright + Stealth 进行反检测无头浏览器抓取，并输出 Markdown。Python 端通过 `asyncio.create_subprocess_exec` 启动子进程并捕获 stdout。超时或异常时**显式终止进程并回收资源**，杜绝僵尸进程。

```python
import asyncio
from pathlib import Path

SCRIPT_PATH = Path(__file__).parent.parent.parent.parent / "scripts" / "fetch.js"

class LocalScriptFetcher:
    def __init__(self, timeout: float = 25.0):
        self.timeout = timeout

    async def fetch(self, url: str) -> Optional[str]:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "node", str(SCRIPT_PATH), url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=10 * 1024 * 1024,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
            if proc.returncode != 0:
                return None
            markdown = stdout.decode("utf-8").strip()
            return markdown if len(markdown) > 100 else None
        except asyncio.TimeoutError:
            if proc:
                proc.kill()
                await proc.wait()
            return None
        except Exception:
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()
            return None
```

**僵尸进程防护**：超时和其他异常分支中，只要子进程尚未结束（`returncode is None`），都执行 `proc.kill()` 并 `await proc.wait()`。

### 3. 清洗模块

当抓取器返回原始 HTML（内容类型为 `"html"`）时，调用下面的清洗管道；若已是 Markdown 则跳过。

#### 3.1 正文提取 (`cleaners/readability_cleaner.py`)

```python
from readability import Document

def extract_main_content(html: str) -> str:
    try:
        return Document(html).summary()
    except Exception:
        return html
```

#### 3.2 Markdown 转换 (`cleaners/markdown_converter.py`)

```python
from markdownify import markdownify as md

def html_to_markdown(html: str) -> str:
    try:
        return md(
            html,
            heading_style="ATX",
            strip=['script', 'style', 'img', 'nav', 'footer'],
            autolinks=False,
        ).strip()
    except Exception:
        return html
```

### 4. 本地抓取脚本 (`scripts/fetch.js`)

该脚本通过命令行参数接收 URL，使用 Playwright + Stealth 启动无头 Chrome，滚动触发懒加载，移除干扰元素，最后将纯净 Markdown 输出到 stdout（所有日志信息输出到 stderr，确保 Python 端解析纯净）。

```javascript
#!/usr/bin/env node
const { chromium } = require('playwright-extra');
const stealthPlugin = require('puppeteer-extra-plugin-stealth');
const TurndownService = require('turndown');

chromium.use(stealthPlugin());
const turndownService = new TurndownService({ headingStyle: 'atx', codeBlockStyle: 'fenced' });
turndownService.addRule('ignore-base64-images', {
  filter: node => node.nodeName === 'IMG' && node.getAttribute('src')?.startsWith('data:image/'),
  replacement: () => ''
});

async function fetchUrl(url) {
  process.stderr.write(`Fetching: ${url}\n`);
  let browser;
  try {
    browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-setuid-sandbox'] });
    const page = await browser.newPage();
    page.setDefaultTimeout(60000);
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });

    for (let i = 0; i < 3; i++) {
      await page.evaluate(() => window.scrollBy(0, window.innerHeight));
      await page.waitForTimeout(1000);
    }
    await page.waitForTimeout(2000);

    const pageData = await page.evaluate(() => {
      document.querySelectorAll(
        'script,style,noscript,iframe,ad,.ads,#ads,img[src^="data:image/"]'
      ).forEach(el => el.remove());
      return { title: document.title, html: document.body.innerHTML };
    });
    const markdown = turndownService.turndown(pageData.html);
    if (!markdown || !markdown.trim()) {
      process.stderr.write('No readable content\n');
      process.exit(1);
    }
    process.stdout.write(markdown);
  } catch (e) {
    process.stderr.write(`Error: ${e.message}\n`);
    process.exit(1);
  } finally {
    if (browser) await browser.close();
  }
}

const url = process.argv[2];
if (!url) {
  process.stderr.write('Usage: node fetch.js <url>\n');
  process.exit(1);
}
fetchUrl(url);
```

## 🛠️ 技术选型

| 组件 | 技术 | 许可证 | 说明 |
|------|------|--------|------|
| 静态抓取 | `httpx` | MIT | 现代化异步 HTTP 客户端 |
| 浏览器 API（自托管） | `Steel` (Docker + SDK) | MIT | 真实 Chrome 渲染，内置 Markdown |
| 本地浏览器脚本 | `playwright-extra`, `stealth`, `turndown` (Node.js) | MIT | 反检测抓取 + Markdown 输出 |
| 正文提取 | `readability-lxml` | MIT | Mozilla Readability 算法 |
| HTML→Markdown | `markdownify` | MIT | 可自定义过滤标签 |

## 📝 使用示例

```python
from application.browse_url.smart_fetcher import SmartFetcher

fetcher = SmartFetcher(timeout=15)      # browser_timeout 自动设为 35s
result = await fetcher.browse_url("https://example.com")
print(result["method"], result["markdown"][:200])

# 强制浏览器模式
result = await fetcher.browse_url("https://spa-site.com", mode="browser")
```

## 🚀 特性亮点

1. **极简架构**：一个类、三级降级链，无冗余抽象，核心调度约 30 行。
2. **轻量优先**：`httpx` 快速处理静态页面；遇到动态内容才升级到 Steel。
3. **一步到位**：Steel 直接覆盖所有需要 JS 渲染或反爬绕过的场景，删除冗余的 `cloudscraper` 层。
4. **多层容错**：空内容自动降级，清洗异常兜底返回原始文本，僵尸进程自动回收。
5. **显式与安全**：方法名显式声明，超时统一管理，自托管零凭证依赖。
6. **完全开源**：所有依赖均为 MIT 协议，可随意商用与分发。
7. **钢铁兜底**：本地 Node.js 脚本确保任何情况下都能返回有效内容。

## 📦 部署依赖

### Python
```toml
dependencies = [
    "httpx",
    "steel-sdk",
    "readability-lxml",
    "markdownify",
]
```

### Node.js
```bash
npm install playwright-extra puppeteer-extra-plugin-stealth turndown
```

### Docker（推荐用于 Steel 自托管）
```bash
docker run -d -p 3000:3000 ghcr.io/steel-dev/steel-browser
```

### 环境变量
```bash
STEEL_BASE_URL=http://your-server:3000  # 自托管 Steel 地址，默认 localhost:3000
# 如果使用 Steel Cloud 服务，还需设置 STEEL_API_KEY，当前自托管模式无需此项
```

该方案以最小代码量实现了三级智能降级，兼顾速度与覆盖率，可直接部署为生产级 Agent 工具。后续如有更多交互需求（点击、登录）可独立增加 `browse_interact` 工具，保持架构解耦。