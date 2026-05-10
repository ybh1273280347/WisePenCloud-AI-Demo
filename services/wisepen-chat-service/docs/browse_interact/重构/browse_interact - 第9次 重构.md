你这个判断是对的。现在的问题不是“拆得不够”，而是**已经从单文件膨胀走向了过度微模块化**。

当前这层：

```text
action_contracts.py
action_guards.py
action_responses.py
dispatcher.py
interact_constants.py
intervention_detector.py
keyboard_input.py
protocol_models.py
session.py
snapshot_manager.py
snapshot_script.js
tool_errors.py
tool_results.py
url_safety.py
```

确实太碎。很多文件不是独立领域，只是把一小段逻辑拆出去，导致维护时需要频繁跳转。

核心原则应该改成：

> **按稳定领域拆文件，不按“函数类别”拆文件。**

---

# 建议最终结构

我建议收敛成这样：

```text
browse_interact/
  __init__.py
  bootstrap.py

  tool.py
  controller.py
  protocol.py
  session.py
  snapshot.py
  snapshot_script.js
  intervention.py
  action_runtime.py

  actions/
    __init__.py
    navigation.py
    ref.py
    observation.py
    control.py
    status.py
```

这个结构比现在干净很多。

---

# 合并映射

## 1. `protocol_models.py + tool_errors.py + tool_results.py -> protocol.py`

这三个文件强相关，都是 **tool 协议层**：

```text
protocol_models.py  数据结构
tool_errors.py      错误构造
tool_results.py     JSON 返回构造
```

它们拆开后，每次看一个 error response 都要跳三个文件。没必要。

合并成：

```text
protocol.py
```

里面放：

```text
PageState
SessionState
RecoveryHint
UserActionRequest
ToolError
SnapshotPayload
InterventionSignal
ActionResult

ErrorCategory
ToolErrorCode
make_xxx_error

build_success_response
build_error_response
get_page_state
get_session_state
```

这是合理的，因为它们共同服务于一个边界：

```text
browse_interact 返回给 agent 的协议
```

---

## 2. `action_guards.py + action_responses.py -> action_runtime.py`

这两个本质上不是两个领域。

```text
action_guards.py     获取 page / session 前置条件
action_responses.py  构造 action handler 响应
```

它们都属于：

```text
action handler 的运行时支撑
```

所以合并成：

```text
action_runtime.py
```

里面放：

```text
get_existing_page_or_error
get_or_create_page_or_error
session_state
action_error_response
```

这样 action handler 只依赖一个支撑模块，不用在 guard / response 之间跳。

---

## 3. `interact_constants.py` 删除

不要再有一个全局常量桶。

常量放到实际拥有它的模块里：

```text
SESSION_ID_LENGTH
  -> session.py

SNAPSHOT_ID_LENGTH
SNAPSHOT_DEFAULT_MODE
SNAPSHOT_FOCUSED_DEFAULT_LIMIT
SNAPSHOT_MAX_LIMIT
  -> snapshot.py

SCROLL_STEP_PX
FILL_FOCUS_WAIT_MS
SETTLE_WAIT_MS
WAIT_DURATION_MAX_S
SCREENSHOT_JPEG_QUALITY
NAVIGATION_TIMEOUT_MS
  -> 对应 actions 文件，或者 action_runtime.py

AUTH_PAGE_INDICATORS
  -> intervention.py
```

全局 constants 文件很容易变成另一个 `utils.py`。

---

## 4. `keyboard_input.py` 删除，合并进 `actions/control.py`

现在 keyboard 相关只服务于：

```text
key action
```

所以放进：

```text
actions/control.py
```

即可。

包括：

```text
KEY_SYNONYMS
split_keys
normalize_key
normalize_keys
```

不用为了三个小函数单独建文件。

---

## 5. `url_safety.py` 删除，合并到需要它的地方

如果 `redact_url` 只在导航失败、日志、错误 context 里用，可以放进：

```text
actions/navigation.py
```

如果多个 action 都用，再放到：

```text
action_runtime.py
```

但不要单独一个 `url_safety.py`，这个文件太薄。

---

## 6. `action_contracts.py` 删除

