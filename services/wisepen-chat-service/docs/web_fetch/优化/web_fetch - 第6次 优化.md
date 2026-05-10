可以。这里的“最佳实践但不大动干戈”，我建议只做 **4 个实用增强**：

1. **用流式读取限制响应体大小**，避免超大文件一次性进内存。HTTPX 官方支持 `AsyncClient.stream()` 和 `Response.aiter_bytes()` 用于异步流式读取。([Httpx][1])
2. **保留你现在的 `AsyncHTTPTransport(retries=...)`**，但明确它主要处理连接层失败，比如 `ConnectError` / `ConnectTimeout`，不是完整业务重试。([Httpx][2])
3. **增加 URL 扩展名兜底**，处理 `application/octet-stream` 或错误 `Content-Type` 的文档直链。
4. **补充少量文本 MIME 类型**，比如 `application/*+json`、`application/*+xml`。

不建议引入持久化 `AsyncClient` / `close()` 生命周期。HTTPX 的异步 client 确实可以跨任务共享，但那会引入资源关闭管理；在你这个“简单静态抓取器 + 调度链路兜底”的结构里，继续每次 `fetch()` 内部创建 client 更符合当前复杂度。([Httpx][3])

---

# 推荐优化版

```python
import httpx
from typing import Optional, Set
from urllib.parse import urlparse

from common.logger import log_ok, log_fail, log_error


_SUPPORTED_DOC_MIME_TYPES: Set[str] = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

_TEXT_FRIENDLY_MIME_TYPES: Set[str] = {
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-javascript",
}

_SUPPORTED_DOC_EXTENSIONS = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
)

_TEXT_FRIENDLY_EXTENSIONS = (
    ".txt",
    ".md",
    ".json",
    ".xml",
    ".csv",
)

_MAX_RESPONSE_BYTES = 50 * 1024 * 1024


class StaticFetcher:
    """轻量级静态 HTTP 抓取器"""

    def __init__(
        self,
        timeout: float = 10.0,
        max_retries: int = 3,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
    ):
        self._timeout = timeout
        self._max_retries = max_retries
        self._max_response_bytes = max_response_bytes
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    async def fetch(self, url: str) -> Optional[str | bytes]:
        try:
            transport = httpx.AsyncHTTPTransport(retries=self._max_retries)
            async with httpx.AsyncClient(
                timeout=self._timeout,
                headers=self._headers,
                transport=transport,
                follow_redirects=True,
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()

                    content = await self._read_limited(response, url)
                    if content is None:
                        return None

                    return self._route_response(response, url, content)

        except httpx.TimeoutException:
            log_fail("静态抓取", f"请求超时 {self._timeout}s", url=url)
            return None

        except httpx.ConnectError:
            log_fail("静态抓取", "连接失败", url=url)
            return None

        except httpx.HTTPStatusError as e:
            log_fail("静态抓取", f"HTTP {e.response.status_code}", url=url)
            return None

        except httpx.TooManyRedirects:
            log_fail("静态抓取", "重定向次数过多", url=url)
            return None

        except httpx.RequestError as e:
            log_fail("静态抓取", f"请求异常: {e.__class__.__name__}", url=url)
            return None

        except Exception as e:
            log_error("静态抓取", e, url=url)
            return None

    async def _read_limited(self, response: httpx.Response, url: str) -> Optional[bytes]:
        content_length = response.headers.get("content-length")

        if content_length:
            try:
                expected_size = int(content_length)
            except ValueError:
                expected_size = 0

            if expected_size > self._max_response_bytes:
                log_fail(
                    "静态抓取",
                    f"响应体过大({expected_size}字节)，上限{self._max_response_bytes}字节",
                    url=url,
                )
                return None

        chunks = []
        total_size = 0

        async for chunk in response.aiter_bytes():
            total_size += len(chunk)

            if total_size > self._max_response_bytes:
                log_fail(
                    "静态抓取",
                    f"响应体超过上限({total_size}字节)，上限{self._max_response_bytes}字节",
                    url=url,
                )
                return None

            chunks.append(chunk)

        return b"".join(chunks)

    def _route_response(
        self,
        response: httpx.Response,
        url: str,
        content: bytes,
    ) -> Optional[str | bytes]:
        media_type = response.headers.get("content-type", "").lower().split(";")[0].strip()
        path = urlparse(url).path.lower()

        if self._is_text_response(media_type, path, content):
            text = self._decode_text(response, content).strip()

            if not text:
                log_fail("静态抓取", "文本响应为空", url=url)
                return None

            log_ok("静态抓取", content_type=media_type or "unknown", size=len(content), url=url)
            return text

        if self._is_document_response(media_type, path, content):
            log_ok("静态抓取", content_type=media_type or "unknown", size=len(content), url=url)
            return content

        log_fail("静态抓取", f"不支持的 Content-Type: {media_type or 'unknown'}", url=url)
        return None

    def _is_text_response(self, media_type: str, path: str, content: bytes) -> bool:
        if media_type.startswith("text/"):
            return True

        if media_type in _TEXT_FRIENDLY_MIME_TYPES:
            return True

        if media_type.endswith("+json") or media_type.endswith("+xml"):
            return True

        if path.endswith(_TEXT_FRIENDLY_EXTENSIONS):
            return True

        head = content[:512].lstrip().lower()
        if head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<html" in head[:128]:
            return True

        return False

    def _is_document_response(self, media_type: str, path: str, content: bytes) -> bool:
        if media_type in _SUPPORTED_DOC_MIME_TYPES:
            return True

        if path.endswith(_SUPPORTED_DOC_EXTENSIONS):
            return True

        if content.startswith(b"%PDF-"):
            return True

        return False

    def _decode_text(self, response: httpx.Response, content: bytes) -> str:
        encoding = response.encoding or "utf-8"

        try:
            return content.decode(encoding, errors="replace")
        except LookupError:
            return content.decode("utf-8", errors="replace")
```

