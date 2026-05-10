下面这份文档默认前提是：**上一轮已经把 `common.py` 的问题处理掉了**，也就是有副作用的 session/page guard 已经迁移到类似 `action_guards.py`，response helper 已经迁移到类似 `action_responses.py`。

这轮只处理剩下的命名问题，核心目标是：**降低 grep / import / review 时的认知磨损，不改变任何行为。**

````md
# browse_interact 命名收敛重构执行文档

## 背景

上一轮已经解决 `common.py` 的问题：

- 有副作用的 session/page 前置条件逻辑已从 common 中移出。
- action 内部 response/session_state helper 已独立。
- `common.py` 不再作为万能垃圾袋存在。

本轮继续处理剩余命名问题：

1. `models.py` 名字过泛，且与 `browser_profile/models.py` 重名。
2. `errors.py` 名字过泛，实际是工具错误协议。
3. `responses.py` 容易误解成 HTTP response 层。
4. `intervention.py` 名字过宽，实际是 intervention detector。
5. `snapshot.py` 名字过宽，实际是 snapshot lifecycle / manager。
6. `constants.py` 与 `browser_profile/constants.py` 重名。
7. `utils.py` 是万能抽屉，里面混有 URL 脱敏和 keyboard input 处理。
8. `browser_profile/models.py` / `browser_profile/constants.py` 与 interact 层重名，存在长期认知磨损。

本轮目标是纯命名重构：

```text
只改文件名、import、少量模块注释。
不改协议。
不改行为。
不改 action 流程。
不改 snapshot/ref 生命周期。
不改 session 生命周期。
不改 browser_profile resolver 逻辑。
````

---

# 一、最终命名方案

## browser_interact 目录

### 当前可能结构

```text
browser_interact/
  tool.py
  dispatcher.py
  actions.py
  action_contracts.py
  action_guards.py
  action_responses.py
  models.py
  errors.py
  responses.py
  constants.py
  utils.py
  snapshot.py
  snapshot_script.js
  intervention.py
  session.py
```

### 目标结构

```text
browser_interact/
  tool.py
  dispatcher.py
  actions.py

  action_contracts.py
  action_guards.py
  action_responses.py

  protocol_models.py
  tool_errors.py
  tool_results.py
  interact_constants.py

  keyboard_input.py
  url_safety.py

  snapshot_manager.py
  snapshot_script.js
  intervention_detector.py
  session.py
```

---

## browser_profile 目录

### 当前可能结构

```text
browser_profile/
  __init__.py
  constants.py
  catalog.py
  models.py
  paths.py
  checker.py
  config.py
  resolver.py
  presenter.py
```

### 目标结构

```text
browser_profile/
  __init__.py
  profile_constants.py
  catalog.py
  profile_models.py
  paths.py
  checker.py
  config.py
  resolver.py
  presenter.py
