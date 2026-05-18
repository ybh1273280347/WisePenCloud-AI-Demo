# Document Tool Protocol

本文档用于团队协作对齐 WisePen Chat Service 的 document 工具体系和文件链路协议。重点是协议边界、调用链、安全约束、错误模型、测试与运维协作方式，不重新定义 PDF/DOCX/Markdown 的 parse/export 算法策略。

## 1. 协议目标

document 系统的目标是把用户侧的外部输入统一摄取为服务器内部临时文件，再由文档工具按统一协议处理。

核心链路：

```text
attachment / URL / upload
  -> server temp file
  -> file_ref
  -> DocumentTempFileResolver(user_id, session_id)
  -> document_convert / document_parse / document_export
  -> output temp file
  -> download_ref
  -> user download
```

必须保持的协议事实：

- `file_ref` 是服务器内部临时文件路径。
- `file_ref` 指向的文件必须真实存在、实际可读。
- `file_ref` 不是 `attachment_ref`。
- `file_ref` 不是 `download_ref`。
- `attachment_ref` 只能在附件摄取和 `attachment_read` 阶段出现。
- URL 只能在 web 摄取阶段出现。
- document 工具不直接下载 URL。
- document 工具不直接读取 `attachment_ref`。
- document 工具读取文件前必须经过 `DocumentTempFileResolver`。
- 用户侧响应不得暴露服务器绝对路径。
- 开发日志可以记录内部路径、`user_id`、`session_id` 和失败原因。

## 2. 目录结构

统一临时根目录：

```text
DOCUMENT_TEMP_FILE_ROOT=/tmp/wisepen-chat-upload-files
```

会话级目录：

```text
/tmp/wisepen-chat-upload-files/
  {user_id}/
    {session_id}/
      {safe_file_id}-{sanitized_original_filename}
      outputs/
        {safe_output_file_id}-{sanitized_output_file_name}
      .in_progress/
        {operation_id}.lock
```

实现位置：

- `src/chat/application/document_temp_files.py`
- `src/chat/application/tools/services/document_file/config.py`
- `src/chat/application/tools/services/document_file/pathing.py`
- `src/chat/application/tools/common/file_handoff/store.py`
- `src/chat/application/document_export/service.py`

协作约定：

- 新增任何文档输入路径时，必须写入上述目录结构。
- 不允许按单独 `session_id` 存储文件。
- 不允许多个用户或多个 session 共享同一目录。
- 文件名必须 sanitize。
- 文件名前必须有随机 `safe_file_id`，避免冲突和猜测。

## 3. 核心概念

### 3.1 attachment_ref

`attachment_ref` 是用户会话内附件的外部引用。它用于定位用户上传的附件，但不能直接交给 document 工具。

允许出现的位置：

- `attachment_read` 工具入参。
- 附件 resolver。
- 附件读取服务内部日志和中间对象。

禁止出现的位置：

- `document_parse` 入参。
- `document_convert` 入参。
- `document_export` 文件输入。

### 3.2 file_ref

`file_ref` 是服务器内部临时文件路径，是 document 工具读取文件的唯一事实来源。

要求：

- 路径必须落在 `DOCUMENT_TEMP_FILE_ROOT/{user_id}/{session_id}/` 内。
- 文件必须存在。
- 文件必须是普通文件。
- 文件必须可读。
- 文件大小不能超过当前上传和解析限制。
- 读取前必须通过 `DocumentTempFileResolver.resolve(file_ref, user_id, session_id)`。

协作注意：

- `file_ref` 可以作为工具间内部交接值。
- 不应要求终端用户理解或手工区分 `file_ref`、`attachment_ref`、`download_ref`。
- 对用户展示时，应展示文件名或下载链接，不展示服务器绝对路径。

### 3.3 download_ref

`download_ref` 是生成文件的下载引用，不是输入文件引用。

格式：

```text
{user_id}/{session_id}/{storage_file_name}
```

要求：

- 必须绑定当前 `user_id`。
- 必须绑定当前 `session_id`。
- 必须指向当前 user/session 的 `outputs/` 下文件。
- 不暴露服务器绝对路径。
- 下载接口必须校验当前登录用户和 session 权限。

实现位置：

- `src/chat/application/document_export/download_reference.py`
- `src/chat/application/document_export/download_resolver.py`
- `src/chat/api/endpoints/document_export.py`
- `src/chat/api/endpoints/chat_file.py`

## 4. 输入摄取链路

### 4.1 聊天文件上传