---

# 为什么这是“最佳实践但不大改”

## 保留原结构

没动这些核心设计：

```text
StaticFetcher.fetch(url) -> Optional[str | bytes]
每次 fetch 内部创建 AsyncClient
异常内部消化，返回 None
Content-Type 决定 str / bytes
文档交给 ContentProcessor
```

没有引入：

```text
持久化 AsyncClient
close()
async context manager
外部生命周期管理
复杂重试框架
缓存
并发池
```

---

## 关键增强 1：响应体大小上限

原版：

```python
response = await client.get(url)
```

会先把响应体完整读入内存。

新版：

```python
async with client.stream("GET", url) as response:
    content = await self._read_limited(response, url)
```

配合：

```python
_MAX_RESPONSE_BYTES = 50 * 1024 * 1024
```

这样即使对方返回超大 PDF、视频、压缩包，也不会无限读入内存。

这是最值得加的优化。

---

## 关键增强 2：扩展名兜底

很多真实下载链接会返回：

```text
application/octet-stream
```

或者干脆没有正确 `Content-Type`。

所以新增：

```python
_SUPPORTED_DOC_EXTENSIONS = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
)
```

这样 URL 是：

```text
https://example.com/report.pdf
https://example.com/file.docx
```

即使 MIME 不标准，也能返回 `bytes` 给 `ContentProcessor`。

---

## 关键增强 3：文本类型覆盖更完整

新增支持：

```python
media_type.endswith("+json")
media_type.endswith("+xml")
```

可以覆盖：

```text
application/ld+json
application/rss+xml
application/atom+xml
application/problem+json
```

这些本质上都是文本，应该作为 `str` 返回。

---

## 关键增强 4：少量内容嗅探

只做两个非常低风险的嗅探：

```python
content.startswith(b"%PDF-")
```

和：

```python
head.startswith(b"<!doctype html")
head.startswith(b"<html")
```

这不是复杂类型识别，只是处理服务端 MIME 错误的常见情况。

---

# 一个小顺序建议

我把 `_route_response()` 里文本判断放在文档判断前：

```python
if self._is_text_response(...):
    ...

if self._is_document_response(...):
    ...
```

原因是 HTML 页面更常见，而且 `text/html` 应优先作为文本返回。

不过 PDF magic 只会在文档判断里命中，所以不会误处理 PDF。

---

# 不建议加的东西

## 不加 `close()`

这里不需要：

```python
async def close(...)
```

因为当前设计没有持久化 client。

---

## 不持久化 `AsyncClient`

虽然 HTTPX 支持共享 `AsyncClient`，但那会引入关闭时机和生命周期管理。你现在的抓取器是调度链里一个轻量组件，每次请求内部创建 client 更简单。([Httpx][3])

---

## 不加业务重试

你现在的：

```python
httpx.AsyncHTTPTransport(retries=self._max_retries)
```

可以保留。HTTPX 文档说明 transport 层 retries 主要针对连接错误和连接超时。([Httpx][2])

不要在这里再实现 HTTP 500 / 429 业务重试，否则会和上层降级链路叠加，导致耗时不可控。

---

# 最终结论

这版优化是我认为最合适的平衡点：

```text
保留原类结构
保留原返回协议
保留原异常处理风格
不引入生命周期复杂度
增加响应体大小保护
增加 MIME / 扩展名兜底
增加文本类型覆盖
增加 PDF / HTML 轻量嗅探
```

也就是：**更抗真实网页和文档下载的脏输入，但不把 StaticFetcher 改成复杂 HTTP 框架。**

[1]: https://www.python-httpx.org/async/?utm_source=chatgpt.com "Async Support"
[2]: https://www.python-httpx.org/advanced/transports/?utm_source=chatgpt.com "Transports"
[3]: https://www.python-httpx.org/api/?utm_source=chatgpt.com "Developer Interface"
