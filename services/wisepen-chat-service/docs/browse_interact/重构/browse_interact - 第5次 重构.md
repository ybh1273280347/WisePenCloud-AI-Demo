# Browser Interact 模块架构文档

## 一、设计思路

### 1.1 核心问题

让 LLM Agent 操控真实浏览器面临三个核心挑战：

1. **DOM 脆弱性**：CSS 选择器/XPath 在页面变化后立即失效，Agent 无法可靠定位元素
2. **会话持久性**：Agent 需要跨多轮对话保持登录态、Cookie、页面状态
3. **人工干预**：遇到 CAPTCHA、登录页时，Agent 无法自行处理，必须暂停等待用户

### 1.2 解决方案：Snapshot + Ref 模式

本模块的核心设计是 **Snapshot + Ref 模式**，灵感来自 Playwright 的 accessibility snapshot 和浏览器自动化工具的 ref 机制：

```
Agent 操作流程:
  navigate(url) → snapshot() → [获得 ref 列表] → click_ref(ref="e5") → snapshot() → ...
```

**Snapshot** 不是截图，而是一份结构化的 DOM 交互元素清单。每个可交互元素（链接、按钮、输入框等）被注入一个 `data-agent-ref` 属性，值为 `e1`, `e2`, `e3`...。Agent 通过 ref 引用元素，而非脆弱的 CSS 选择器。

**关键约束**：
- ref 仅在当前 DOM 快照有效期内有效（`refs_valid_for: "current_dom_only"`）
- 任何改变 DOM 的操作（navigate、click、scroll、key）都会使快照失效
- 失效后 Agent 必须重新 snapshot 获取新 ref

### 1.3 架构分层

```
┌─────────────────────────────────────────────────┐
│  BrowseInteractTool (tools/)                     │  ← LLM 工具接口层
│  参数校验、schema 定义                            │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│  BrowserInteractController (core/dispatcher.py)  │  ← 调度层
│  动作路由、并发锁、错误统一封装                    │
└────────────────┬────────────────────────────────┘
                 │
     ┌───────────┼───────────┬──────────────┐
     ▼           ▼           ▼              ▼
┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────────┐
│Session  │ │Snapshot │ │Interven-│ │ 12 个 Action │
│Manager  │ │Manager  │ │tion     │ │ Handler      │
│         │ │         │ │Detector │ │              │
│Playwright│ │DOM快照  │ │CAPTCHA/ │ │navigate/click│
│生命周期  │ │ref管理  │ │登录检测 │ │/fill/scroll..│
└────┬────┘ └─────────┘ └─────────┘ └──────────────┘
     │
┌────▼────────────────────────────────────────────┐
│  browser_profile/                                │  ← 浏览器 Profile 管理层
│  跨平台浏览器数据目录解析、持久化、锁定检测        │
└─────────────────────────────────────────────────┘
```

---

## 二、目录结构与文件职责

```text
browser_interact/
├── __init__.py                    # 模块公共导出
├── bootstrap.py                   # 应用启动：解析 profile 并注入 DI 容器
│
├── core/                          # 核心引擎
│   ├── __init__.py                # core 层导出
│   ├── dispatcher.py              # BrowserInteractController：总调度器
│   ├── session.py                 # BrowserSessionManager：Playwright 生命周期
│   ├── models.py                  # 数据模型（不可变 dataclass）
│   ├── errors.py                  # 错误码枚举 + 错误工厂函数
│   ├── constants.py               # 配置常量
│   ├── snapshot.py                # SnapshotManager：DOM 快照 + ref 管理
│   ├── intervention.py            # UserInterventionDetector：人工干预检测
│   ├── responses.py               # 响应构建器（成功/错误 JSON）
│   ├── utils.py                   # 工具函数（按键标准化、URL 脱敏）
│   ├── snapshot_script.js         # 浏览器端 DOM 快照采集脚本
│   │
│   └── actions/                   # 12 个动作处理器
│       ├── __init__.py            # 动作导出
│       ├── common.py              # 公共辅助（页面获取、错误响应）
│       ├── navigation.py          # navigate / go_back / go_forward
│       ├── snapshot_actions.py    # snapshot / screenshot
│       ├── ref_actions.py         # click_ref / fill_ref
│       ├── input_actions.py       # scroll / key / wait
│       ├── content.py             # get_content（页面内容提取）
│       └── status.py              # status（会话状态查询）
│
└── browser_profile/               # 浏览器 Profile 管理
    ├── __init__.py                # profile 层导出
    ├── resolver.py                # BrowserAutomationProfileResolver：核心解析器
    ├── models.py                  # Profile 数据模型
    ├── catalog.py                 # 系统浏览器目录（Chrome/Edge/Chromium 路径）
    ├── checker.py                 # 目录健康检查（存在/可读/可写/锁定）
    ├── config.py                  # 配置持久化（保存/加载 profile 路径）
    ├── paths.py                   # 路径解析工具
    ├── presenter.py               # 结果的人类可读描述
    └── constants.py               # Profile 常量
```