入口：

- `src/chat/api/endpoints/chat_file.py`

职责：

- 校验当前登录用户。
- 校验 session 属于当前用户。
- 按流式方式接收上传内容。
- 写入 `DOCUMENT_TEMP_FILE_ROOT/{user_id}/{session_id}/`。
- 返回 `file_ref`、展示文件名、content type、大小和预览/下载 URL。

安全要求：

- `file_name` 必须 sanitize。
- `file_id` 必须随机生成。
- preview/delete/list 只能访问当前 user/session 目录。
- 预览解析路径必须使用 `Path.resolve()` 后再用 `relative_to()` 做作用域校验。

### 4.2 attachment_read

入口：

- `src/chat/application/tools/attachment/attachment_read_tool.py`
- `src/chat/application/tools/services/attachment_read/service.py`

职责：

- 接收 `attachment_ref`。
- 通过附件 resolver 找到附件文件。
- 对文本附件直接读取和缓存内容。
- 对图片执行 OCR，并保留可视觉分析的 image reference。
- 对 PDF/DOCX/PPTX/XLSX 等二进制文档，复制到 server temp file，生成 `file_ref`。

协议边界：

- `attachment_read` 是附件到 `file_ref` 的摄取层。
- `document_parse` 和 `document_convert` 不接受 `attachment_ref`。
- 二进制文档必须先落盘成当前 user/session 下的真实文件。

### 4.3 web_fetch

入口：

- `src/chat/application/tools/web/web_fetch_tool.py`

职责：

- 抓取 URL。
- 对网页文本生成 cached content。
- 对 PDF 等文档响应写入 server temp file，生成 `file_ref`。

协议边界：

- `web_fetch` 可以处理 URL。
- document 工具不处理 URL。
- URL 下载结果必须落到当前 user/session 目录。

### 4.4 web_crawl

入口：

- `src/chat/application/tools/web/web_crawl_tool.py`
- `src/chat/application/tools/services/web_crawl/service.py`

职责：

- 从 seed URL 抓取页面和站内链接。
- 页面内容走 cached content。
- 发现文档资源时写入 server temp file，生成 `file_ref`。

协议边界：

- `CrawlRequest` 必须带 `user_id` 和 `session_id`。
- crawler 不把 URL 直接传给 document 工具。
- crawler 生成的文档文件同样受 user/session scope 限制。

### 4.5 TemporaryFileHandoffStore

位置：

- `src/chat/application/tools/common/file_handoff/store.py`

职责：

- 为 attachment/web 摄取层统一写入或复制临时文档文件。
- 生成 `file_ref`。
- 统一 sanitize 文件名。
- 统一添加随机文件 ID。
- 调用 cleanup 清理过期 session 目录。

关键方法：

```python
write_bytes(user_id, session_id, filename, content, canonical_suffix, content_type)
copy_file(user_id, session_id, source_path, filename, canonical_suffix, content_type)
```

协作约定：

- 新增摄取入口时必须复用该 store，或实现等价安全语义。
- 不允许绕过该 store 直接把 URL、附件路径或逻辑引用传给 document 工具。

## 5. DocumentTempFileResolver

位置：

- `src/chat/application/tools/services/document_file/resolver.py`

职责：

- document 工具读取文件前的强制安全边界。
- 将 `file_ref + user_id + session_id` 解析为 `ResolvedDocumentSource`。

校验顺序：

1. `file_ref` 非空。
2. `user_id` 非空。
3. `session_id` 非空。
4. `DOCUMENT_TEMP_FILE_ROOT` 存在且是目录。
5. `DOCUMENT_TEMP_FILE_ROOT/{user_id}/{session_id}` 位于 temp root 内。
6. 如果 `file_ref` 是相对路径，则以 session root 为基准解析。
7. 先做 lexical path scope 校验，拦截 `..` 路径穿越。
8. session root 必须存在且是目录。
9. `candidate.resolve(strict=True)` 获取 canonical path。
10. canonical path 必须 `relative_to(session_root)` 成功。
11. symlink 解析后不能逃逸 session root。
12. path 不能是目录。
13. path 必须是普通文件。
14. path 必须可读。
15. 文件大小不能超过限制。

必须使用：

```python
Path.resolve()
Path.relative_to()
```

禁止使用：

```python
str(path).startswith(str(root))
```

原因：

- 字符串前缀判断无法可靠处理 `..`、大小写、路径分隔符和 symlink。
- `resolve()` 会 canonicalize 路径。
- `relative_to()` 明确表达“路径是否在指定根目录下”。

