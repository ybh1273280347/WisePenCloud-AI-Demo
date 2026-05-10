下面是整合三条补丁后的**最终执行文档版本**。

---

# web_fetch 用户侧效果强化最终执行文档

## 目标

本轮只做 **用户能直接感知的效果增强**：

```text
1. 提升网页抓取成功率
2. 提升正文质量
3. 减少中文网页乱码
4. 提升文档直链 handoff 的文件名和后缀准确率
5. 提升本地浏览器兜底抓取效果
```

当前架构冻结，不再做架构调整，不再做开发者诊断体验优化。

---

## 禁止事项

```text
1. 不新增工具。
2. 不新增复杂架构。
3. 不改 ToolContentStore 协议。
4. 不让 web_fetch 调用 document_parse。
5. 不恢复 force_browser / force_ocr。
6. 不新增第三方库。
7. 不做 failure reason 结构化。
8. 不优化开发者日志 / 错误分类 / 诊断信息。
9. 不新增 sidecar metadata。
10. 不做 RSS / Atom，本轮暂缓。
11. 不改模型可见协议字段。
12. 不把文档解析、OCR、表格抽取能力塞回 web_fetch。
```

涉及文件：

```text
src/chat/application/web_fetch/fetcher/static_fetcher.py
src/chat/application/web_fetch/content_processor.py
src/chat/application/web_fetch/local_web_fetcher.js
```

---

# 一、StaticFetcher 支持 Content-Disposition 文件名

## 目标

让这类直链下载获得正确文件名和后缀：

```text
https://example.com/download?id=123
Content-Disposition: attachment; filename*=UTF-8''%E6%96%87%E4%BB%B6.pdf
```

最终 handoff filename 应为：

```text
文件.pdf
```

---

## 实现要求

```text
1. 优先解析 Content-Disposition 的 filename*。
2. filename* 按 RFC 5987 形式解析，例如 UTF-8''%E6%96%87%E4%BB%B6.pdf。
3. 其次解析 filename。
4. 再回退 URL path basename。
5. 最后用 MIME type 推断 download.xxx。
6. 不引入第三方库。
7. 最终 filename 仍经过现有安全文件名清洗。
8. document_parse 支持范围是 pdf/docx/pptx/xls/xlsx/ods/epub，不要恢复 doc/ppt。
9. 如果 header / URL 文件名已有不受支持的后缀，不要拼成 name.exe.pdf，应使用 stem + fallback_suffix。
```

---

## 参考代码

在 `static_fetcher.py` 中增加：

```python
import re
from pathlib import PurePosixPath
from typing import Optional
from urllib.parse import unquote
```

```python
_CONTENT_DISPOSITION_FILENAME_STAR_RE = re.compile(
    r"filename\*\s*=\s*([^;]+)",
    re.IGNORECASE,
)

_CONTENT_DISPOSITION_FILENAME_RE = re.compile(
    r"filename\s*=\s*(?P<filename>\"[^\"]+\"|'[^']+'|[^;]+)",
    re.IGNORECASE,
)
```

新增：

```python
def _filename_from_content_disposition(value: str) -> Optional[str]:
    filename_star = _filename_from_rfc5987(value)
    if filename_star:
        return filename_star

    match = _CONTENT_DISPOSITION_FILENAME_RE.search(value)
    if not match:
        return None

    return match.group("filename").strip().strip("\"'").strip() or None


def _filename_from_rfc5987(value: str) -> Optional[str]:
    match = _CONTENT_DISPOSITION_FILENAME_STAR_RE.search(value)
    if not match:
        return None

    raw = match.group(1).strip().strip("\"'")

    try:
        charset, _, encoded = raw.split("'", 2)
    except ValueError:
        return None

    try:
        return unquote(
            encoded,
            encoding=charset or "utf-8",
        ).strip() or None
    except LookupError:
        return unquote(
            encoded,
            encoding="utf-8",
            errors="replace",
        ).strip() or None
```

新增统一后缀处理 helper：

```python
def _with_supported_suffix(name: str, fallback_suffix: str) -> str:
    path_name = PurePosixPath(name)
    suffix = path_name.suffix.lower()

    if suffix in DOCUMENT_EXTENSIONS:
        return path_name.name

    stem = path_name.stem or path_name.name
    return f"{stem}{fallback_suffix}"
```