这个文件如果只是“事实表”，当前收益不高。

因为你已经通过 action 文件分组表达了行为边界：

```text
navigation.py   导航类，会改变页面
ref.py          ref-based 动作
observation.py  页面观察类
control.py      页面控制类
status.py       状态检查
```

再维护一份 `action_contracts.py` 容易变成重复文档，而且和真实 handler 行为漂移。

删除更好。

把关键行为写在对应 action 文件顶部注释里即可，例如：

```text
ref.py:
- click_ref 成功后 invalidate snapshot
- fill_ref 成功后不 invalidate snapshot
```

---

## 7. `dispatcher.py -> controller.py`

你的类叫：

```python
BrowserInteractController
```

那文件名就应该叫：

```text
controller.py
```

`dispatcher.py` 听起来只负责分发，但它现在还持有：

```text
session manager
snapshot manager
intervention detector
content processor
execute lock
```

它已经是 controller，不只是 dispatcher。

---

## 8. `snapshot_manager.py -> snapshot.py`

在新的结构里，`actions/observation.py` 负责 `handle_snapshot`，所以根目录的 `snapshot.py` 不再容易和 action 混淆。

`snapshot.py` 负责：

```text
SnapshotManager
snapshot_id 生命周期
ref_selector
snapshot script 加载
full/focused mode
DOM elements 格式化
```

这个领域就叫 snapshot，够清楚。

---

## 9. `intervention_detector.py -> intervention.py`

这个可以改，也可以不改。

我倾向于改回：

```text
intervention.py
```

因为文件里已经明确只有：

```text
UserInterventionDetector
InterventionSignal 相关检测逻辑
```

而且 `intervention_detector.py` 名字偏长。
不过这个不是最关键的问题，保留也可以。

---

# actions 目录建议

你现在已经分到 actions 包里了，那文件名不需要再带 `_actions` 后缀。

也就是说，建议从：

```text
control_actions.py
navigation_actions.py
observation_actions.py
ref_actions.py
status_actions.py
```

改成：

```text
actions/
  navigation.py
  ref.py
  observation.py
  control.py
  status.py
```

因为父目录已经叫 `actions`，再加 `_actions` 是重复。

对应关系：

```text
actions/navigation.py
  handle_navigate
  handle_go_back
  handle_go_forward

actions/ref.py
  handle_click_ref
  handle_fill_ref

actions/observation.py
  handle_snapshot
  handle_screenshot
  handle_get_content

actions/control.py
  handle_scroll
  handle_key
  handle_wait

actions/status.py
  handle_status
```

`actions/__init__.py` 只做 re-export。

---

# 最终每个文件的职责

```text
tool.py
  BaseTool adapter，tool schema，tool description。

controller.py
  BrowserInteractController，action 分发，execute lock，持有各 manager。

protocol.py
  所有 agent-facing 协议模型、错误构造、结果 JSON 构造。

session.py
  BrowserSessionManager，Playwright persistent context 生命周期。

snapshot.py
  SnapshotManager，ref 生命周期，snapshot script 加载，full/focused snapshot。

snapshot_script.js
  浏览器内 DOM 元信息采集。

intervention.py
  best-effort user intervention signal detection。

action_runtime.py
  action handler 共用运行时支撑：session/page guard、action error response。

actions/navigation.py
  navigate / go_back / go_forward。

actions/ref.py
  click_ref / fill_ref。

actions/observation.py
  snapshot / screenshot / get_content。

actions/control.py
  scroll / key / wait。

actions/status.py
  status。
```

---

# 该删除的文件

```text
action_contracts.py
action_guards.py
action_responses.py
interact_constants.py
keyboard_input.py
url_safety.py
protocol_models.py
tool_errors.py
tool_results.py
```

其中：

```text
action_guards.py + action_responses.py -> action_runtime.py
protocol_models.py + tool_errors.py + tool_results.py -> protocol.py
keyboard_input.py -> actions/control.py
url_safety.py -> actions/navigation.py 或 action_runtime.py
interact_constants.py -> 各自所属模块
action_contracts.py -> 删除
```

---

# 保留的边界

不要把这些合并掉：

```text
session.py
snapshot.py
intervention.py
actions/
```