## 6. document_parse

入口：

- `src/chat/application/tools/document/document_parse_tool.py`

服务：

- `src/chat/application/tools/services/document_parse/document_parse_service.py`

职责：

- 接收 `file_refs`。
- 每个 `file_ref` 先通过 `DocumentTempFileResolver`。
- 只把 resolver 后的真实 `Path` 传给 parse service。
- 支持 PDF、DOCX、DOCM、PPTX、PPTM、EPUB、XLSX、XLS、XLSM、ODS。
- 返回 Markdown 文本、页、表格、warnings 和 cached content。

协议边界：

- 不接受 URL。
- 不接受 `attachment_ref`。
- 不接受 `download_ref`。
- 不接受 `content_id`。
- 不负责下载外部资源。
- 不负责生成下载文件。

PDF 约束：

- PDF parse 不得使用 Docling。
- PDF parse 失败不得 fallback 到 Docling。
- PDF 路径保持现有 PyMuPDF/pdfplumber/camelot/OCR 策略，不在协议修复中重写。

协作约定：

- 修改 parse 策略必须另开任务。
- 当前协议任务只允许接入 resolver 和 user/session scope，不改变解析语义。

## 7. document_export

入口：

- `src/chat/application/tools/document/document_export_tool.py`

服务：

- `src/chat/application/document_export/service.py`

职责：

- 将 Markdown-like 内容或 cached content 导出为文件。
- 支持 `markdown`、`html`、`pdf`、`docx`、`txt`。
- 输出写入 `DOCUMENT_TEMP_FILE_ROOT/{user_id}/{session_id}/outputs/`。
- 生成 `GeneratedDocumentFile`。
- 由 formatting 层生成 `download_ref` 和 download URL。

协议边界：

- `document_export` 不是文件读取工具。
- `document_export` 不解析二进制文档。
- 对用户上传的 PDF/DOCX，应先 `document_parse` 或通过 `document_convert` 编排。

输出文件规则：

- `file_name` 是下载展示名来源。
- 实际存储名带随机 ID 前缀。
- `file_name` 后缀如果和 `target_format` 冲突，直接报错。
- 不静默改名。
- 不静默修正 `target_format`。
- 输出文件为空时不应返回成功。

## 8. document_convert

入口：

- `src/chat/application/tools/document/document_convert_tool.py`

服务：

- `src/chat/application/tools/services/document_convert/service.py`

职责：

- 只做协议适配和 parse/export 编排。
- 不重写 parse 算法。
- 不重写 export 算法。
- 不直接下载 URL。
- 不读取 `attachment_ref`。
- 不读取 `download_ref`。

工具入参：

```json
{
  "file_ref": "server temp file path",
  "target_format": "docx",
  "file_name": "optional-output-name.docx"
}
```

内部 normalized request：

```text
source_file_ref = file_ref
output_file_name = file_name
target_format = target_format
user_id = context.user_id
session_id = context.session_id
```

字段语义：

- `file_ref` 是输入文件路径。
- `file_name` 是输出文件名。
- `target_format` 是输出格式。
- 不能用 `file_name` 判断 source format。
- source format 只能来自 resolved source path 或后续明确 metadata/magic detection。

严格校验：

- `target_format` 必须在支持枚举中。
- `file_ref` 必须非空字符串。
- `file_name` 如果传入，必须是非空字符串。
- `file_name` 后缀和 `target_format` 冲突时直接报错。
- 不做非法格式静默修正。
- 不把未知 source format 默认当 Markdown。

## 9. convert 路由矩阵

当前 convert 的职责是选择已验证的 parse/export 路线。

| Source | Target | 路线 | 是否调用 parse |
| --- | --- | --- | --- |
| Markdown | DOCX | resolver -> read text -> document_export | 否 |
| Markdown | PDF | resolver -> read text -> document_export | 否 |
| Markdown | HTML/TXT | resolver -> read text -> document_export | 否 |
| TXT | DOCX/PDF/HTML/Markdown | resolver -> read text -> document_export | 否 |
| HTML | PDF/HTML/DOCX/TXT/Markdown | resolver -> read text -> document_export | 否 |
| PDF | DOCX/HTML/TXT/Markdown/PDF | resolver -> existing document_parse -> existing document_export | 是 |
| DOCX/DOCM | Markdown/HTML/TXT/PDF/DOCX | resolver -> existing document_parse -> existing document_export | 是 |
| PPTX/PPTM | Markdown/HTML/TXT/PDF/DOCX | resolver -> existing document_parse -> existing document_export | 是 |
| XLSX/XLS/XLSM/ODS | Markdown/HTML/TXT/PDF/DOCX | resolver -> existing document_parse -> existing document_export | 是 |
| EPUB | Markdown/HTML/TXT/PDF/DOCX | resolver -> existing document_parse -> existing document_export | 是 |