修改 `_document_filename()`：

```python
def _document_filename(
    *,
    path: str,
    media_type: str,
    content_disposition: str,
) -> str:
    fallback_suffix = _document_suffix(
        path=path,
        media_type=media_type,
    )

    header_filename = _filename_from_content_disposition(content_disposition)
    if header_filename:
        name = PurePosixPath(
            header_filename.replace("\\", "/")
        ).name.strip()

        if name:
            return _with_supported_suffix(
                name,
                fallback_suffix,
            )

    name = PurePosixPath(unquote(path)).name.strip()
    if name:
        return _with_supported_suffix(
            name,
            fallback_suffix,
        )

    return f"download{fallback_suffix}"


def _document_suffix(*, path: str, media_type: str) -> str:
    suffix = PurePosixPath(unquote(path)).suffix.lower()
    if suffix in DOCUMENT_EXTENSIONS:
        return suffix

    return _DOCUMENT_EXTENSION_BY_MIME_TYPE.get(media_type, ".bin")
```

修改 `_route_response()` 调用处：

```python
return FetchedDocument(
    url=url,
    media_type=media_type or "application/octet-stream",
    filename=_document_filename(
        path=path,
        media_type=media_type,
        content_disposition=content_disposition,
    ),
    content=content,
)
```

在 `fetch()` 中读取 header：

```python
content_disposition = response.headers.get(
    "content-disposition",
    "",
)
```

并传入 `_route_response()`。

---

## 文件名行为样例

```text
Content-Disposition: attachment; filename*=UTF-8''%E6%96%87%E4%BB%B6.pdf
=> 文件.pdf
```

```text
Content-Disposition: attachment; filename="report.pdf"
=> report.pdf
```

```text
Content-Disposition: attachment; filename='report.pdf'
=> report.pdf
```

```text
Content-Disposition: attachment; filename=report.pdf
=> report.pdf
```

```text
report.exe + application/pdf
=> report.pdf
```

```text
archive.bin + application/epub+zip
=> archive.epub
```

```text
/download?id=1 + application/pdf
=> download.pdf
```

---

# 二、StaticFetcher 支持 charset-aware decode

## 目标

减少 GBK / GB2312 中文网页乱码。

当前不要使用 `response.text`，继续基于原始 `bytes` 解码。

---

## 解码优先级

```text
1. Content-Type header charset
2. HTML head 前 4096 bytes 中的 <meta charset="...">
3. HTML head 前 4096 bytes 中的 meta http-equiv Content-Type charset
4. utf-8 errors=replace fallback
```

补充要求：

```text
如果 Content-Type header charset 存在但无法识别，不要立刻 fallback utf-8。
应继续尝试 HTML meta charset / meta http-equiv charset。
最后才 utf-8 fallback。
```

---

## 参考代码

增加：

```python
_CONTENT_TYPE_CHARSET_RE = re.compile(
    r"charset\s*=\s*['\"]?([^;'\"]+)",
    re.IGNORECASE,
)

_META_CHARSET_RE = re.compile(
    rb"<meta[^>]+charset\s*=\s*['\"]?([^'\"\s/>;]+)",
    re.IGNORECASE,
)

_META_TAG_RE = re.compile(
    rb"<meta\b[^>]*>",
    re.IGNORECASE,
)
```

新增：

```python
def _decode_text_response(
    content: bytes,
    *,
    content_type_header: str,
) -> str:
    encoding = _charset_from_content_type(content_type_header)
    if encoding:
        decoded = _try_decode_with_encoding(content, encoding)
        if decoded is not None:
            return decoded

    encoding = _charset_from_html_meta(content)
    if encoding:
        decoded = _try_decode_with_encoding(content, encoding)
        if decoded is not None:
            return decoded

    return content.decode("utf-8", errors="replace")


def _try_decode_with_encoding(
    content: bytes,
    encoding: str,
) -> Optional[str]:
    try:
        return content.decode(encoding, errors="replace")
    except LookupError:
        return None


def _charset_from_content_type(value: str) -> Optional[str]:
    match = _CONTENT_TYPE_CHARSET_RE.search(value)
    if not match:
        return None

    return match.group(1).strip()


def _charset_from_html_meta(content: bytes) -> Optional[str]:
    head = content[:4096]

    match = _META_CHARSET_RE.search(head)
    if match:
        return match.group(1).decode(
            "ascii",
            errors="replace",
        ).strip()

    for meta_match in _META_TAG_RE.finditer(head):
        tag = meta_match.group(0)
        lower_tag = tag.lower()

        if b"http-equiv" not in lower_tag:
            continue

        if b"content-type" not in lower_tag:
            continue

        tag_text = tag.decode(
            "ascii",
            errors="replace",
        )

        charset_match = _CONTENT_TYPE_CHARSET_RE.search(tag_text)
        if charset_match:
            return charset_match.group(1).strip()

    return None
```