```

只改最容易和 interact 层混淆的两个文件：

```text
constants.py -> profile_constants.py
models.py    -> profile_models.py
```

不建议现在把整个目录 `browser_profile/` 改成 `automation_profile/`。
原因是目录级重命名波及面更大，而且 `browser_profile` 目前仍然可以表达“自动化浏览器 profile 管理”这个大概念。

---

# 二、重命名映射表

## browser_interact 层

| 旧文件               | 新文件                                      | 原因                                                |
| ----------------- | ---------------------------------------- | ------------------------------------------------- |
| `models.py`       | `protocol_models.py`                     | 这里定义的是 agent tool 协议模型，不是普通业务模型                   |
| `errors.py`       | `tool_errors.py`                         | 这里定义的是工具错误协议，不是通用异常体系                             |
| `responses.py`    | `tool_results.py`                        | 这里构造的是工具返回给 agent 的结果 JSON，不是 HTTP response       |
| `constants.py`    | `interact_constants.py`                  | 避免和 profile constants 混淆                          |
| `utils.py`        | 拆成 `keyboard_input.py` / `url_safety.py` | 避免万能 utils 抽屉                                     |
| `snapshot.py`     | `snapshot_manager.py`                    | 文件实际管理 snapshot/ref 生命周期                          |
| `intervention.py` | `intervention_detector.py`               | 文件实际只负责 best-effort intervention signal detection |

---

## browser_profile 层

| 旧文件            | 新文件                    | 原因                             |
| -------------- | ---------------------- | ------------------------------ |
| `models.py`    | `profile_models.py`    | 避免和 `protocol_models.py` 混淆    |
| `constants.py` | `profile_constants.py` | 避免和 `interact_constants.py` 混淆 |

---

# 三、每个文件的新职责说明

## `protocol_models.py`

负责 browse_interact 工具协议中使用的数据结构：

```text
PageState
SessionState
RecoveryHint
UserActionRequest
ToolError
SnapshotPayload
InterventionSignal
ActionResult
```

它不负责：

```text
Playwright session 管理
browser profile 数据模型
错误工厂函数
JSON 序列化
```

---

## `tool_errors.py`

负责构造 agent-facing tool error：

```text
ErrorCategory
ToolErrorCode
make_session_error
make_schema_error
make_action_failed_error
make_user_intervention_error_from_signal
make_browser_unavailable_error
make_internal_error
```

它不负责：

```text
Python exception 定义集合
日志格式化
HTTP 错误
Playwright 原始异常透传
```

---

## `tool_results.py`

负责把工具执行结果序列化为 JSON string：

```text
build_success_response
build_error_response
get_page_state
get_session_state
```

它不负责：

```text
HTTP response
FastAPI response
业务 action 执行
错误分类决策
```

---

## `interact_constants.py`

负责 browser_interact runtime 常量：

```text
SCROLL_STEP_PX
FILL_FOCUS_WAIT_MS
SETTLE_WAIT_MS
WAIT_DURATION_MAX_S
SCREENSHOT_JPEG_QUALITY
NAVIGATION_TIMEOUT_MS
SESSION_ID_LENGTH
SNAPSHOT_ID_LENGTH
SNAPSHOT_DEFAULT_MODE
SNAPSHOT_DEFAULT_LIMIT
SNAPSHOT_FOCUSED_DEFAULT_LIMIT
SNAPSHOT_MAX_LIMIT
AUTH_PAGE_INDICATORS
```

它不放 browser_profile 的路径、配置文件名、channel catalog。

---

## `keyboard_input.py`

负责键盘输入解析和标准化：

```text
split_keys
normalize_key
normalize_keys
_KEY_SYNONYMS
```

它不负责：

```text
URL 脱敏
日志安全
Playwright keyboard 操作本身
```

---

## `url_safety.py`

负责 URL 安全展示和脱敏：

```text
redact_url
```

它不负责：

```text
导航
URL 校验
请求发送
```

---

## `snapshot_manager.py`

负责 snapshot/ref 生命周期：

```text
SnapshotManager
SnapshotScriptProtocolError
is_valid_ref
ref_selector
normalize_snapshot_mode
normalize_snapshot_limit
select_snapshot_elements
```

它不负责：

```text
snapshot action handler
click_ref/fill_ref action
session 管理
```

---

## `intervention_detector.py`

负责 best-effort user intervention signal detection：

```text
UserInterventionDetector
_CAPTCHA_DETECTION_JS
```

它返回：

```text
InterventionSignal | None
```

它不直接返回：

```text
ToolError
```

也不承诺可靠检测所有登录、验证码、风控。

---

## `profile_models.py`

负责 browser profile resolver 使用的数据结构：

```text
ProfileDirCheck
ResolveSource
ResolveFailureReason
ResolveSuccess
ResolveFailure
ResolveResult
```

它不定义 browse_interact tool protocol models。

---

## `profile_constants.py`

负责 browser profile 模块常量：

```text
APP_NAME
CONFIG_FILE_NAME
CONFIG_PROFILE_KEY
CONFIG_CHANNEL_KEY
BROWSER_CHANNELS
DEFAULT_BROWSER_CHANNEL
SUPPORTED_PLATFORMS
```

它不放 browser_interact action/runtime 常量。

---

# 四、import 修改规则

## 1. `.models` 替换为 `.protocol_models`

旧：

```python
from .models import (
    ActionResult,
    PageState,
    RecoveryHint,
    SessionState,
    SnapshotPayload,
    ToolError,
)
```

新：

```python
from .protocol_models import (
    ActionResult,
    PageState,
    RecoveryHint,
    SessionState,
    SnapshotPayload,
    ToolError,
)
```

影响文件通常包括：

```text
actions.py
action_responses.py
tool_errors.py
tool_results.py
snapshot_manager.py
intervention_detector.py
```

---

## 2. `.errors` 替换为 `.tool_errors`

旧：

```python
from .errors import (
    ToolErrorCode,
    make_action_failed_error,
    make_schema_error,
    make_session_error,
)
```

新：

```python
from .tool_errors import (
    ToolErrorCode,
    make_action_failed_error,
    make_schema_error,
    make_session_error,
)
```

影响文件通常包括：

```text
actions.py
action_guards.py
dispatcher.py
snapshot_manager.py
intervention_detector.py 不应直接依赖 tool_errors
```

注意：

```text
intervention_detector.py 不应该 import tool_errors。
它只返回 InterventionSignal。
```

---

## 3. `.responses` 替换为 `.tool_results`

旧：

```python
from .responses import (
    build_error_response,
    build_success_response,
    get_page_state,
    get_session_state,
)
```

新：

```python
from .tool_results import (
    build_error_response,
    build_success_response,
    get_page_state,
    get_session_state,
)
```

影响文件通常包括：

```text
actions.py
action_guards.py
action_responses.py
dispatcher.py
```

---

## 4. `.constants` 替换为 `.interact_constants`

旧：

```python
from .constants import (
    FILL_FOCUS_WAIT_MS,
    NAVIGATION_TIMEOUT_MS,
    SCREENSHOT_JPEG_QUALITY,
    SCROLL_STEP_PX,
    SETTLE_WAIT_MS,
    WAIT_DURATION_MAX_S,
)
```

新：

```python
from .interact_constants import (
    FILL_FOCUS_WAIT_MS,
    NAVIGATION_TIMEOUT_MS,
    SCREENSHOT_JPEG_QUALITY,
    SCROLL_STEP_PX,
    SETTLE_WAIT_MS,
    WAIT_DURATION_MAX_S,
)
```

影响文件通常包括：

```text
actions.py
session.py
snapshot_manager.py
intervention_detector.py
tool.py
```

---

## 5. `.utils` 拆分为 `.keyboard_input` 和 `.url_safety`

旧：

```python
from .utils import normalize_keys, redact_url, split_keys
```

新：

```python
from .keyboard_input import normalize_keys, split_keys
from .url_safety import redact_url
```

影响文件通常包括：

```text
actions.py
```

---

## 6. `.snapshot` 替换为 `.snapshot_manager`

旧：

```python
from .snapshot import SnapshotManager, ref_selector
```

新：

```python
from .snapshot_manager import SnapshotManager, ref_selector
```

影响文件通常包括：

```text
actions.py
dispatcher.py
```

---

## 7. `.intervention` 替换为 `.intervention_detector`

旧：

```python
from .intervention import UserInterventionDetector
```

新：

```python
from .intervention_detector import UserInterventionDetector
```

影响文件通常包括：

```text
dispatcher.py
actions.py 视当前 handler 签名而定
```

---

# 五、browser_profile import 修改规则

## 1. `.models` 替换为 `.profile_models`

旧：

```python
from .models import (
    ProfileDirCheck,
    ResolveFailure,
    ResolveFailureReason,
    ResolveResult,
    ResolveSource,
    ResolveSuccess,
)
```

新：

```python
from .profile_models import (
    ProfileDirCheck,
    ResolveFailure,
    ResolveFailureReason,
    ResolveResult,
    ResolveSource,
    ResolveSuccess,
)
```

影响文件通常包括：

```text
checker.py
resolver.py
presenter.py
__init__.py
```

---

## 2. `.constants` 替换为 `.profile_constants`

旧：

```python
from .constants import (
    APP_NAME,
    BROWSER_CHANNELS,
    CONFIG_FILE_NAME,
    DEFAULT_BROWSER_CHANNEL,
)
```

新：

```python
from .profile_constants import (
    APP_NAME,
    BROWSER_CHANNELS,
    CONFIG_FILE_NAME,
    DEFAULT_BROWSER_CHANNEL,
)
```

影响文件通常包括：

```text
paths.py
config.py
resolver.py
__init__.py
```

---

# 六、目标目录结构示例

## browser_interact

```text
browser_interact/
  __init__.py

  tool.py
  dispatcher.py
  actions.py

  action_contracts.py
  action_guards.py
  action_responses.py

  protocol_models.py
  tool_errors.py
  tool_results.py
  interact_constants.py

  keyboard_input.py
  url_safety.py

  snapshot_manager.py
  snapshot_script.js
  intervention_detector.py
  session.py
