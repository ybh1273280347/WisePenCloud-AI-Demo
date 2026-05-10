你说得对。前一版把 **agent 理论风险** 当成了真实问题，导致方案明显过度设计了。

这里应该按你的实际情况处理：

```text
AI 没传错过参数 → 不需要宽松布尔解析
生命周期由外层管理/进程常驻 → 不需要 close
session_id 原语义够用 → 不需要安全校验函数
整体实战稳定 → 不要重构，不要新增抽象
```

下面是**重新收敛后的 Codex 执行文档**，只做必要优化。

---

# Codex 执行文档：最小必要优化 `WebFetchTool`

## 目标

在保持原有结构和语义基本不变的前提下，做少量必要优化：

```text
1. 保留原 session_id 逻辑
2. 保留原 force_browser 逻辑
3. 不新增 close
4. 不新增 context manager
5. 不引入复杂 helper
6. 不改变 FetchCoordinator 构造方式
7. 只补 URL 基础校验、结果 strip、截断长度控制
```

---

## 不要做的事

请不要实现以下内容：

```text
1. 不要写 _parse_bool
2. 不要写 _extract_session_id
3. 不要新增 session_id 正则校验
4. 不要新增 close()
5. 不要新增 __aenter__ / __aexit__
6. 不要引入 FetchCoordinatorConfig
7. 不要改 FetchCoordinator / SteelFetcher / ContentProcessor
8. 不要对 settings 使用 getattr 兜底
```

---

## 最终建议代码

```python
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from chat.core.config.app_settings import settings
from chat.domain.interfaces.tool import BaseTool
from chat.application.web_fetch.fetch_coordinator import FetchCoordinator
from common.logger import log_fail


_TRUNCATION_MARKER = "\n\n...(Content truncated due to length)"


def _is_valid_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


class WebFetchTool(BaseTool):
    """网页浏览抓取工具"""

    def __init__(self):
        self._fetcher = FetchCoordinator(settings.STEEL_BASE_URL)

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return (
            "Fetches and extracts textual content from a given URL, returning clean Markdown. "
            "Supports both web pages and direct document links (PDF, DOCX, XLSX, PPTX) — "
            "content type is auto-detected, no need to specify format.\n\n"
            "**Fetch strategy:** Three-stage automatic fallback: "
            "1) lightweight static HTTP request; 2) headless browser (Steel) for JS-heavy pages; "
            "3) local headless browser as final fallback. "
            "Document URLs are handled directly via static fetch — no browser stage needed.\n\n"
            "**When to use:** Call this tool when a user provides a URL (web page or document) "
            "and asks you to read, summarize, analyze, or answer questions about its content.\n\n"
            "**force_browser:** Set to true ONLY when: "
            "a) default mode returned incomplete content or a bot-check page; "
            "b) the target is a known dynamic, JavaScript-heavy website. "
            "Do NOT use force_browser for document URLs (PDF, DOCX, etc.) — it provides no benefit.\n\n"
            "**Note:** Returned content may be very long and is automatically truncated. "
            "Focus on extracting the information relevant to the user's request."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": (
                        "The URL to fetch. Accepts web pages (HTML) and direct document links "
                        "(PDF, DOCX, XLSX, PPTX). Must start with http:// or https://."
                    ),
                },
                "force_browser": {
                    "type": "boolean",
                    "description": (
                        "Force browser mode. Defaults to false. "
                        "false: tries a fast static fetch first, then falls back to a headless browser. "
                        "true: skips the static fetch and uses a headless browser immediately. Useful for dynamic, "
                        "JavaScript-heavy pages or when a static fetch has already failed."
                    ),
                    "default": False,
                },
            },
            "required": ["url"],
        }

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        """执行网页浏览并返回 Markdown 内容或错误消息"""
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        url: str = kwargs.get("url", "")
        force_browser: bool = kwargs.get("force_browser", False)

        if not url:
            return "[Tool Error] Missing required url parameter"

        url = url.strip()
        if not _is_valid_http_url(url):
            return "[Tool Error] Invalid url parameter. URL must start with http:// or https://."

        try:
            md_result = await self._fetcher.fetch(url, force_browser=force_browser)
        except Exception as e:
            log_fail("网页抓取工具", e, session_id=session_id, url=url, force_browser=force_browser)
            return "[Tool Error] Unexpected error while fetching web page content."

        if md_result is None:
            return "[Tool Result] Failed to fetch web page content (all fetch methods exhausted)"

        md_result = md_result.strip()
        if not md_result:
            return "[Tool Result] Failed to fetch web page content (empty content returned)"

        if len(md_result) > settings.TOOL_RESULT_MAX_CHARS:
            limit = settings.TOOL_RESULT_MAX_CHARS
            keep_len = max(0, limit - len(_TRUNCATION_MARKER))
            md_result = md_result[:keep_len].rstrip() + _TRUNCATION_MARKER

        return md_result
```