修改 `_route_response()`，增加 `content_type_header` 参数：

```python
def _route_response(
    *,
    media_type: str,
    content_type_header: str,
    content_disposition: str,
    path: str,
    url: str,
    content: bytes,
) -> Optional[str | FetchedDocument]:
```

文本响应解码改为：

```python
text = _decode_text_response(
    content,
    content_type_header=content_type_header,
).strip()
```

在 `fetch()` 中保留原始 `Content-Type`：

```python
content_type_header = response.headers.get("content-type", "")
media_type = _get_media_type(response)
```

并传给 `_route_response()`。

---

## charset 行为样例

### 1. header charset

输入：

```http
Content-Type: text/html; charset=gbk
```

期望 charset：

```text
gbk
```

---

### 2. meta charset

输入：

```html
<meta charset="gbk">
```

期望 charset：

```text
gbk
```

---

### 3. meta http-equiv 顺序 A

输入：

```html
<meta http-equiv="Content-Type" content="text/html; charset=gbk">
```

期望 charset：

```text
gbk
```

---

### 4. meta http-equiv 顺序 B

输入：

```html
<meta content="text/html; charset=gbk" http-equiv="Content-Type">
```

期望 charset：

```text
gbk
```

---

### 5. header charset 写坏时继续尝试 meta

输入：

```http
Content-Type: text/html; charset=gb23122
```

```html
<meta charset="gbk">
```

期望：

```text
不要直接 utf-8 fallback。
应继续读取 meta charset，并使用 gbk 解码。
```

---

# 三、local_web_fetcher.js 提升浏览器兜底效果

## 目标

本地 JS 脚本只作为最后兜底抓取器。

协议必须保持：

```text
1. stdout 只输出 Markdown。
2. stderr 保持当前运行信息。
3. 不在 JS 里做 URL 安全校验。
4. 不在 JS 里做文档解析。
```

---

## 1. 不再阻断 stylesheet

当前资源阻断中移除 `stylesheet`。

改成：

```javascript
const BLOCKED_RESOURCE_TYPES = new Set([
  'image',
  'media',
  'font',
]);
```

不要新增阻断 `other`。

---

## 2. 不再删除 banner

从 `removeSelectors` 中删除：

```javascript
'[class*="banner"]',
'[id*="banner"]',
```

保留广告相关选择器：

```javascript
'ad',
'.ad',
'.ads',
'#ad',
'#ads',
'[class*="advert"]',
'[id*="advert"]',
```

---

## 3. 输出 Markdown 时加入 title

当前 `extractPageData()` 已经返回 `title`。

最终 Markdown 输出改为：

```javascript
const bodyMarkdown = normalizeMarkdown(
  turndownService.turndown(pageData.html)
);

const title = normalizeMarkdown(pageData.title || '');
const markdown = normalizeMarkdown(
  title ? `# ${title}\n\n${bodyMarkdown}` : bodyMarkdown
);

if (!markdown) {
  throw new Error('No readable content');
}

