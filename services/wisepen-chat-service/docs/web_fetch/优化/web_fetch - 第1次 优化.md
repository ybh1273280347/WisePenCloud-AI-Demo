````markdown
# `local_web_fetcher.js` 最终优化文档

## 1. 优化目标

该脚本用于作为 Python 子进程调用，负责：

1. 打开指定 URL；
2. 等待页面完成基础渲染；
3. 执行少量滚动以触发懒加载；
4. 清理无关 DOM；
5. 将页面 HTML 转换为 Markdown；
6. 通过 `stdout` 返回 Markdown；
7. 通过 `stderr` 返回日志和错误信息。

本版本不再保留历史行为包袱，优化目标调整为：

```text
性能优先
稳定性优先
子进程协议清晰
stdout 只输出 Markdown
stderr 输出日志和错误
失败时返回非 0 exit code
不引入新依赖
````

---

## 2. 最终优化方向

相比原始脚本，最终版本接受以下显著优化：

| 优化项                      | 是否采用 | 目的                |
| ------------------------ | ---: | ----------------- |
| 拦截图片、媒体、字体、CSS           |   采用 | 显著降低网络和渲染开销       |
| 动态滚动                     |   采用 | 提高懒加载内容召回率        |
| `networkidle` 短等待        |   采用 | 尽量等待异步内容渲染        |
| 正文容器优先提取                 |   采用 | 降低导航、页脚、广告噪声      |
| 失败时不在内部提前 `process.exit` |   采用 | 保证资源释放            |
| stdout 写入完成后再退出          |   采用 | 避免 Python 管道读取不完整 |
| DOM 清理增强                 |   采用 | 减少无关内容进入 Markdown |
| Markdown 后处理             |   采用 | 减少多余空行和空白         |
| 输出标题                     |  不采用 | 避免污染 stdout 协议    |
| JSON 输出                  |  不采用 | 保持 Python 侧处理简单   |

---

## 3. 子进程协议

该脚本应遵循固定协议：

| 通道            | 内容           |
| ------------- | ------------ |
| `stdout`      | 最终 Markdown  |
| `stderr`      | 日志、错误、截图诊断片段 |
| exit code `0` | 成功           |
| exit code `1` | 失败           |

Python 调用方可以稳定使用：

```python
import subprocess

result = subprocess.run(
    ["node", "local_web_fetcher.js", url],
    capture_output=True,
    text=True,
    timeout=90,
)

if result.returncode == 0:
    markdown = result.stdout
else:
    error_log = result.stderr
```

---

## 4. 最终完整脚本

```js
const { chromium } = require('rebrowser-playwright');
const TurndownService = require('turndown');

// ---------------------------------------------------------------------------
// 配置常量
// ---------------------------------------------------------------------------
const NAVIGATION_TIMEOUT_MS = 60000;
const NETWORK_IDLE_TIMEOUT_MS = 8000;

const MAX_SCROLL_STEPS = 8;
const SCROLL_DELAY_MS = 700;
const POST_SCROLL_IDLE_MS = 1200;

const MIN_CONTENT_TEXT_LENGTH = 200;

const BLOCKED_RESOURCE_TYPES = new Set([
  'image',
  'media',
  'font',
  'stylesheet',
]);

const BROWSER_ARGS = [
  '--proxy-server=direct://',
  '--disable-blink-features=AutomationControlled',
  '--no-sandbox',
  '--disable-dev-shm-usage',
  '--disable-infobars',
  '--disable-background-networking',
  '--disable-component-extensions-with-background-pages',
  '--disable-default-apps',
  '--disable-extensions-http-throttling',
  '--disable-sync',
  '--disable-translate',
  '--metrics-recording-only',
  '--mute-audio',
  '--no-first-run',
  '--safebrowsing-disable-auto-update',
  '--lang=zh-CN',
];

// ---------------------------------------------------------------------------
// Markdown 转换器
// ---------------------------------------------------------------------------
const turndownService = new TurndownService({
  headingStyle: 'atx',
  codeBlockStyle: 'fenced',
  bulletListMarker: '-',
});

turndownService.addRule('ignore-base64-images', {
  filter: node =>
    node.nodeName === 'IMG' &&
    node.getAttribute('src')?.startsWith('data:image/'),
  replacement: () => '',
});

