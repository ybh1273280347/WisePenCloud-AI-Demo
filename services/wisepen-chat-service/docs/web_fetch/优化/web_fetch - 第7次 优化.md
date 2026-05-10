# web_fetch 第 7 次优化：多轮追问体验与可诊断性增强

## 背景

本次优化不重写 `web_fetch` 架构，只在现有职责边界上增强体验：

```text
WebFetchTool
    负责参数校验和工具返回截断

FetchCoordinator
    负责抓取链调度、降级、缓存和日志

StaticFetcher
    负责静态 HTTP 抓取和 MIME / 扩展名路由

ContentProcessor / DocumentParser
    负责 HTML / 文本 / 文档内容处理
```

本轮保持抓取器返回协议不变，不引入新依赖，不增加 Redis、数据库或文件缓存。

---

## 改动文件

```text
chat/application/web_fetch/fetch_coordinator.py
chat/application/web_fetch/content_processor.py
chat/application/tools/web_fetch_tool.py
```

`StaticFetcher` 当前已经具备流式读取、响应体大小限制、读取 body 前 Content-Type 预判和扩展名兜底，因此本轮没有继续修改。

---

## 1. FetchCoordinator 增加进程内 TTL 缓存

### 目标

同一 URL 在多轮追问中经常被连续访问，例如：

```text
总结这个网页
提取其中的表格
分析风险因素
翻译重点段落
```

如果每轮都重新抓取，用户感知会明显变慢。

### 实现

缓存放在 `FetchCoordinator` 内部：

```text
key: url + effective_force_browser
value: markdown
ttl: 10 分钟
max_items: 128
```

使用 `OrderedDict` 实现轻量 LRU 行为：

```text
命中后 move_to_end
写入后 move_to_end
超过 max_items 时 popitem(last=False)
```

### 说明

缓存只保存最终 Markdown，不保存原始响应体，也不改变工具返回协议。

---

## 2. 文档 URL 自动跳过浏览器链路

### 目标

当用户或模型传入：

```text
force_browser=True
```

但 URL 实际是：

```text
.pdf / .doc / .docx / .xls / .xlsx / .ppt / .pptx
```

浏览器链路通常没有收益，反而会增加等待。

### 实现

新增文档 URL 判断：

```text
is_document_url(url)
```

当文档 URL 遇到 `force_browser=True` 时，实际调度改为静态链路：

```text
StaticFetcher -> ContentProcessor -> DocumentParser
```

缓存 key 使用 `effective_force_browser`，避免同一个文档 URL 因 `force_browser=True/False` 产生两份缓存。

---

## 3. 抓取链路耗时日志

### 目标

当用户感觉抓取慢时，日志需要能定位慢点：

```text
StaticFetcher 慢
ContentProcessor 慢
SteelFetcher 慢
LocalScriptFetcher 慢
fallback 链路太长
```

### 实现

每个 fetcher 调用都会记录耗时：

```text
elapsed
```

raw 内容还会拆分：

```text
fetch_elapsed
process_elapsed
```

这样可以区分“网络慢”和“解析慢”。

---

## 4. 最后失败原因汇总

### 目标

最终失败时不再只看到：

```text
所有抓取器均失败
```

而是可以看到最后几步失败路径。

### 实现

`FetchCoordinator.fetch()` 内部维护：

```text
failure_reasons
```

记录常见失败：

```text
StaticFetcher: empty content
SteelFetcher: too short length=23 min=400
LocalScriptFetcher: exception=RuntimeError
StaticFetcher: processor failed
```

最终日志只输出最后 5 条，避免日志过长。

---

## 5. XLSX / PPTX 文档输出结构化 Markdown

### XLSX

原先 Sheet 和行内容不够明显。

现在输出为：

````markdown
## Sheet: Sheet1

```tsv
a	b	c
1	2	3
```
````

### PPTX

原先所有 shape 文本容易混在一起。

现在按 slide 分组：

```markdown
## Slide 1

第一页内容

## Slide 2

第二页内容
```

### 说明

PDF 暂不按页分组，避免给长 PDF 额外增加大量标题 token。

---

## 6. WebFetchTool 截断逻辑优化

### 目标

工具层保持薄，只负责最终结果归一化和截断。

### 实现

`normalize_markdown_result()` 现在会：

```text
1. 先 strip
2. 未超过 TOOL_RESULT_MAX_CHARS 时直接返回
3. 超长时预留截断提示长度
4. 优先在段落边界截断
5. 末尾追加 Content truncated due to length
```

这样减少截在半个段落中间的情况。

---

## 风格调整

根据 `docs/code_style.md`：

```text
1. 模块级常量和函数不使用单下划线前缀
2. 不增加宽容式参数转换
3. 不新增配置壳类
4. 不新增伪抽象
```

所以本轮使用：

```text
CACHE_TTL_SECONDS
CACHE_MAX_ITEMS
DOCUMENT_EXTENSIONS
is_document_url
normalize_markdown_result
```

导出边界仍由 `__all__` 控制。

---

## 验收结果

已验证：

```text
1. py_compile 通过
2. 缓存命中路径可用
3. 文档 URL force_browser=True 会走静态链路
4. XLSX 输出包含 ## Sheet 和 tsv fenced block
5. PPTX 输出包含 ## Slide N
6. Markdown 截断保留 Content truncated due to length
7. git diff --check 通过
```

完整 E2E 仍依赖服务、外网和本地浏览器环境，没有在本轮强制执行。

---

## 最终效果

```text
同一 URL 连续追问更快
文档 URL 不再误走浏览器链路
慢点可以通过耗时日志定位
失败时可以看到最后失败路径
XLSX / PPTX 更适合模型理解
超长 Markdown 截断更稳定
```