不支持路线：

- 报 `UnsupportedDocumentRouteError`。
- 不应被包装成文件不可读。
- 不应 fallback 到其他 parser。

文件不可读：

- 报 `UnreadableDocumentRefError` 或 `InvalidDocumentRefError`。
- 不应报 `UnsupportedDocumentRouteError`。

parse 失败：

- 报 `DocumentParseError`。

export 失败：

- 报 `DocumentExportError`。

## 10. download_ref 和下载权限

生成位置：

- `src/chat/application/tools/document/formatting.py`
- `src/chat/application/document_export/download_reference.py`

解析位置：

- `src/chat/application/document_export/download_resolver.py`

下载入口：

- `src/chat/api/endpoints/document_export.py`
- `src/chat/api/endpoints/chat_file.py`

权限规则：

- `download_ref` 必须包含 `user_id`。
- 当前登录用户必须等于 `download_ref` 中的 user segment。
- session repository 必须确认 session 属于当前用户。
- 文件路径必须解析到 `output_root/{user_id}/{session_id}/outputs/`。
- 下载时返回展示文件名，不返回存储绝对路径。

失败规则：

- `download_ref` 格式非法：400。
- 当前用户不匹配：400 或权限错误。
- session 不属于当前用户：由 session repository 拒绝。
- 文件不存在：404。
- 文件生成成功但 download_ref 注册失败：视为 convert/export 失败，不返回成功。

## 11. cleanup 协议

位置：

- `src/chat/application/tools/services/document_file/cleanup.py`
- `src/chat/application/tools/services/document_file/processing.py`

职责：

- 定期清理过期 user/session 临时目录。
- 清理输入文件和输出文件。
- 跳过正在处理的 session。

环境变量：

```text
DOCUMENT_TEMP_FILE_TTL_SECONDS
DOCUMENT_TEMP_FILE_GRACE_SECONDS
DOCUMENT_TEMP_FILE_MAX_BYTES
```

规则：

- 只清理 `DOCUMENT_TEMP_FILE_ROOT` 内部路径。
- 清理粒度是 session directory。
- 使用 canonical path 校验。
- 删除前必须确保 session dir 位于 temp root 内。
- 发现 `.in_progress` 下存在 lock 文件时跳过。
- 清理失败只记录日志，不影响主服务启动或主流程。
- 日志记录扫描目录数、删除目录数、失败目录数、跳过 in-progress 数。

处理中的文件：

- `document_convert` 和 `document_parse` 在执行时会创建 `.in_progress/{operation_id}.lock`。
- cleanup 遇到非空 `.in_progress` 会跳过当前 session。
- 这避免 TTL 到期时删除正在 parse/export 的文件。

## 12. 错误模型

document 链路至少区分以下错误：

| 错误 | 使用场景 |
| --- | --- |
| `InvalidDocumentRefError` | `file_ref` 不在当前 user/session scope 下，或输入引用非法 |
| `UnreadableDocumentRefError` | 文件不存在、目录、不可读、stat 失败、超大小限制 |
| `UnsupportedDocumentFormatError` | source format 或 target format 不支持 |
| `UnsupportedDocumentRouteError` | source -> target 路由不支持 |
| `DocumentParseError` | parse 阶段失败 |
| `DocumentExportError` | export 阶段失败 |
| `DocumentConvertError` | convert 协议适配或业务校验失败 |
| `DocumentDownloadRefError` | download_ref 注册或解析失败 |
| `DocumentInternalError` | 内部状态或未预期异常 |

用户侧原则：

- 不暴露服务器绝对路径。
- 不让用户理解 `file_ref`、`attachment_ref`、`download_ref` 的内部差异。
- 不把内部协议错误伪装成“文件可能损坏”。
- 只有文件确实缺失、权限不足、加密或需要密码时，才引导用户重新提供文件。

开发日志原则：

- 使用项目日志函数：`log_event`、`log_fail`、`log_error`、`log_ok`。
- 不在本链路新增裸 `logger` 用法。
- 日志必须包含 `user_id`、`session_id` 和内部 path。
- 内部异常必须保留 traceback。