// ---------------------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------------------
function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function normalizeMarkdown(markdown) {
  return markdown
    .replace(/\r\n/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function writeStdout(content) {
  return new Promise((resolve, reject) => {
    process.stdout.write(content, error => {
      if (error) reject(error);
      else resolve();
    });
  });
}

async function autoScroll(page) {
  let previousHeight = 0;
  let stableCount = 0;

  for (let step = 0; step < MAX_SCROLL_STEPS; step++) {
    const state = await page.evaluate(() => {
      const documentElement = document.documentElement;
      const body = document.body;

      const scrollHeight = Math.max(
        documentElement?.scrollHeight || 0,
        body?.scrollHeight || 0
      );

      const viewportHeight = window.innerHeight || 768;
      const currentY = window.scrollY || window.pageYOffset || 0;

      const nearBottom =
        currentY + viewportHeight >= scrollHeight - 16;

      if (!nearBottom) {
        const delta = Math.floor(viewportHeight * (0.85 + Math.random() * 0.25));
        window.scrollBy({
          top: delta,
          behavior: 'auto',
        });
      }

      return {
        scrollHeight,
        currentY,
        nearBottom,
      };
    });

    if (state.scrollHeight === previousHeight) {
      stableCount += 1;
    } else {
      stableCount = 0;
      previousHeight = state.scrollHeight;
    }

    if (state.nearBottom && stableCount >= 1) {
      break;
    }

    await sleep(SCROLL_DELAY_MS + Math.floor(Math.random() * 300));
  }

  await sleep(POST_SCROLL_IDLE_MS);
}

async function extractPageData(page) {
  return page.evaluate((minTextLength) => {
    const removeSelectors = [
      'script',
      'style',
      'noscript',
      'iframe',
      'template',
      'canvas',
      'svg',
      'ad',
      '.ad',
      '.ads',
      '#ad',
      '#ads',
      '[class*="advert"]',
      '[id*="advert"]',
      '[class*="banner"]',
      '[id*="banner"]',
      'img[src^="data:image/"]',
    ];

    document
      .querySelectorAll(removeSelectors.join(','))
      .forEach(element => element.remove());

    const contentSelectors = [
      'article',
      'main',
      '[role="main"]',
      '.article',
      '.post',
      '.entry-content',
      '.post-content',
      '.article-content',
      '.content',
      '#content',
      '#main',
    ];

    let bestElement = null;
    let bestTextLength = 0;

    for (const selector of contentSelectors) {
      const elements = Array.from(document.querySelectorAll(selector));

      for (const element of elements) {
        const textLength = element.innerText
          ? element.innerText.trim().length
          : 0;

        if (textLength > bestTextLength) {
          bestElement = element;
          bestTextLength = textLength;
        }
      }
    }

    const body = document.body;
    const bodyTextLength = body?.innerText?.trim().length || 0;

    const selectedElement =
      bestElement && bestTextLength >= minTextLength
        ? bestElement
        : body;

    return {
      title: document.title || '',
      html: selectedElement ? selectedElement.innerHTML : '',
      textLength: selectedElement?.innerText?.trim().length || 0,
      usedBodyFallback: selectedElement === body,
      bodyTextLength,
      selectedTextLength: selectedElement?.innerText?.trim().length || 0,
    };
  }, MIN_CONTENT_TEXT_LENGTH);
}

// ---------------------------------------------------------------------------
// 主抓取逻辑
// ---------------------------------------------------------------------------
async function fetchUrl(url) {
  process.stderr.write(`Fetching: ${url}\n`);

  let browser;
  let context;

  try {
    browser = await chromium.launch({
      headless: true,
      args: BROWSER_ARGS,
    });

    context = await browser.newContext({
      locale: 'zh-CN',
      timezoneId: 'Asia/Shanghai',
      viewport: {
        width: 1366 + Math.floor(Math.random() * 100),
        height: 768 + Math.floor(Math.random() * 100),
      },
    });

    await context.route('**/*', route => {
      const resourceType = route.request().resourceType();

      if (BLOCKED_RESOURCE_TYPES.has(resourceType)) {
        return route.abort();
      }

      return route.continue();
    });

    await context.addInitScript(() => {
      try {
        Object.defineProperty(navigator, 'webdriver', {
          get: () => undefined,
          configurable: true,
        });
      } catch (_) {
        // best-effort
      }

      try {
        const permissions = window.navigator.permissions;
        const originalQuery = permissions?.query;

        if (originalQuery) {
          permissions.query = function query(parameters) {
            return parameters?.name === 'notifications'
              ? Promise.resolve({ state: Notification.permission })
              : originalQuery.call(this, parameters);
          };
        }
      } catch (_) {
        // best-effort
      }

      try {
        if (!window.chrome) {
          window.chrome = {
            runtime: {},
            loadTimes() {},
            csi() {},
            app: {},
          };
        }
      } catch (_) {
        // best-effort
      }

      try {
        delete window.__pwInitScripts;
        delete window.__playwright__binding__;
      } catch (_) {
        // best-effort
      }
    });

    const page = await context.newPage();

    page.setDefaultTimeout(NAVIGATION_TIMEOUT_MS);
    page.setDefaultNavigationTimeout(NAVIGATION_TIMEOUT_MS);

    await page.goto(url, {
      waitUntil: 'domcontentloaded',
      timeout: NAVIGATION_TIMEOUT_MS,
    });

    await page
      .waitForLoadState('networkidle', {
        timeout: NETWORK_IDLE_TIMEOUT_MS,
      })
      .catch(() => {
        // 现代页面常有长连接、埋点、轮询。
        // networkidle 超时不代表失败。
      });

    await autoScroll(page);

    const pageData = await extractPageData(page);

    process.stderr.write(
      `Extracted text length: ${pageData.selectedTextLength}, fallbackToBody: ${pageData.usedBodyFallback}\n`
    );

    const markdown = normalizeMarkdown(
      turndownService.turndown(pageData.html)
    );

    if (!markdown) {
      throw new Error('No readable content');
    }

    await writeStdout(markdown);
  } catch (error) {
    process.stderr.write(`Error: ${error.message}\n`);

    try {
      const errorPage = context?.pages?.()[0];

      if (errorPage) {
        const screenshot = await errorPage.screenshot({
          type: 'jpeg',
          quality: 30,
          fullPage: false,
        });

        process.stderr.write(
          `[screenshot] ${screenshot.toString('base64').slice(0, 200)}...\n`
        );
      }
    } catch (_) {
      // best-effort
    }

    throw error;
  } finally {
    if (context) {
      await context.close().catch(() => {});
    }

    if (browser) {
      await browser.close().catch(() => {});
    }
  }
}

// ---------------------------------------------------------------------------
// CLI 入口
// ---------------------------------------------------------------------------
async function main() {
  const url = process.argv[2];

  if (!url) {
    process.stderr.write('Usage: node local_web_fetcher.js <url>\n');
    process.exitCode = 1;
    return;
  }

  try {
    await fetchUrl(url);
  } catch (_) {
    process.exitCode = 1;
  }
}

main();
```

---

## 5. 关键优化说明

### 5.1 资源拦截

最终版本默认拦截：

```js
const BLOCKED_RESOURCE_TYPES = new Set([
  'image',
  'media',
  'font',
  'stylesheet',
]);
```

这通常是性能提升最大的部分。

多数网页中，图片、字体、视频、CSS 占据大量网络体积。脚本最终只需要 DOM 文本，因此默认不加载这些资源。

收益：

```text
减少网络请求
减少页面加载时间
减少内存占用
降低 Python 批量调用时的总耗时
```

代价：

```text
少数依赖 CSS 或图片加载事件触发内容渲染的页面可能受到影响
```

在性能优先场景下，该优化值得采用。

---

### 5.2 动态滚动

原始版本固定滚动 3 次：

```js
const SCROLL_STEPS = 3;
```

最终版本改为动态滚动：

```js
const MAX_SCROLL_STEPS = 8;
```

滚动逻辑会根据页面高度变化和是否接近底部决定是否提前停止。

收益：

```text
短页面不会浪费太多时间
长页面可以触发更多懒加载内容
无限流页面不会无限滚动，因为有 MAX_SCROLL_STEPS 上限
```

---

### 5.3 短暂等待 `networkidle`

最终版本增加：

```js
await page
  .waitForLoadState('networkidle', {
    timeout: NETWORK_IDLE_TIMEOUT_MS,
  })
  .catch(() => {});
```

该逻辑不会强制要求页面完全空闲。

收益：

```text
给异步渲染内容一点时间
提高 SPA、新闻站、文档站的正文召回率
```

同时通过 `catch` 忽略超时，避免因为长连接、埋点、轮询导致脚本失败。

---

### 5.4 正文容器优先提取

最终版本优先尝试提取：

```js
article
main
[role="main"]
.article
.post
.entry-content
.post-content
.article-content
.content
#content
#main
```

选择文本长度最大的候选容器。

如果候选正文太短，则回退到 `document.body`。

核心逻辑：

```js
const selectedElement =
  bestElement && bestTextLength >= minTextLength
    ? bestElement
    : body;
```

这样兼顾了：

```text
正文优先
失败回退
减少导航、页脚、广告、侧栏噪声
```

---

### 5.5 DOM 清理增强

最终版本清理：

```js
script
style
noscript
iframe
template
canvas
svg
ad
.ad
.ads
#ad
#ads
[class*="advert"]
[id*="advert"]
[class*="banner"]
[id*="banner"]
img[src^="data:image/"]
```

目的：

```text
减少无意义内容
减少 Markdown 噪声
避免 base64 图片撑爆输出
降低 turndown 转换成本
```

---

### 5.6 stdout 写入可靠性

最终版本使用：

```js
function writeStdout(content) {
  return new Promise((resolve, reject) => {
    process.stdout.write(content, error => {
      if (error) reject(error);
      else resolve();
    });
  });
}
```

这样可以避免 Markdown 内容较大时，Node 进程退出过快导致 Python 侧读取不完整。

---

### 5.7 退出码处理

最终版本不在 `fetchUrl()` 内部直接 `process.exit(1)`。

失败时：

```js
throw error;
```

然后在入口函数中：

```js
process.exitCode = 1;
```

这样可以确保：

```text
finally 有机会关闭 context 和 browser
Python 仍然能拿到非 0 exit code
错误信息仍然写入 stderr
```

---

## 6. Python 调用建议

推荐 Python 侧使用：

```python
import subprocess


def fetch_url_as_markdown(url: str, timeout: int = 90) -> str:
    result = subprocess.run(
        ["node", "local_web_fetcher.js", url],
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"fetch failed: {url}\n"
            f"stderr:\n{result.stderr}"
        )

    markdown = result.stdout.strip()

    if not markdown:
        raise RuntimeError(
            f"fetch returned empty markdown: {url}\n"
            f"stderr:\n{result.stderr}"
        )

    return markdown
```

批量调用时，Python 侧应使用超时控制，避免单个 URL 卡住整个任务：

```python
timeout=90
```

---

## 7. 最终优化收益

### 7.1 性能收益最大

主要来自：

```text
资源拦截
DOM 清理增强
正文容器提取
```

这些优化会明显减少：

```text
网络请求量
页面资源加载量
HTML 转 Markdown 的输入体积
Python 子进程等待时间
```

---

### 7.2 输出质量提升

主要来自：

```text
正文容器优先提取
广告和无关 DOM 清理
Markdown 空白规范化
```

相比直接转换整个 `body.innerHTML`，最终 Markdown 通常更干净。

---

### 7.3 稳定性提升

主要来自：

```text
stdout 写入等待
错误统一抛出
finally 中关闭 context 和 browser
networkidle 超时不致命
正文提取失败回退 body
```

---

## 8. 最终结论

最终版本采用性能优先策略，接受会显著提升吞吐量和输出质量的改动：

```text
默认拦截重资源
动态滚动
短暂 networkidle 等待
正文容器优先提取
增强 DOM 清理
Markdown 规范化
可靠 stdout 写入
统一错误处理
```

该版本更适合 Python 批量调用场景。

stdout 仍然只输出 Markdown，stderr 负责诊断信息，exit code 负责成功/失败判断。

整体取向是：

```text
更快
更干净
更适合作为 Python 子进程
不引入额外依赖
```

```
```