---

## 三、逐文件详解

### 3.1 顶层

#### `__init__.py`
模块公共 API。导出 4 个符号：
- `BrowserInteractController` — 核心控制器
- `WAIT_DURATION_MAX_S` — wait 动作最大时长常量
- `ToolErrorCode` — 错误码枚举
- `setup_browser_automation_profile` — 启动时 profile 装配函数

#### `bootstrap.py` — 启动装配
应用启动时调用。职责：
1. 创建 `BrowserAutomationProfileResolver` 解析可用的浏览器数据目录
2. 将解析结果注入 DI 容器的 `BrowseInteractTool`，覆盖默认构造参数
3. 解析失败时记录日志，使用临时会话模式（不持久化 profile）

---

### 3.2 core/ — 核心引擎

#### `dispatcher.py` — BrowserInteractController（总调度器）

**职责**：模块的单一入口。接收 LLM Agent 的 action 请求，路由到对应 handler，统一封装响应。

**关键设计**：
- **`asyncio.Lock` 串行化**：所有浏览器操作通过 `_execute_lock` 串行执行，避免并发操作同一页面导致状态混乱
- **动作路由表**：`_ACTION_HANDLERS` 字典将 12 种 action type 映射到对应 handler 函数
- **browser_session_id 自动补全**：如果 Agent 未传 session_id 但已有活跃会话，自动使用当前会话 ID
- **错误统一封装**：无效 action、未知 action、schema 错误均返回结构化 `ToolError` JSON，包含 `recommended_next_action` 引导 Agent 恢复

**12 种动作**：

| 动作 | 说明 | 是否使快照失效 |
|------|------|:---:|
| `navigate` | 导航到 URL | ✓ |
| `go_back` | 后退 | ✓ |
| `go_forward` | 前进 | ✓ |
| `snapshot` | 获取 DOM 快照 | — |
| `screenshot` | 页面截图（JPEG base64） | — |
| `click_ref` | 通过 ref 点击元素 | ✓ |
| `fill_ref` | 通过 ref 填充输入框 | — |
| `scroll` | 页面滚动 | ✓ |
| `key` | 键盘按键/组合键 | ✓ |
| `wait` | 等待指定秒数 | — |
| `get_content` | 提取页面文本内容 | — |
| `status` | 查询会话状态 | — |

#### `session.py` — BrowserSessionManager（会话管理）

**职责**：管理 Playwright 浏览器实例的完整生命周期。

**核心设计**：
- **持久化上下文**：使用 `launch_persistent_context` 而非普通 `launch`，浏览器数据（Cookie、LocalStorage、登录态）持久化到磁盘
- **单例会话**：同一时刻只维护一个浏览器会话（`_session`），新建会话前自动清理旧会话
- **会话验证**：`validate_session()` 检查会话是否存在、页面是否关闭、session_id 是否匹配
- **两种页面获取模式**：
  - `get_existing_page()` — 要求会话已存在且 session_id 匹配（用于 click/fill/scroll 等操作）
  - `get_or_create_page()` — 会话不存在时自动创建（用于 navigate）
- **启动参数**：`--disable-infobars`，可选 `--no-sandbox` / `--disable-dev-shm-usage`（Docker 环境）

**数据模型**：
- `BrowserSession` — 持有 `playwright`、`context`、`page`、`automation_user_data_dir`、`browser_channel`
- `BrowserSessionError` — 携带 `ToolErrorCode` 的异常