await writeStdout(markdown);
```

要求：

```text
1. stdout 仍然只输出 Markdown。
2. stderr 保持现有运行信息。
3. 不在 JS 里做 URL 安全校验。
4. 不在 JS 里做文档解析。
```

---

# 四、ContentProcessor 增强 trafilatura fallback

## 目标

提升 HTML 正文抽取成功率，不新增依赖。

---

## 提取顺序

```text
1. trafilatura.extract(... output_format="markdown", favor_recall=True)
2. 如果为空或过短，尝试 trafilatura.baseline(html)
3. baseline 仍为空或过短，尝试 trafilatura.html2txt(html)
4. 仍失败则返回 None，让 FetchCoordinator 继续 fallback
```

注意：

```text
1. trafilatura.baseline(html) 返回 tuple，必须解包取 text，不能直接当字符串用。
2. 所有 fallback 结果都必须走 normalize_text。
3. extract 过短时必须有机会继续 baseline / html2txt。
4. 不新增 BeautifulSoup / readability-lxml。
5. 不新增 failure reason。
6. 不优化开发者日志。
7. HTML 清洗成功日志不要固定写 extractor="trafilatura"。
```

---

## 参考代码

把 `_extract_markdown_from_html()` 改为接收 `min_content_length`：

```python
def _extract_markdown_from_html(
    html: str,
    *,
    min_content_length: int,
) -> Optional[str]:
    best_result: Optional[str] = None

    for extractor in (
        _extract_with_trafilatura,
        _extract_with_baseline,
        _extract_with_html2txt,
    ):
        extracted = extractor(html)
        if not extracted:
            continue

        normalized = normalize_text(extracted)

        if best_result is None or len(normalized) > len(best_result):
            best_result = normalized

        if len(normalized) >= min_content_length:
            return normalized

    return best_result
```

新增三个 extractor：

```python
def _extract_with_trafilatura(html: str) -> Optional[str]:
    try:
        return trafilatura.extract(
            html,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            include_links=True,
            favor_precision=False,
            favor_recall=True,
        )
    except Exception:
        return None


def _extract_with_baseline(html: str) -> Optional[str]:
    try:
        _, text, _ = trafilatura.baseline(html)
    except Exception:
        return None

    return text or None


def _extract_with_html2txt(html: str) -> Optional[str]:
    try:
        text = trafilatura.html2txt(html)
    except Exception:
        return None

    return text or None
```

修改 `_process_html()`：

```python
def _process_html(self, html: str) -> Optional[str]:
    result = _extract_markdown_from_html(
        html,
        min_content_length=self._min_content_length,
    )
    if result is None:
        return None

    if _should_reject_page(
        result,
        stage="HTML 清洗",
        html=html,
    ):
        return None

    if len(result) < self._min_content_length:
        log_fail(
            "HTML 清洗",
            f"清洗后文本过短({len(result)}字符)，阈值{self._min_content_length}，触发降级",
        )
        return None

    log_ok(
        "HTML 清洗",
        length=len(result),
    )
    return result
