# browse_interact 第 1 次修复：运行合同与失败恢复

## 背景

本轮修复发生在 `browse_interact` 从旧浏览器交互实现重构到 `snapshot/ref` 模式之后。重构后的代码已经能导入，目录和命名也基本收敛，但运行时暴露出一类更重要的问题：

```text
代码风格正确，但跨边界调用合同被破坏。
```

典型表现包括：

- Playwright persistent context 启动参数放错位置。
- Python 调用 snapshot JS 的协议与 JS 实际返回不一致。
- Locator / ElementHandle 使用方式错误。
- snapshot ref 生命周期不够严格。
- action 失败后缺少 agent 恢复任务所需的上下文。

本轮只修代码行为，不做命名、目录、风格或大抽象调整。

## 修复目标

```text
1. 保证 Playwright API 使用正确。
2. 保证 snapshot JS 与 Python 调用协议一致。
3. 保证 ref 只能来自当前 snapshot。
4. 保证 action failed 不误判 session 失效。
5. 保证失败响应保留必要恢复上下文。
6. 保证本地浏览器网络尽量继承用户电脑环境。
```

## 关键修复

### 1. Persistent Context 启动

修复前风险：

```python
browser = await playwright.chromium.launch(...)
context = await browser.new_context(user_data_dir=...)
```

`browser.new_context()` 不接受 `user_data_dir`。持久化 profile 必须使用：

```python
context = await playwright.chromium.launch_persistent_context(
    user_data_dir=str(user_data_dir),
    ...
)
```

修复后：

- `BrowserSessionManager` 只保存 `BrowserContext`。
- `user_data_dir/channel/headless/args/proxy` 统一传给 `launch_persistent_context()`。
- 启动失败时调用 `close()`，尽力关闭 context 并 stop Playwright。

### 2. Snapshot JS 调用协议

JS 当前返回：

```json
{
  "schemaVersion": 1,
  "elements": []
}
```

Python 侧修复为：

```text
page.evaluate(SNAPSHOT_SCRIPT)
json.loads(raw)
校验 schemaVersion
校验 elements
根据 mode 生成 tree
```

不再调用不存在的 `snapshot.takeSnapshot()`，也不再把 JS 原始 JSON 字符串直接当成 agent-facing tree。

### 3. ref 生命周期和安全

修复后合同：

```text
1. snapshot action 生成 current_snapshot_id。
2. click_ref/fill_ref/select_ref/check_ref 必须携带当前 snapshot_id。
3. 缺失 snapshot_id 返回 SNAPSHOT_REQUIRED。
4. snapshot_id 不匹配返回 STALE_REF。
5. ref 必须匹配 e1/e2/e123。
6. 不允许把外部任意 selector 拼入 CSS selector。
```

`ref_selector()` 只接受 `e[1-9][0-9]*`，降低 selector 注入风险。

### 4. fill_ref 容器下钻

实际运行中出现过：

```text
ref 指向 role=search 的 div 容器，但真正可填写的是内部 input。
```

Playwright `fill()` 只能作用于 input、textarea、select、contenteditable 或允许编辑的 ARIA 元素。

修复后：

- 如果 ref 自身可编辑，直接 fill。
- 如果 ref 是容器，则查找内部第一个可编辑元素。
- 找不到时返回结构化 `ACTION_FAILED`，`diagnostic_code=REF_NOT_FILLABLE`。
- 成功响应仍只返回 `text_length`，不泄漏输入文本。

### 5. action failed 恢复上下文

修复后，`click_ref/fill_ref/select_ref/check_ref` 失败时错误上下文保留：

```json
{
  "action_type": "fill_ref",
  "ref": "e3",
  "snapshot_id": "abc123"
}
```

不返回：

```text
Playwright stack
本地文件路径
内部 DOM selector
调试计数
```

这样 agent 能判断是否需要刷新 snapshot 或换 ref，而不会因为一次动作失败就重启 session。

### 6. 新 tab 处理

`click_ref` 后如果出现新 page：

```text
1. 自动切换到最新 page。
2. 等待 domcontentloaded。
3. invalidate snapshot。
4. 返回新的 page_state。
5. action_result.detail.opened_new_page = true。
```

这能覆盖点击文章、视频、外链时打开新 tab 的常见网站行为。

### 7. 本地网络代理继承

修复点：

- `browse_interact` 启动浏览器时读取 `HTTPS_PROXY/HTTP_PROXY/ALL_PROXY`。
- 同步传递 `NO_PROXY/no_proxy` 到 Playwright proxy bypass。
- 未设置环境代理时不强制覆盖 Chromium 默认系统代理。

同时修复 `web_fetch/local_web_fetcher.js` 曾经强制：

```text
--proxy-server=direct://
```

该参数会绕过代理，导致“用户浏览器能打开 GitHub，但测试打不开”的不一致。

## 非目标

本轮没有做：

```text
1. 多 session manager。
2. owner_id / 多用户隔离。
3. pixel click。
4. 复杂鼠标动作。
5. LLM rerank / embedding rerank。
6. 网站特化适配器。
```

## 验证

已执行：

```bash
python -m py_compile ...
python -m compileall -q services/wisepen-chat-service/src/chat/application/browser_interact ...
node --check services/wisepen-chat-service/src/chat/application/browser_interact/snapshot_script.js
node --check services/wisepen-chat-service/src/chat/application/web_fetch/local_web_fetcher.js
```

未在文档编写阶段重复执行真实浏览器 E2E。