#### `models.py` — 数据模型

全部使用 `@dataclass(frozen=True)` 不可变模型：

| 模型 | 用途 |
|------|------|
| `PageState` | 页面状态：url、title、readyState、is_closed |
| `SessionState` | 会话状态：browser_session_id、valid、created、reused |
| `RecommendedNextAction` | 错误恢复引导：告诉 Agent 下一步该做什么 |
| `UserActionRequest` | 人工干预请求：要求用户执行的操作 |
| `ToolError` | 结构化错误：code、message、retryable、recommended_next_action |
| `SnapshotPayload` | 快照数据：snapshot_id、DOM tree、refs_valid_for |
| `ActionResult` | 操作结果：type、status、detail |

#### `errors.py` — 错误体系

**`ToolErrorCode` 枚举** — 4 大类 20+ 错误码：

| 类别 | 错误码 | 含义 |
|------|--------|------|
| 动作 | `NO_ACTION` | 未提供 action |
| | `INVALID_ACTION_SCHEMA` | action 参数格式无效 |
| | `UNKNOWN_ACTION` | 未知 action type |
| 会话 | `SESSION_REQUIRED` | 缺少 session_id |
| | `SESSION_NOT_FOUND` | session_id 不存在 |
| | `SESSION_MISMATCH` | session_id 不匹配 |
| | `SESSION_EXPIRED` | 页面已关闭 |
| 导航 | `NAVIGATION_FAILED` | 导航失败 |
| | `NAVIGATION_TIMEOUT` | 导航超时 |
| | `ACTION_TIMEOUT` | 动作超时 |
| 元素 | `SNAPSHOT_REQUIRED` | 需要先 snapshot |
| | `STALE_REF` | ref 已过期 |
| | `REF_NOT_FOUND` | ref 不存在 |
| | `ELEMENT_NOT_VISIBLE` | 元素不可见 |
| | `ELEMENT_DISABLED` | 元素被禁用 |
| | `FILL_FAILED` | 填充失败 |
| | `CLICK_FAILED` | 点击失败 |
| 干预 | `USER_INTERVENTION_REQUIRED` | 需要用户干预 |
| | `AUTH_REQUIRED` | 需要登录 |
| | `CAPTCHA_REQUIRED` | 需要验证码 |
| | `PERMISSION_PROMPT_REQUIRED` | 权限提示 |
| 系统 | `AUTOMATION_PROFILE_LOCKED` | Profile 被锁定 |
| | `BROWSER_LAUNCH_FAILED` | 浏览器启动失败 |
| | `INTERNAL_ERROR` | 内部错误 |

**错误工厂函数**：`make_session_error()`、`make_schema_error()`、`make_snapshot_required_error()`、`make_stale_ref_error()`、`make_ref_not_found_error()`、`make_user_intervention_error()` — 每个函数构造携带 `recommended_next_action` 的完整 `ToolError`，引导 Agent 自动恢复。

#### `constants.py` — 配置常量

| 常量 | 值 | 用途 |
|------|-----|------|
| `SCROLL_STEP_PX` | 100 | 每次滚动像素 |
| `FILL_FOCUS_WAIT_MS` | 100 | 输入框聚焦等待 |
| `SETTLE_WAIT_MS` | 800 | 页面稳定等待 |
| `WAIT_DURATION_MAX_S` | 30 | wait 最大秒数 |
| `SCREENSHOT_JPEG_QUALITY` | 40 | 截图质量 |
| `NAVIGATION_TIMEOUT_MS` | 60000 | 导航超时 |
| `SESSION_ID_LENGTH` | 12 | 会话 ID 长度 |
| `SNAPSHOT_ID_LENGTH` | 8 | 快照 ID 长度 |
| `AUTH_PAGE_INDICATORS` | `("login.", "accounts.", "signin.", "auth.")` | 认证页面 URL 特征 |

#### `snapshot.py` — SnapshotManager（快照管理）

**职责**：管理 DOM 快照的生命周期和 ref 验证。

