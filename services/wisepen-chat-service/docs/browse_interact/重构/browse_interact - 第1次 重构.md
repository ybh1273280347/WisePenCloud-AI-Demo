# browse_interact - 第1次 重构

> 从 `browser_tools - 第1次 重构.md` 拆分而来。本文只保留 BrowseInteractTool 相关的架构决策、实现变更和验证结果。

## 一、BrowseInteractTool — 浏览器交互工具

### 1.1 架构决策

| 决策 | 说明 |
|------|------|
| 移除 Steel 远端执行器 | 仅保留本地 Playwright（Chromium），消除双执行路径的代码冗余 |
| 删除全部像素坐标操作 | 仅保留 snapshot + ref 范式，Agent 不再猜测坐标 |
| `actions` 数组 → `action` 单对象 | 从结构上强制每次调用只执行一个 action，避免批量操作中一个失败导致后续全部跳过 |
| `_last_snapshot` / `_last_screenshot` 提升为实例属性 | 每次 `execute` 结束时 Agent 拿到的始终是最新快照，消除多 action 场景下 snapshot 丢失导致的 Agent 幻觉 |

### 1.2 参数 Schema：actions → action

```python
# 旧代码：actions 数组，AI 可批量传入多个 action
"actions": {
    "type": "array",
    "items": { "type": "object", "properties": { "type": {...}, "ref": {...}, ... } }
}

# 新代码：action 单对象，结构上强制单步操作
"action": {
    "type": "object",
    "properties": { "type": {...}, "url": {...}, "ref": {...}, ... }
}
```

改进点：
- AI 无法再批量传入 `fill_ref + click_ref`，杜绝一个失败导致后续全部跳过的问题
- `url` 从顶层参数移入 `action.url`（navigate 专用），语义更内聚
- 移除 `screenshot` 布尔参数，有独立的 `screenshot` action type

### 1.3 会话管理重构

旧代码将会话复用判断散落在 `_ensure_local_page` 里，重构后拆分为三个语义化方法：

| 方法 | 职责 |
|------|------|
| `_is_session_reusable(session_id)` | 三条件合一：page 存在 + session_id 匹配 + 页面存活 |
| `_validate_page_alive()` | 执行 `page.evaluate("() => 1")` 验证连接，检测登录重定向（`_REDIRECT_INDICATORS`） |
| `_cleanup_local()` | 循环化清理 page / browser / playwright，每步异常独立日志 |

会话保持改进：action 执行失败时不再无条件销毁会话，先通过 `_validate_page_alive()` 检查页面是否存活，仅在页面确实不可用时才重置 session。

### 1.4 Action Pipeline

所有 action 通过 `self._action_handlers` 字典分发，新增 action 只需在 `__init__` 中注册 + 写对应方法：

```python
self._action_handlers = {
    "navigate":    self._navigate,
    "go_back":     self._go_back,
    "go_forward":  self._go_forward,
    "snapshot":    self._snapshot_ref,
    "click_ref":   self._click_ref,
    "fill_ref":    self._fill_ref,
    "scroll":      self._scroll,
    "key":         self._key,
    "wait":        self._wait,
}
```

由于 `actions` 数组改为 `action` 单对象，`_run_actions` 循环简化为 `_run_action` 单次执行，消除了循环逻辑和 `screenshot` 附加标记等复杂度。

### 1.5 fill_ref 简化

```python
# 旧代码：两阶段填充，清空时可能触发校验错误态
await el.fill("")
await el.fill(text)

# 新代码：信任 Playwright 原生 fill 的自动清空行为
await el.click()
await page.wait_for_timeout(_FILL_FOCUS_WAIT_MS)
await el.fill(text)
await page.keyboard.press("Escape")  # 通用关闭联想菜单
```

### 1.6 快照脚本改进

| 改进点 | 旧行为 | 新行为 |
|--------|--------|--------|
| 隐藏 input 过滤 | `type="hidden"` 的 input 被识别为 textbox | `getRole` 对 `type="hidden"` 返回空字符串，直接跳过 |
| 非标准元素过滤 | `tabIndex >= 0` 的 `<div>` / `<span>` 等被收录 | `getRole` 对非标准元素返回空字符串，只收录有明确 role 的元素 |
| 可见性前置检查 | 不可见元素仍被收录并标记 `[disabled]` | `isVisible()` 前置检查，不可见元素直接跳过不收录 |
| 可点击性检查 | `isActuallyInteractive` 决定是否收录 | `isClickable` 仅决定是否加 `[disabled]` 标记，放宽 `elementFromPoint` 检查允许父子元素重叠 |
| `getLabel` 优先级链 | 不完整 | `aria-label → placeholder → title → name → aria-labelledby → <label for> → 自身文本` |
| 常量提取 | 硬编码在 JS 字符串内 | `_LABEL_MAX_LENGTH` 等模块级常量，调参无需钻进 JS |
| 脚本位置 | 类定义前 | 模块末尾，阅读者先看到公开接口 |

效果：百度首页从 93 个元素精简到 36 个，搜索框和按钮清晰可辨，无噪声干扰。

### 1.6 模块级常量

```python
_SCROLL_STEP_PX = 100              # 每次滚动像素数
_FILL_FOCUS_WAIT_MS = 100           # fill 前等待元素获焦的毫秒数
_SCREENSHOT_JPEG_QUALITY = 40       # 截图 JPEG 压缩质量 (1-100)
_NAVIGATION_TIMEOUT_MS = 60000      # 页面导航超时（毫秒）
_SESSION_ID_LENGTH = 12             # 会话标识符截取长度
_LABEL_MAX_LENGTH = 80              # 快照中元素标签最大字符数
_FILL_LOG_TEXT_MAX_LENGTH = 40      # fill 操作日志中文本截断长度
_REDIRECT_INDICATORS = ("login.", "accounts.")  # 判定会话被重定向到登录页的关键词
```

---

## 文件变更清单

| 文件 | 变更 |
|------|------|
| 	ools/browse_interact_tool.py | 全面重构：本地 Playwright、单 action schema、会话管理、snapshot/ref 管线和 fill_ref 简化。 |