```

---

## browser_profile

```text
browser_profile/
  __init__.py

  profile_constants.py
  profile_models.py

  catalog.py
  paths.py
  checker.py
  config.py
  resolver.py
  presenter.py
```

---

# 七、模块头部注释建议

## `protocol_models.py`

```python
"""
Data models for the browse_interact agent tool protocol.

These models describe structured tool results, errors, snapshots,
session state, and user intervention signals exchanged with the agent.
They are not browser profile resolver models.
"""
```

---

## `tool_errors.py`

```python
"""
Agent-facing error protocol for browse_interact.

This module converts runtime/session/action failures into structured
ToolError objects with recovery_hint. It should not expose raw Playwright
exceptions directly to the agent.
"""
```

---

## `tool_results.py`

```python
"""
Result serialization for browse_interact.

This module builds the JSON string returned by the tool to the agent.
It is not an HTTP response layer.
"""
```

---

## `snapshot_manager.py`

```python
"""
Snapshot and ref lifecycle management.

This module owns snapshot_id generation, current snapshot validation,
safe ref selector construction, snapshot script execution, and
snapshot mode filtering.
"""
```

---

## `intervention_detector.py`

```python
"""
Best-effort user intervention signal detection.

This detector reports cheap observable signals from URL/title/DOM evidence.
It does not guarantee reliable detection of all login, verification,
CAPTCHA, or anti-bot challenges.
"""
```

---

## `profile_models.py`

```python
"""
Data models for browser automation profile resolution.

These models describe local profile directory checks and resolver outcomes.
They are not browse_interact tool protocol models.
"""
```

---

# 八、执行顺序

## Phase 1：重命名 browser_interact 协议层文件

先执行这几个低风险重命名：

```text
models.py    -> protocol_models.py
errors.py    -> tool_errors.py
responses.py -> tool_results.py
constants.py -> interact_constants.py
```

然后全局更新 import。

优先处理这些文件中的 import：

```text
actions.py
dispatcher.py
action_guards.py
action_responses.py
snapshot_manager.py / 原 snapshot.py
intervention_detector.py / 原 intervention.py
tool.py
session.py
```

---

## Phase 2：重命名 snapshot / intervention 文件

执行：

```text
snapshot.py     -> snapshot_manager.py
intervention.py -> intervention_detector.py
```

然后更新 import：

```text
dispatcher.py
actions.py
```

确保：

```python
from .snapshot_manager import SnapshotManager, ref_selector
from .intervention_detector import UserInterventionDetector
```

---

## Phase 3：拆分 utils.py

将 `utils.py` 拆成：

```text
keyboard_input.py
url_safety.py
```

迁移规则：

```text
split_keys      -> keyboard_input.py
normalize_key   -> keyboard_input.py
normalize_keys  -> keyboard_input.py
_KEY_SYNONYMS   -> keyboard_input.py

