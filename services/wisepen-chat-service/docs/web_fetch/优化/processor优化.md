有，`ContentProcessor` 本身还可以做一轮**小而明确的优化**。你说的第一点是对的：现在注释已经过时了。

当前注释：

```python
"""内容处理器：负责 str/bytes 分流，以及 HTML/纯文本清洗。"""
```

现在它已经不只是“清洗”了，而是：

```text
str / bytes 分流
文档解析委派
HTML 正文抽取
Markdown 规范化
页面阻断检测
纯文本规范化
```

建议改成：

```python
"""内容处理器：负责将抓取结果转换为可供模型消费的 Markdown 文本。

职责：
- bytes 内容交给 DocumentParser 解析；
- HTML 内容使用 Trafilatura 抽取正文并输出 Markdown；
- 纯文本内容进行规范化；
- 对反爬、登录、验证码、JS 空壳页面做检测，失败时返回 None 触发抓取降级。
"""
```

---

# 我建议继续优化的点

## 1. 把“阻断检测 + 日志”封装成一个模块级函数

现在 `_process_text`、`_process_html`、`_process_plain_text` 里重复了：

```python
detection = detect_page_block(...)
if should_degrade_page(...):
    log_page_block(...)
    return None
```

可以做一个模块级函数：

```python
def should_reject_page(
    text: str,
    *,
    stage: str,
    html: str = "",
) -> bool:
    detection = detect_page_block(text, html=html)

    if not should_degrade_page(text, html=html):
        return False

    log_page_block(stage, detection)
    return True
```

这样主流程更清楚：

```python
if should_reject_page(stripped, stage="内容检测", html=stripped):
    return None
```

---

## 2. HTML 判断抽成模块级函数

现在：

```python
lower_head = stripped[:1024].lower()
is_html = "<html" in lower_head or "<!doctype html" in lower_head or "<body" in lower_head
```

建议抽成：

```python
HTML_DETECTION_SCAN_CHARS = 1024

def looks_like_html(text: str) -> bool:
    lower_head = text[:HTML_DETECTION_SCAN_CHARS].lower()
    return "<html" in lower_head or "<!doctype html" in lower_head or "<body" in lower_head
```

这个函数不使用类状态，放模块级更合适。

---

## 3. Trafilatura 参数抽成模块常量

现在 `_process_html` 里内联：

```python
trafilatura.extract(
    html,
    output_format="markdown",
    include_comments=False,
    include_tables=True,
    include_links=True,
    favor_precision=False,
)
```

可以抽成：

```python
TRAFILATURA_OUTPUT_FORMAT = "markdown"
TRAFILATURA_INCLUDE_COMMENTS = False
TRAFILATURA_INCLUDE_TABLES = True
TRAFILATURA_INCLUDE_LINKS = True
TRAFILATURA_FAVOR_PRECISION = False
```

但我更建议简单一点，只抽一个函数：

```python
def extract_markdown_from_html(html: str) -> Optional[str]:
    extracted = trafilatura.extract(...)
    if not extracted:
        return None
    return normalize_text(extracted)
```

这样 `_process_html` 不关心 Trafilatura 细节。

---

## 4. `_process_html` 里 `try` 范围可以缩小

现在整个 `_process_html` 都在一个 `try` 里，包括：

```python
normalize_text
detect_page_block
should_degrade_page
len(result)
log_ok
```

严格来说，真正可能抛三方异常的是：

```python
trafilatura.extract(...)
```

建议把异常边界收窄到 `extract_markdown_from_html()`，让 `_process_html` 只处理业务判断。

---

# 推荐最终结构

```python
HTML_DETECTION_SCAN_CHARS = 1024


def looks_like_html(text: str) -> bool:
    lower_head = text[:HTML_DETECTION_SCAN_CHARS].lower()
    return "<html" in lower_head or "<!doctype html" in lower_head or "<body" in lower_head


def extract_markdown_from_html(html: str) -> Optional[str]:
    try:
        extracted = trafilatura.extract(
            html,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            include_links=True,
            favor_precision=False,
        )
    except Exception as e:
        log_fail("HTML 清洗", e, extractor="trafilatura")
        return None

    if not extracted:
        log_fail("HTML 清洗", "Trafilatura 未提取到正文，触发降级")
        return None

    return normalize_text(extracted)


def log_page_block(stage: str, detection) -> None:
    ...


def should_reject_page(
    text: str,
    *,
    stage: str,
    html: str = "",
) -> bool:
    detection = detect_page_block(text, html=html)

    if not should_degrade_page(text, html=html):
        return False

    log_page_block(stage, detection)
    return True
```

然后类里变成：

```python
class ContentProcessor:
    """内容处理器：负责将抓取结果转换为可供模型消费的 Markdown 文本。

    职责：
    - bytes 内容交给 DocumentParser 解析；
    - HTML 内容使用 Trafilatura 抽取正文并输出 Markdown；
    - 纯文本内容进行规范化；
    - 对反爬、登录、验证码、JS 空壳页面做检测，失败时返回 None 触发抓取降级。
    """

    ...
```