## 13. PDF Docling 禁用边界

硬约束：

- PDF parse 不调用 Docling。
- PDF convert 不调用 Docling。
- PDF export 不调用 Docling。
- PDF route 不实例化 `docling.document_converter.DocumentConverter`。
- PyMuPDF/pdfplumber/camelot/OCR 失败后不得 fallback 到 Docling。

当前 Dockerfile 中 Docling 预加载仅能视为既有非 PDF Office parse 依赖准备，不能成为 PDF 路由依赖。

协作约定：

- 任何涉及 PDF parser 的改动，必须单独证明没有引入 Docling。
- 测试中保留“PDF route 不 import/use Docling”的断言。

## 14. Docker 和容器可见性

配置：

```dockerfile
ENV DOCUMENT_TEMP_FILE_ROOT=/tmp/wisepen-chat-upload-files
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
```

Compose volume：

```yaml
volumes:
  - wisepen-document-temp:/tmp/wisepen-chat-upload-files
```

要求：

- 生成 `file_ref` 的服务和 document 工具必须看到同一个 temp root。
- 多容器部署时必须挂载共享 volume，或改成对象存储引用。
- 当前路径必须对容器运行用户可读写。
- Playwright 浏览器目录固定，避免普通源码修改触发重复下载。
- pandoc、Playwright、中文字体仍需在最终运行镜像内。

协作检查点：

- 如果拆出 document worker，必须确认 worker 和 API service 共享 `DOCUMENT_TEMP_FILE_ROOT`。
- 如果改成对象存储，`file_ref` 协议需要重新设计，不能继续冒充本地 path。

## 15. 测试覆盖

协议级测试位置：

- `tests/test_file_handoff.py`
- `tests/application/document_convert/test_document_convert_routes.py`
- `tests/application/document_export/test_download_resolver.py`
- `tests/application/document_export/test_document_convert_tool.py`
- `tests/application/document_export/test_pdf_renderer.py`
- `tests/application/document_parse/test_frozen_instance_error.py`
- `tests/api/test_document_export_download_route.py`
- `tests/test_attachment_read.py`
- `tests/test_attachment_read_tool.py`
- `tests/test_web_crawl.py`

已覆盖重点：

- 文件按 user/session 落盘。
- 文件名 sanitize。
- safe file id 防冲突。
- resolver 成功解析当前 user/session 文件。
- resolver 拒绝不存在文件。
- resolver 拒绝其他 user/session 文件。
- resolver 拒绝路径穿越。
- resolver 拒绝 symlink 逃逸。
- cleanup 跳过 in-progress。
- cleanup 只清理过期 session dir。
- Markdown -> DOCX 不调用 parse。
- `file_name` 不影响 source format 判断。
- output suffix 和 target_format 冲突时报错。
- PDF -> DOCX 调用 existing parse/export。
- PDF route 不使用 Docling。
- download_ref 不暴露服务器绝对路径。
- 用户 A 不能下载用户 B 的 `download_ref`。
- attachment_read 将文档附件转成 `file_ref`。
- web_crawl 将文档 URL 转成 `file_ref`。

建议本地验证命令：

```powershell
$env:OPENBLAS_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
$env:PYTHONPATH='src;..\wisepen-common\src'
.\.venv\Scripts\python.exe -m pytest `
  tests/test_file_handoff.py `
  tests/application/document_convert/test_document_convert_routes.py `
  tests/application/document_export/test_download_resolver.py `
  tests/application/document_export/test_document_convert_tool.py `
  tests/application/document_export/test_pdf_renderer.py `
  tests/application/document_parse/test_frozen_instance_error.py `
  tests/api/test_document_export_download_route.py `
  tests/test_attachment_read.py `
  tests/test_attachment_read_tool.py `
  tests/test_web_crawl.py `
  -q