redact_url      -> url_safety.py
```

然后删除 `utils.py`。

调用方改为：

```python
from .keyboard_input import normalize_keys, split_keys
from .url_safety import redact_url
```

---

## Phase 4：重命名 browser_profile 的 models/constants

执行：

```text
browser_profile/models.py    -> browser_profile/profile_models.py
browser_profile/constants.py -> browser_profile/profile_constants.py
```

更新：

```text
browser_profile/checker.py
browser_profile/resolver.py
browser_profile/presenter.py
browser_profile/config.py
browser_profile/paths.py
browser_profile/__init__.py
```

---

## Phase 5：更新公开导出

如果 `browser_profile/__init__.py` 当前从旧文件导出：

```python
from .models import ...
from .constants import ...
```

改成：

```python
from .profile_models import ...
from .profile_constants import ...
```

如果 `browser_interact/__init__.py` 存在类似导出，也同步改成新文件名。

---

# 九、保持不变的行为约束

本轮重构必须保持：

```text
1. error.recovery_hint 不变回 recommended_next_action。
2. 错误响应仍然是嵌套 error 对象。
3. SnapshotPayload 字段不变。
4. InterventionSignal 机制不变。
5. UserInterventionDetector 不直接返回 ToolError。
6. fill_ref 成功后仍然不 invalidate snapshot。
7. fill_ref 成功响应仍然只返回 text_length。
8. click_ref 成功后仍然 invalidate snapshot。
9. navigate / go_back / go_forward 成功后仍然 invalidate snapshot。
10. browser_session_id 自动复用逻辑不变。
11. BrowserSessionManager 仍然是单活动 session 设计。
12. browser_profile resolver 行为不变。
```

---

# 十、完成后的全局禁止项

重构完成后，不应该再出现这些 import：

```python
from .models import
from .errors import
from .responses import
from .constants import
from .utils import
from .snapshot import
from .intervention import
from browser_profile.models import
from browser_profile.constants import
```

应该替换为：

```python
from .protocol_models import
from .tool_errors import
from .tool_results import
from .interact_constants import
from .keyboard_input import
from .url_safety import
from .snapshot_manager import
from .intervention_detector import
from browser_profile.profile_models import
from browser_profile.profile_constants import
```

---

# 十一、给 AI 执行时的提示词

```text
请对 browse_interact 和 browser_profile 做一轮纯命名收敛重构。