**核心机制**：
1. **快照采集**：`take(page)` 调用 `page.evaluate()` 执行 `snapshot_script.js`，该脚本遍历 DOM 中所有可交互元素，为每个元素注入 `data-agent-ref="e{N}"` 属性，返回结构化的 DOM 树文本
2. **快照 ID**：每次 snapshot 生成唯一 ID（8 位 hex），Agent 后续操作必须携带此 ID
3. **ref 验证**：`require_current(snapshot_id)` 检查 Agent 提供的 snapshot_id 是否与当前快照一致，不一致则返回 `STALE_REF` 错误
4. **失效机制**：`invalidate()` 清空当前快照 ID，由 navigate/click/scroll/key 等 DOM 变更操作触发
5. **ref 格式**：`e1`, `e2`, `e3`... 正则 `^e[1-9][0-9]*$`
6. **ref → CSS 选择器**：`ref_selector("e5")` → `[data-agent-ref="e5"]`

#### `intervention.py` — UserInterventionDetector（人工干预检测）

**职责**：检测页面是否需要人工介入（登录、验证码）。

**检测策略**：
1. **认证页面检测**：检查 URL 和 title 是否包含 `login.` / `accounts.` / `signin.` / `auth.`
2. **CAPTCHA 检测**：在页面中执行 JS，检查：
   - DOM 中是否存在 `class*="captcha"` / `id*="recaptcha"` / `iframe[src*="hcaptcha"]` 等元素
   - 页面文本是否包含 "verify you are human" / "security check" 等关键词

检测到干预需求时，返回 `requires_user_action=True` 的 `ToolError`，Agent 应暂停并将请求转发给用户。

#### `responses.py` — 响应构建器

**职责**：将内部模型序列化为 Agent 可消费的 JSON 字符串。

- `build_success_response()` — 成功响应，包含 session_state、page_state、action_result、可选的 snapshot/screenshot
- `build_error_response()` — 错误响应，包含 error_code、error_message、retryable、recommended_next_action
- `get_page_state()` — 从 Playwright Page 提取 PageState
- `get_session_state()` — 构造 SessionState

#### `utils.py` — 工具函数

- `redact_url()` — URL 脱敏，去除 query string 和 fragment（用于日志）
- `split_keys()` — 将 `"Ctrl+Shift+R"` 拆分为 `["Ctrl", "Shift", "R"]`
- `normalize_key()` — 按键名标准化（`"ESC"` → `"Escape"`, `"CMD"` → `"Meta"` 等），支持 30+ 同义词
- `normalize_keys()` — 批量标准化

---

### 3.3 core/actions/ — 动作处理器

每个 handler 函数签名统一为：

```python
async def handle_xxx(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    processor: ContentProcessor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
```

#### `common.py` — 公共辅助

- `_session_state()` — 快捷构造 SessionState
- `_action_error_response()` — 快捷构造错误响应
- `_get_existing_page_or_error()` — 获取已有页面，失败返回错误响应（用于 click/fill/scroll 等需要已存在会话的操作）
- `_get_or_create_page_or_error()` — 获取或创建页面（用于 navigate）
- `_selector_or_error_response()` — ref → CSS 选择器转换，无效 ref 返回错误

#### `navigation.py` — 导航动作

- **`handle_navigate`**：导航到 URL。自动补全 `https://`，支持 `wait_until`（domcontentloaded/load/networkidle），导航后等待 800ms 稳定，然后检测人工干预
- **`handle_go_back` / `handle_go_forward`**：历史导航，复用 `_handle_navigation_direction`

#### `snapshot_actions.py` — 快照动作

- **`handle_snapshot`**：调用 `SnapshotManager.take()` 获取 DOM 快照，返回 snapshot_id 和 DOM tree
- **`handle_screenshot`**：JPEG 截图（质量 40%），base64 编码返回

#### `ref_actions.py` — 元素交互

- **`handle_click_ref`**：通过 ref 点击元素。流程：验证 snapshot_id → ref 转选择器 → `query_selector` 定位 → `scroll_into_view_if_needed` → 优先使用 `bounding_box` 中心坐标点击（更稳定），fallback 到 `element.click()` → 等待稳定 → 检测干预
- **`handle_fill_ref`**：通过 ref 填充输入框。使用 `page.locator(selector).first` 定位 → `fill(text)` → 按 Escape 关闭可能的弹窗

#### `input_actions.py` — 输入动作