---

## 实际改动点

### 1. URL 增加基础校验

原来只判断：

```python
if not url:
```

优化后增加：

```python
url = url.strip()
if not _is_valid_http_url(url):
```

只允许：

```text
http://...
https://...
```

这是必要优化，因为工具描述里已经声明 URL 必须是 HTTP/HTTPS。

---

### 2. fetch 外层加兜底异常捕获

虽然 `FetchCoordinator` 内部通常会处理异常，但工具层作为 agent 边界，最好保证 `execute()` 始终返回字符串。

```python
try:
    md_result = await self._fetcher.fetch(url, force_browser=force_browser)
except Exception as e:
    ...
    return "[Tool Error] Unexpected error while fetching web page content."
```

这属于边界层必要防御，不改变正常路径。

---

### 3. 返回内容统一 `strip()`

原来直接：

```python
return md_result
```

优化后：

```python
md_result = md_result.strip()
```

避免返回首尾大量空白。

---

### 4. 空白内容单独处理

如果 fetcher 返回 `"   \n\n"`，原来会被当作成功。

现在返回：

```text
[Tool Result] Failed to fetch web page content (empty content returned)
```

---

### 5. 截断长度更准确

原来：

```python
md_result = md_result[:settings.TOOL_RESULT_MAX_CHARS] + "\n\n...(Content truncated due to length)"
```

这个会导致最终结果超过 `TOOL_RESULT_MAX_CHARS`。

现在：

```python
limit = settings.TOOL_RESULT_MAX_CHARS
keep_len = max(0, limit - len(_TRUNCATION_MARKER))
md_result = md_result[:keep_len].rstrip() + _TRUNCATION_MARKER
```

这样最终长度更接近配置上限。

---

## 保持不变的点

```text
1. session_id 仍然是两行原逻辑
2. force_browser 不做宽松解析
3. 不加 close
4. 不加 context manager
5. 不改 settings 读取方式
6. 不改 FetchCoordinator 构造方式
7. 不改工具 description 的整体语义
8. 不改失败返回风格
```

---

## 最小验收标准

### 缺少 session_id

```python
await tool.execute({}, url="https://example.com")
```

返回：

```text
[Tool Error] Missing session_id in execution context.
```

---

### 缺少 URL

```python
await tool.execute({"session_id": "s"})
```

返回：

```text
[Tool Error] Missing required url parameter
```

---

### 非 HTTP URL

```python
await tool.execute({"session_id": "s"}, url="file:///tmp/a.txt")
```

返回：

```text
[Tool Error] Invalid url parameter. URL must start with http:// or https://.
```

---

### 正常 URL

```python
await tool.execute({"session_id": "s"}, url="https://example.com")
```

应正常调用：

```python
self._fetcher.fetch(url, force_browser=force_browser)
```

---

## 结论

这版只做必要优化：

```text
URL 基础校验
fetch 边界异常保护
结果 strip
空白结果判断
截断长度修正
```

不引入额外生命周期管理，不新增复杂参数解析，不改变原有调度逻辑。