```

这里刻意不写：

```python
extractor="trafilatura"
```

原因：

```text
实际结果可能来自 trafilatura.extract / trafilatura.baseline / trafilatura.html2txt。
如果不记录真实 extractor，就不要固定写 extractor="trafilatura"，避免误标。
```

---

# 五、验收标准

## 代码边界验收

### 1. web_fetch 不应重新引入文档解析 / OCR / 表格抽取

```bash
rg "DocumentParser|Docling|OCR|Camelot|PPStructure" src/chat/application/web_fetch
```

不应出现。

---

### 2. 不应恢复 force_browser / force_ocr

```bash
rg "force_browser|force_ocr" src/chat/application/web_fetch
```

不应出现。

---

### 3. StaticFetcher 不应使用 response.text

```bash
rg "response\.text" src/chat/application/web_fetch/fetcher/static_fetcher.py
```

不应出现。

---

### 4. local_web_fetcher.js 不应阻断 stylesheet

```bash
rg "stylesheet" src/chat/application/web_fetch/local_web_fetcher.js
```

不应出现在 `BLOCKED_RESOURCE_TYPES` 中。

---

### 5. local_web_fetcher.js 不应删除 banner

```bash
rg "class\\*=\"banner\"|id\\*=\"banner\"" src/chat/application/web_fetch/local_web_fetcher.js
```

不应出现。

---

## 功能样例验收

### 1. Content-Disposition filename*

输入：

```text
attachment; filename*=UTF-8''%E6%96%87%E4%BB%B6.pdf
```

期望：

```text
文件.pdf
```

---

### 2. Content-Disposition 双引号 filename

输入：

```text
attachment; filename="report.pdf"
```

期望：

```text
report.pdf
```

---

### 3. Content-Disposition 单引号 filename

输入：

```text
attachment; filename='report.pdf'
```

期望：

```text
report.pdf
```

---

### 4. Content-Disposition 无引号 filename

输入：

```text
attachment; filename=report.pdf
```

期望：

```text
report.pdf
```

---

### 5. 不受支持后缀替换

输入：

```text
filename="report.exe"
media_type="application/pdf"
```

期望：

```text
report.pdf
```

---

### 6. URL path 不受支持后缀替换

输入：

```text
path="/files/archive.bin"
media_type="application/epub+zip"
```

期望：

```text
archive.epub
```

---

### 7. URL path 无有效文件名

输入：

```text
path="/download"
media_type="application/pdf"
```

期望：

```text
download.pdf
```

如果当前实现把 `/download` 视为 basename，则可接受：

```text
download.pdf
```

如果 path 为空，则期望：

```text
download.pdf
```

---

### 8. meta charset

输入：

```html
<meta charset="gbk">
```

期望 charset：

```text
gbk
```

---

### 9. meta http-equiv 顺序 A

输入：

```html
<meta http-equiv="Content-Type" content="text/html; charset=gbk">
```

期望 charset：

```text
gbk
```

---

### 10. meta http-equiv 顺序 B

输入：

```html
<meta content="text/html; charset=gbk" http-equiv="Content-Type">
```

期望 charset：

```text
gbk
```

---

### 11. header charset 写坏时继续尝试 meta

输入：

```http
Content-Type: text/html; charset=gb23122
```

```html
<meta charset="gbk">
```

期望：

```text
使用 gbk，而不是直接 utf-8 fallback。
```

---

### 12. local_web_fetcher.js 输出

期望：

```text
1. stdout 只输出 Markdown。
2. Markdown 开头包含页面 title。
3. stderr 可以继续输出 Fetching / Extracted text length / Error。
```

---

### 13. ContentProcessor fallback

准备一个 `trafilatura.extract()` 结果为空或过短、但 `baseline()` 或 `html2txt()` 能抽出正文的 HTML。

期望：

```text
1. extract 失败或过短后继续尝试 baseline。
2. baseline 失败或过短后继续尝试 html2txt。
3. 所有 fallback 结果都经过 normalize_text。
4. 最终结果仍经过 _should_reject_page。
5. 最终长度仍小于 min_content_length 时返回 None，让 FetchCoordinator 继续 fallback。
```

---

### 14. HTML 清洗成功日志

期望：

```text
log_ok("HTML 清洗", length=len(result))
```

不应固定写：

```text
extractor="trafilatura"
```

除非实现明确记录并输出真实 extractor 名称。

---

# 六、本轮完成后不继续追加的事项

```text
1. 不做 RSS / Atom。
2. 不做 sitemap。
3. 不做 GitHub 特化。
4. 不做 sidecar metadata。
5. 不做 handoff TTL 清理。
6. 不做 failure reason 结构化。
7. 不做错误码体系。
8. 不做开发者日志体验优化。
```

本轮只落地四项：

```text
1. Content-Disposition filename / filename*
2. charset-aware decode
3. local_web_fetcher.js 放开 stylesheet、不删 banner、加 title
4. trafilatura extract + baseline + html2txt fallback
```

三条补充修正已经吸收到上述四项内部：

```text
1. charset header 解码失败后继续尝试 HTML meta。
2. 文件名已有不受支持后缀时使用 stem + fallback_suffix，避免 report.exe.pdf。
3. ContentProcessor 成功日志不固定写 extractor="trafilatura"。
```

---

# 七、最终结论

本轮执行边界保持不变：

```text
不改架构
不新增依赖
不做开发者诊断优化
不改模型可见协议
不让 web_fetch 回流 document_parse
```

最终落地后，用户侧应获得这些直接收益：

```text
1. 文档直链文件名更准确。
2. 文档直链后缀更符合真实 MIME。
3. 中文网页乱码减少。
4. 浏览器兜底抓取正文更完整。
5. HTML 正文抽取成功率更高。
6. 输出行为不引入新的模型可见字段。
```