因为它们是真正稳定的领域边界。

也不要把所有 action 重新塞回一个 `actions.py`。
现在的问题不是 actions 拆错了，而是 core 支撑层拆得太碎。

---

# 给 Codex 的执行提示词

```text
请对 browse_interact 做一轮架构收敛重构。

当前问题：
core 层文件过多，存在过度微模块化。
很多文件只有少量函数，增加跳转成本和维护成本。
本轮目标是合并同一领域内的小文件，减少不必要的模块边界。

目标目录结构：

browse_interact/
  __init__.py
  bootstrap.py

  tool.py
  controller.py
  protocol.py
  session.py
  snapshot.py
  snapshot_script.js
  intervention.py
  action_runtime.py

  actions/
    __init__.py
    navigation.py
    ref.py
    observation.py
    control.py
    status.py

具体合并规则：

1. protocol_models.py + tool_errors.py + tool_results.py 合并为 protocol.py。
   protocol.py 负责：
   - PageState / SessionState / ToolError / SnapshotPayload / ActionResult / InterventionSignal
   - ErrorCategory / ToolErrorCode
   - make_xxx_error
   - build_success_response / build_error_response
   - get_page_state / get_session_state

2. action_guards.py + action_responses.py 合并为 action_runtime.py。
   action_runtime.py 负责：
   - get_existing_page_or_error
   - get_or_create_page_or_error
   - session_state
   - action_error_response

3. dispatcher.py 改名为 controller.py。
   BrowserInteractController 保持原类名。
   更新所有 import。

4. snapshot_manager.py 改名为 snapshot.py。
   SnapshotManager 保持原类名。
   更新所有 import。

5. intervention_detector.py 改名为 intervention.py。
   UserInterventionDetector 保持原类名。
   更新所有 import。

6. 删除 interact_constants.py。
   将常量迁移到实际拥有它们的模块：
   - session 相关常量放 session.py
   - snapshot 相关常量放 snapshot.py
   - intervention 相关常量放 intervention.py
   - action 参数相关常量放对应 actions 文件或 action_runtime.py

7. 删除 keyboard_input.py。
   将 KEY_SYNONYMS / split_keys / normalize_key / normalize_keys 移到 actions/control.py。

8. 删除 url_safety.py。
   将 redact_url 移到 actions/navigation.py。
   如果多个模块需要 redact_url，则移到 action_runtime.py。

9. 删除 action_contracts.py。
   不再维护单独的 action contract 事实表。
   关键 action 行为通过 action 文件顶部注释表达。

10. actions 包文件改名：
   - navigation_actions.py -> actions/navigation.py
   - ref_actions.py -> actions/ref.py
   - observation_actions.py -> actions/observation.py
   - control_actions.py -> actions/control.py
   - status_actions.py -> actions/status.py

11. actions/__init__.py 只做 handler re-export。

必须保持：
- 不改变任何 action 行为。
- 不改变 tool schema。
- 不改变 error.recovery_hint。
- 不恢复 recommended_next_action。
- 不改变 snapshot/ref 生命周期。
- fill_ref 成功后仍然不 invalidate snapshot。
- click_ref 成功后仍然 invalidate snapshot。
- navigate / go_back / go_forward 成功后仍然 invalidate snapshot。
- wait / screenshot / get_content / status 不主动 invalidate snapshot。
- UserInterventionDetector 仍然返回 InterventionSignal。
- 不重新引入 common.py / utils.py。
- 不引入 pipeline.py。
- 不引入多 session manager。
- 不引入 owner_id / session_context_id。
- 不引入调试字段进入 tool output。
- 保留 class 内部 self._xxx，下划线规则不变。
- 模块级导出仍然用 __all__。

完成后不应该再存在：
- action_contracts.py
- action_guards.py
- action_responses.py
- interact_constants.py
- keyboard_input.py
- url_safety.py
- protocol_models.py
- tool_errors.py
- tool_results.py
- dispatcher.py
- snapshot_manager.py
- intervention_detector.py
```

最终判断：

```text
现在应该做的是“收敛”，不是继续拆。
保留真正的领域边界，合并过薄的支撑模块。
```

这个结构会比当前更好维护。