背景：
上一轮已经处理了 common.py 的职责问题：
- 有副作用的 session/page guard 已迁移到 action_guards.py。
- action response/session_state helper 已迁移到 action_responses.py。
本轮不要再处理 common.py。

本轮目标：
1. models.py 改名为 protocol_models.py。
2. errors.py 改名为 tool_errors.py。
3. responses.py 改名为 tool_results.py。
4. constants.py 改名为 interact_constants.py。
5. snapshot.py 改名为 snapshot_manager.py。
6. intervention.py 改名为 intervention_detector.py。
7. utils.py 拆分为 keyboard_input.py 和 url_safety.py。
8. browser_profile/models.py 改名为 browser_profile/profile_models.py。
9. browser_profile/constants.py 改名为 browser_profile/profile_constants.py。
10. 更新所有 import。
11. 更新相关模块 docstring，说明文件职责。
12. 不修改任何行为逻辑。

utils.py 拆分规则：
- split_keys / normalize_key / normalize_keys / _KEY_SYNONYMS -> keyboard_input.py
- redact_url -> url_safety.py

命名语义：
- protocol_models.py 表示 browse_interact agent tool 协议模型。
- tool_errors.py 表示 agent-facing 工具错误协议。
- tool_results.py 表示工具返回给 agent 的 JSON result 构造层，不是 HTTP response。
- snapshot_manager.py 表示 snapshot_id/ref 生命周期管理。
- intervention_detector.py 表示 best-effort user intervention signal detection。
- interact_constants.py 表示 browse_interact runtime 常量。
- profile_models.py 表示 browser automation profile resolver 的模型。
- profile_constants.py 表示 browser_profile 模块常量。

必须保持：
- 不改变 error.recovery_hint。
- 不恢复 recommended_next_action。
- 不改变 SnapshotPayload。
- 不改变 InterventionSignal。
- 不改变 click_ref / fill_ref / navigate / snapshot 行为。
- 不改变 fill_ref 不 invalidate snapshot 的语义。
- 不改变 click_ref invalidate snapshot 的语义。
- 不改变 session 生命周期。
- 不改变 browser_profile resolver 行为。
- 不引入兼容旧 import 的 re-export 文件。
- 不新增抽象层。
- 不重构 actions.py 结构。

完成后不应该再出现：
from .models import
from .errors import
from .responses import
from .constants import
from .utils import
from .snapshot import
from .intervention import
from browser_profile.models import
from browser_profile.constants import

应该替换为：
from .protocol_models import
from .tool_errors import
from .tool_results import
from .interact_constants import
from .keyboard_input import
from .url_safety import
from .snapshot_manager import
from .intervention_detector import
from browser_profile.profile_models import
from browser_profile.profile_constants import
```

```
```
