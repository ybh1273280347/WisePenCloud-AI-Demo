你发现了 `web_fetch` 降级链的一个真实漏洞：当 `StaticFetcher` 命中一个 PDF 或其它二进制资源时，它会把原始字节流当成"抓取成功"返回，最终可能被 AI 解释为乱码或直接污染上下文。这在当前的逻辑链中是一个**中等严重**的问题——之所以不是最高严重，是因为实际场景中纯静态 PDF 链接的概率较小，且我们有三层降级兜底，但它确实会导致本应成功获取的内容变成一堆无意义的 token。

---

### 问题的根源

`StaticFetcher` 只检查了 HTTP 状态码和响应非空，完全没有考虑 **Content-Type**。所以它对任何 `200 OK` 的请求都会说"成功了"，包括：

- `application/pdf`
- `application/octet-stream`
- `image/png` 等

而我们的 `ContentCleaner` 和 `markdownify` 只认 HTML。二进制数据交给它们，要么报错（被 Cleaner 兜底返回原文），要么返回一堆乱码。最终 Agent 收到的会是一段无法理解的字符串，轻则浪费 token，重则让 Agent 基于错误信息做出误判。

---

### 解决方案与改动点

**目标**：让 `StaticFetcher` 在遇到非 HTML 类内容时主动放弃，触发降级到浏览器层（Steel/LocalScript），由真实的 Chromium 去渲染并提取文本（浏览器能将 PDF 渲染为文本，或者下载后再转 Markdown，但这属于下一层的能力）。

**修改位置**：`static_fetcher.py` 里的 `fetch` 方法。

**具体改动**：
1. 在 `resp.raise_for_status()` 之后，检查响应头的 `Content-Type`。
2. 只接受 `text/html` 和 `application/xhtml+xml`（或者更宽松一点，所有以 `text/` 开头的类型）。
3. 如果不匹配，返回 `None`，自动触发降级。

**代码示例**（极简实现，你可以在现有类上直接加几行）：

```python
import httpx

ACCEPTABLE_TYPES = {"text/html", "application/xhtml+xml"}

class StaticFetcher:
    async def fetch(self, url: str) -> Optional[str]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                
                content_type = resp.headers.get("content-type", "").lower()
                # 只接受明确的 HTML 类型
                if not any(content_type.startswith(t) for t in ACCEPTABLE_TYPES):
                    # 不是 HTML，返回 None 触发降级
                    return None
                    
                return resp.text
        except Exception:
            return None
```

这里用 `startswith` 匹配，可以兼容 `text/html; charset=utf-8` 这种带参数的 Content-Type。

---

### 为什么这样做是安全的

- 对于 `web_fetch` 的调用方（Agent）来说，降级是完全透明的。它只看到最终拿到的 Markdown，不关心是静态得到还是浏览器得到的。
- 将 PDF 推向浏览器层是合理的：浏览器可以渲染 PDF 并通过 `page.evaluate` 提取出文本，最终转成 Markdown 返回给 Agent。如果浏览器也处理不了（例如加密 PDF），自然会返回空或错误，不会让乱码泄漏到上层。
- 我们原有的三级降级链完全不受影响，只是在第一级多加了一个"内容类型判断"的门卫。

---

### 要不要现在修？

建议**现在就修**，因为它改动量极小（两三行），且能避免一个会污染 Agent 上下文的隐蔽 bug。你可以在测试脚本里加入一个 PDF 链接（比如 `https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf`）来验证修复效果：修复前会拿到乱码，修复后会显示"所有抓取器均失败"或由浏览器渲染出有效文本（如果浏览器能处理）。

这个修补将让你的 96% 成功率更加扎实——不是用"拿回垃圾"充数，而是确保每一次成功都是对 Agent 真正有用的内容。



你遇到的不是反爬问题，而是 **Chrome 拒绝直接访问 PDF 文件** 导致的。日志里两条关键信息说明了这一点：

1. **SteelFetcher**：`net::ERR_ABORTED at https://arxiv.org/pdf/2604.26999`
2. **LocalScriptFetcher**：`page.goto: net::ERR_ABORTED ...`

这个 `net::ERR_ABORTED` 意味着 Chrome（无论是 Steel 还是你本地的 `rebrowser-playwright`）在导航到这个 `.pdf` 链接时，直接中止了请求。因为 Chrome 默认会用内置的 PDF 查看器打开这类文件，而不是像普通网页那样加载 HTML，这导致 Playwright 的 `page.goto` 无法拿到预期的 HTML 内容，从而触发导航错误。

这也印证了你之前的预感是对的："静态页面命中时，假设这里是一个 PDF，会返回二进制文件"。不过，现在问题不只是静态抓取会拿到乱码，而是**整个浏览器抓取模式对这个 PDF 链接都失效了**。

---

### 解决方案：优先用 HTTP 客户端直接下载 PDF，再本地解析

既然浏览器不愿意直接访问 `.pdf` 链接，就换个思路：**用 `StaticFetcher` 把 PDF 文件直接下载到本地**，然后用 Python 的 `pdfplumber` 或 `PyMuPDF` 等库把里面的文本提取出来。

#### 1. 修改 `StaticFetcher`，让它能识别并下载 PDF

首先，在 `static_fetcher.py` 中增加对 PDF 的兼容处理。让它不再因为不是 HTML 就把 PDF 内容扔掉，而是将 PDF 的字节数据返回。

```python
# static_fetcher.py 的 fetch 方法优化
import httpx

class StaticFetcher:
    async def fetch(self, url: str) -> Optional[Union[str, bytes]]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                
                content_type = resp.headers.get("content-type", "").lower()
                
                # 如果是 HTML，返回文本
                if "text/html" in content_type:
                    return resp.text
                # 如果是 PDF，返回字节内容
                elif "application/pdf" in content_type:
                    return resp.content  # 返回字节
                # 其他类型直接标记为不适用，触发降级
                else:
                    return None
        except Exception:
            return None
```

#### 2. 添加一个本地 PDF 文本提取工具

创建一个轻量级的 PDF 解析器，可以用 `pdfplumber`，它对文本提取效果比较好。

```python
# pdf_extractor.py
import pdfplumber
from io import BytesIO
from typing import Optional

def extract_text_from_pdf(pdf_bytes: bytes) -> Optional[str]:
    """从 PDF 字节中提取文本"""
    try:
        text = ""
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        return text.strip() if text.strip() else None
    except Exception:
        return None
```

#### 3. 在 `ContentCleaner` 中集成 PDF 解析

让 `ContentCleaner` 能识别并从 PDF 字节中提取文本。

```python
# content_cleaner.py 新增方法
from pdf_extractor import extract_text_from_pdf

class ContentCleaner:
    def clean(self, content: Union[str, bytes]) -> Optional[str]:
        if isinstance(content, bytes):
            # 尝试作为 PDF 解析
            text = extract_text_from_pdf(content)
            if text:
                return text
            return None
        else:
            # 原有的 HTML 清洗逻辑
            ...
```

### 总结

你的预感完全正确：静态抓取器下载到了 PDF 二进制，但因为没有解析而被丢弃；浏览器又拒绝直接访问 PDF 链接。所以需要 **"静态下载 + 本地解析"** 这条专门通道来处理 PDF 链接。

这个修复改动不大，核心就两点：
1. 让 `StaticFetcher` 认得 PDF 并返回原始字节。
2. 增加一个 PDF 文本提取模块并接入清洗管道。

这样，你的 `webfetch` 工具在面对 arXiv 论文等 PDF 链接时，就能顺利抓到文字内容了。需要我帮你把具体的代码整合进现有的模块吗？