- **`handle_scroll`**：鼠标滚轮滚动，支持上下左右，每次 `SCROLL_STEP_PX * amount` 像素
- **`handle_key`**：键盘操作。单键直接 `press`，组合键（如 `Ctrl+C`）先逐个 `down` 再逆序 `up`
- **`handle_wait`**：等待指定秒数（上限 `WAIT_DURATION_MAX_S`），等待后检测人工干预

#### `content.py` — 内容提取

- **`handle_get_content`**：获取页面 HTML，通过 `ContentProcessor`（复用 web_fetch 模块的处理器）清洗提取纯文本

#### `status.py` — 状态查询

- **`handle_status`**：返回当前会话状态（是否存在、是否存活），引导 Agent 下一步操作（有会话 → snapshot，无会话 → navigate）

---

### 3.4 browser_profile/ — 浏览器 Profile 管理

#### 设计背景

Playwright 的 `launch_persistent_context` 需要一个 **User Data Directory**——浏览器的数据目录（包含 Cookie、LocalStorage、扩展、登录态等）。这个目录的选择涉及多个考量：

1. **不能直接用系统浏览器目录**：会与用户日常浏览器冲突，且可能被锁定
2. **需要跨平台**：Windows/macOS/Linux 路径不同
3. **支持多种浏览器**：Chrome、Edge、Chromium
4. **支持 CLI 指定**：允许用户通过命令行指定自定义目录
5. **支持持久化记忆**：记住上次使用的目录，下次自动复用

#### `resolver.py` — BrowserAutomationProfileResolver（核心解析器）

**职责**：按优先级解析可用的浏览器 User Data Directory。

**解析优先级**：
```
CLI 指定目录 > 已保存配置（persisted） > 默认工具目录
```

**解析流程**：
1. 平台检查（仅支持 win32/darwin/linux）
2. Channel 标准化（chrome/msedge/chromium）
3. 如果 CLI 指定了目录 → 检查可用性 → 可选持久化保存
4. 否则尝试加载已保存配置 → 检查可用性
5. 否则使用默认工具目录（`~/.WisePenCloud/browser-profiles/{channel}/`）

**安全检查**：
- 目录是否存在、是否可读、是否被浏览器锁定（检测 `SingletonLock` 文件）
- 如果 CLI 指定的是系统浏览器主目录，发出警告

#### `models.py` — Profile 数据模型

- `ProfileDirCheck` — 目录健康检查结果（exists、is_dir、readable、writable、locked、usable）
- `ResolveSource` — 解析来源枚举（CLI / PERSISTED / DEFAULT_PROFILE）
- `ResolveFailureReason` — 失败原因枚举
- `ResolveSuccess` / `ResolveFailure` — 解析结果

#### `catalog.py` — 系统浏览器目录

定义 Chrome、Edge、Chromium 在各平台的默认 User Data 路径：

| 浏览器 | Windows | macOS | Linux |
|--------|---------|-------|-------|
| Chrome | `%LOCALAPPDATA%/Google/Chrome/User Data` | `~/Library/Application Support/Google/Chrome` | `~/.config/google-chrome` |
| Edge | `%LOCALAPPDATA%/Microsoft/Edge/User Data` | `~/Library/Application Support/Microsoft Edge` | `~/.config/microsoft-edge` |
| Chromium | `%LOCALAPPDATA%/Chromium/User Data` | `~/Library/Application Support/Chromium` | `~/.config/chromium` |

#### `checker.py` — 目录健康检查

- `check_profile_dir()` — 全面检查目录：存在性、是否为目录、可读性、可写性（可选探针写入）、是否被锁定
- `is_profile_locked()` — 检测目录中是否存在 `SingletonLock*` 文件（Chromium 系浏览器的锁机制）
- `ensure_directory()` — 安全创建目录（`mkdir -p`）

#### `config.py` — 配置持久化

- `load()` — 从 `~/.config/WisePenCloud/config.json`（Linux）或对应平台路径加载上次保存的 profile 路径和 channel
- `save()` — 保存当前 profile 路径和 channel 到配置文件

#### `paths.py` — 路径工具