`_process_text`：

```python
def _process_text(self, content: str) -> Optional[str]:
    stripped = content.strip()
    if not stripped:
        return None

    if looks_like_html(stripped):
        if should_reject_page(stripped, stage="内容检测", html=stripped):
            return None

        return self._process_html(stripped)

    if should_reject_page(stripped, stage="内容检测"):
        return None

    return self._process_plain_text(stripped)
```

`_process_html`：

```python
def _process_html(self, html: str) -> Optional[str]:
    result = extract_markdown_from_html(html)
    if result is None:
        return None

    if should_reject_page(result, stage="HTML 清洗", html=html):
        return None

    if len(result) < self._min_content_length:
        log_fail(
            "HTML 清洗",
            f"清洗后文本过短({len(result)}字符)，阈值{self._min_content_length}，触发降级",
        )
        return None

    log_ok("HTML 清洗", extractor="trafilatura", length=len(result))
    return result
```

`_process_plain_text`：

```python
def _process_plain_text(self, text: str) -> Optional[str]:
    normalized = normalize_text(text)

    if len(normalized) < self._min_content_length:
        log_fail(
            "纯文本检测",
            f"文本过短({len(normalized)}字符)，阈值{self._min_content_length}，触发降级",
        )
        return None

    if should_reject_page(normalized, stage="纯文本检测"):
        return None

    return normalized
```

---

# 给 Codex 的提示词

```text
请优化 content_processor.py，保持现有功能语义不变，不要改 FetchCoordinator 降级协议，不要改返回类型。

一、修改类注释

将 ContentProcessor 当前注释：

“内容处理器：负责 str/bytes 分流，以及 HTML/纯文本清洗。”

改成更准确的说明：

内容处理器负责将抓取结果转换为可供模型消费的 Markdown 文本。

职责：
- bytes 内容交给 DocumentParser 解析；
- HTML 内容使用 Trafilatura 抽取正文并输出 Markdown；
- 纯文本内容进行规范化；
- 对反爬、登录、验证码、JS 空壳页面做检测，失败时返回 None 触发抓取降级。

二、新增模块级常量和函数

1. 新增：
HTML_DETECTION_SCAN_CHARS = 1024

2. 新增模块级函数：
looks_like_html(text: str) -> bool

逻辑：
lower_head = text[:HTML_DETECTION_SCAN_CHARS].lower()
return "<html" in lower_head or "<!doctype html" in lower_head or "<body" in lower_head

3. 新增模块级函数：
extract_markdown_from_html(html: str) -> Optional[str]

逻辑：
- 调用 trafilatura.extract(
      html,
      output_format="markdown",
      include_comments=False,
      include_tables=True,
      include_links=True,
      favor_precision=False,
  )
- 只在该函数内捕获 trafilatura 异常：
  log_fail("HTML 清洗", e, extractor="trafilatura")
  return None
- 如果 extracted 为空：
  log_fail("HTML 清洗", "Trafilatura 未提取到正文，触发降级")
  return None
- 返回 normalize_text(extracted)

4. 保留 log_page_block(stage: str, detection) 模块级函数。

5. 新增模块级函数：
should_reject_page(text: str, *, stage: str, html: str = "") -> bool

逻辑：
- detection = detect_page_block(text, html=html)
- if not should_degrade_page(text, html=html): return False
- log_page_block(stage, detection)
- return True

三、简化 ContentProcessor 方法

1. _process_text:
- strip 空内容返回 None
- 使用 looks_like_html(stripped) 判断 HTML
- 如果是 HTML：
    if should_reject_page(stripped, stage="内容检测", html=stripped): return None
    return self._process_html(stripped)
- 如果不是 HTML：
    if should_reject_page(stripped, stage="内容检测"): return None
    return self._process_plain_text(stripped)

2. _process_html:
- result = extract_markdown_from_html(html)
- result is None 返回 None
- if should_reject_page(result, stage="HTML 清洗", html=html): return None
- 长度小于 self._min_content_length 时 log_fail 并返回 None
- 成功 log_ok("HTML 清洗", extractor="trafilatura", length=len(result))
- return result

3. _process_plain_text:
- normalized = normalize_text(text)
- 长度小于 self._min_content_length 时返回 None
- if should_reject_page(normalized, stage="纯文本检测"): return None
- return normalized

四、不要做

- 不要恢复 readability / markdownify
- 不要新增 fallback 到 normalize_text(html)
- 不要新增模块级 ContentProcessor 单例
- 不要改变 process/process_async 对外行为
- 不要改变 __all__
- 不要引入新依赖
- 不要把这些函数放入类中；不使用 self 的函数保持模块级
```

---

# 结论

`ContentProcessor` 本身的优化重点不是单例，而是：

```text
1. 注释更新，准确表达职责
2. HTML 判断、Trafilatura 抽取、阻断检测日志都移成模块级 helper
3. 类只保留真正的处理流程
4. 异常边界收窄
```

这样后续如果你要在容器里把它做成单例，也不会影响它本身的清晰度。