```

## 16. 团队协作分工

### 后端协议 owner

负责：

- `DocumentTempFileResolver`
- `TemporaryFileHandoffStore`
- cleanup
- download_ref resolver
- API 权限校验

必须关注：

- user/session scope。
- 路径穿越。
- symlink 逃逸。
- 容器路径可见性。
- 错误分类。

### document parse owner

负责：

- PDF/DOCX/PPTX/XLSX/EPUB 等 parse 策略。
- parser 性能和准确率。
- parser 依赖。

必须关注：

- 不绕过 resolver。
- 不在 PDF 路径引入 Docling。
- 不改变协议层错误分类。
- parse 失败应明确上抛 parse error。

### document export owner

负责：

- Markdown/HTML/PDF/DOCX/TXT export。
- pandoc、Playwright、字体依赖。
- output 文件生成。

必须关注：

- 输出目录必须在当前 user/session scope。
- output file name 只影响展示名。
- 空文件不能成功。
- download_ref 注册失败不能成功。

### tool orchestration owner

负责：

- `document_convert` 编排。
- tool schema。
- tool result formatting。

必须关注：

- convert 不接受旧协议输入。
- convert 不用 output file name 判断 source format。
- convert 只接 parse/export，不重写 parse/export。
- 用户侧不暴露内部路径。

### DevOps owner

负责：

- Dockerfile。
- Compose volume。
- runtime user 权限。
- Playwright cache。
- pandoc/字体依赖。

必须关注：

- 多容器共享 temp root。
- `/tmp/wisepen-chat-upload-files` 可读写。
- 普通源码修改不触发 Chromium 重新下载。

## 17. 新功能接入 checklist

新增一个文档输入来源时：

- 是否拿到了当前 `user_id`？
- 是否拿到了当前 `session_id`？
- 是否先写入 `DOCUMENT_TEMP_FILE_ROOT/{user_id}/{session_id}`？
- 文件名是否 sanitize？
- 是否添加随机 safe file id？
- 是否记录 original file name？
- 是否记录 content type 和 size bytes？
- 是否返回真实可读 `file_ref`？
- 是否禁止把 URL 或 attachment_ref 直接传给 document 工具？
- 是否有测试覆盖 user/session 隔离？

新增一个 document 工具时：

- 是否只消费 `file_ref`？
- 是否调用 `DocumentTempFileResolver`？
- 是否拒绝跨 user/session 文件？
- 是否不暴露服务器绝对路径？
- 是否使用统一错误模型？
- 是否使用 `log_event`、`log_fail`、`log_error` 等项目日志函数？

新增一个导出格式时：

- 是否加入 target format 枚举？
- 是否加入 suffix 映射？
- 是否校验 output file name 后缀冲突？
- 是否写入当前 user/session outputs？
- 是否生成可下载的 `download_ref`？
- 是否有 download 权限测试？

## 18. 常见反例

错误做法：

```text
document_convert(attachment_ref="att_xxx", target_format="docx")
```

正确做法：

```text
attachment_read(attachment_ref)
  -> file_ref
document_convert(file_ref, target_format="docx")
```

错误做法：

```text
document_parse(file_refs=["https://example.com/report.pdf"])
```

正确做法：

```text
web_fetch(url)
  -> file_ref
document_parse(file_refs=[file_ref])
```

错误做法：

```text
source_format = Path(output_file_name).suffix
```

正确做法：

```text
source = resolver.resolve(file_ref, user_id, session_id)
source_format = detect_source_format_from_path(source.path)
```

错误做法：

```text
str(path).startswith(str(root))
```

正确做法：

```text
resolved = path.resolve()
resolved.relative_to(root.resolve())
```

## 19. 当前边界和后续演进

当前不做：

- 不重写 PDF parser。
- 不重写 document_export。
- 不引入新的转换依赖。
- 不把 PDF 路径切到 Docling。
- 不支持 object storage file_ref。
- 不保留旧兼容层。

可以后续演进：

- 为 `file_ref` 增加 metadata registry，但 registry 不能替代真实文件存在性。
- 为格式检测引入 magic detection，但不能把未知格式默认为 Markdown。
- 为 cleanup 增加后台定时任务和指标。
- 为 download_ref 增加签名或短 token，但必须继续绑定 user/session。
- 为多容器部署引入对象存储，但必须重新定义协议，不要继续把对象 key 伪装成本地 path。

## 20. 最短协作口径

团队讨论时可以用以下口径对齐：

```text
document 工具体系只消费服务器内部临时文件 file_ref。
所有附件、URL、上传文件必须先摄取到 DOCUMENT_TEMP_FILE_ROOT/{user_id}/{session_id}/。
读取 file_ref 前必须走 DocumentTempFileResolver，并通过 Path.resolve() + relative_to() 校验作用域。
document_convert 只做协议适配和 parse/export 编排，不重写 parse/export 策略。
输出文件写入当前 user/session 的 outputs/，download_ref 绑定 user_id/session_id。
cleanup 按 user/session TTL 清理，并跳过 in_progress。
PDF 路径禁止 Docling。
```