- `default_automation_profile_dir()` — 计算默认工具 profile 目录（遵循 XDG 规范）
- `default_config_file()` — 计算配置文件路径
- `find_system_browser_dir()` — 查找系统浏览器 User Data 目录
- `normalize_channel()` — channel 名标准化
- `mask_home()` — 路径脱敏（`/home/user/...` → `~/...`）

#### `presenter.py` — 人类可读描述

将 `ResolveResult` 转换为中文日志消息，用于启动时告知用户使用了哪个 profile。

#### `constants.py` — Profile 常量

- `APP_NAME = "WisePenCloud"`
- `BROWSER_CHANNELS = ("chrome", "msedge", "chromium")`
- `DEFAULT_BROWSER_CHANNEL = "chrome"`
- `SUPPORTED_PLATFORMS = {"win32", "darwin", "linux"}`

---

## 四、数据流全景

```
LLM Agent
  │
  │  { "action": { "type": "navigate", "url": "https://example.com" } }
  ▼
BrowseInteractTool.execute()
  │
  ▼
BrowserInteractController.execute()
  │  asyncio.Lock 串行化
  │  自动补全 browser_session_id
  │  路由到 _ACTION_HANDLERS[type]
  ▼
handle_navigate()
  │  BrowserSessionManager.get_or_create_page()
  │    ├─ BrowserAutomationProfileResolver.resolve()
  │    │    ├─ CLI 指定? → 检查可用性
  │    │    ├─ 已保存配置? → 检查可用性
  │    │    └─ 默认工具目录 → 创建/检查
  │    └─ playwright.chromium.launch_persistent_context()
  │  page.goto(url)
  │  SnapshotManager.invalidate()
  │  UserInterventionDetector.detect()
  ▼
build_success_response()
  │  { "success": true, "browser_session_id": "a1b2c3...", ... }
  ▼
LLM Agent 收到响应
  │
  │  { "action": { "type": "snapshot" }, "browser_session_id": "a1b2c3..." }
  ▼
handle_snapshot()
  │  SnapshotManager.take(page)
  │    └─ page.evaluate(snapshot_script.js)
  │         └─ 遍历 DOM → 注入 data-agent-ref → 返回树结构
  ▼
  { "success": true, "snapshot": { "snapshot_id": "x1y2", "tree": "..." } }
  ▼
LLM Agent 解析 tree，决定点击 ref="e5"
  │
  │  { "action": { "type": "click_ref", "snapshot_id": "x1y2", "ref": "e5" } }
  ▼
handle_click_ref()
  │  SnapshotManager.require_current("x1y2") ✓
  │  ref_selector("e5") → '[data-agent-ref="e5"]'
  │  page.query_selector('[data-agent-ref="e5"]')
  │  element.click()
  │  SnapshotManager.invalidate()
  │  UserInterventionDetector.detect()
  ▼
  { "success": true, "recommended_next_action": { "type": "snapshot" } }
```

---

## 五、关键设计决策

### 5.1 为什么用 `launch_persistent_context` 而非普通 `launch`

普通 `launch` + `new_context` 创建的是临时会话，关闭后所有数据丢失。`launch_persistent_context` 将浏览器数据持久化到磁盘，Agent 的登录态、Cookie、LocalStorage 在服务重启后依然保留。

### 5.2 为什么 Snapshot 后要 invalidate

任何改变 DOM 的操作（导航、点击、滚动、按键）都可能导致之前注入的 `data-agent-ref` 失效（元素被移除、新增元素等）。强制 invalidate 确保 Agent 不会使用过期的 ref。

### 5.3 为什么用 asyncio.Lock 串行化

浏览器是单页面状态机，并发操作会导致不可预期的竞态条件。Lock 确保同一时刻只有一个 action 在执行。

### 5.4 为什么错误响应要带 `recommended_next_action`

LLM Agent 遇到错误时容易"卡住"。`recommended_next_action` 明确告诉 Agent 下一步该做什么（如 `{"type": "snapshot", "reason": "refresh refs before retrying"}`），大幅提高自动恢复率。

### 5.5 为什么 Profile 管理要独立成子模块

Profile 解析涉及跨平台路径、文件系统检查、配置持久化、锁定检测等多个关注点，与浏览器操作逻辑正交。独立子模块保持职责单一，也便于单独测试。