同意。这个规则可以立起来，而且会让代码更清爽：

> **模块级不要用 `_xxx` 表示私有；模块导出边界统一靠 `__all__`。
> 下划线只保留在 class 内部状态/方法，以及 Python 标准 dunder 名称。**

注意：`__all__` 本身是 Python 标准 dunder，允许保留。

下面是可执行清理方案。

````md
# browse_interact 模块级下划线清理执行文档

## 目标

清理 browse_interact 中大量模块级 `_xxx` 命名。

当前问题：

```text
_ALLOWED_SNAPSHOT_MODES
_SNAPSHOT_JS
_REF_PATTERN
_KEY_SYNONYMS
_CAPTCHA_DETECTION_JS
_format_snapshot_line
_score_for_goal
_base_element_score
_resolve_owner_id
_session_state
_action_error_response
_get_existing_page_or_error
````

这些模块级下划线会制造额外认知负担：

```text
1. 文件内满屏 _xxx，可读性差。
2. “私有”边界靠命名暗示，不够清晰。
3. 维护者不知道哪些函数允许跨模块 import。
4. 和 class 内部状态下划线混在一起，语义不干净。
```

本轮目标：

```text
1. 删除模块级单下划线命名。
2. 模块 public API 由 __all__ 显式声明。
3. 未放入 __all__ 的函数即使没有下划线，也视为模块内部实现。
4. class 内部属性和内部方法可以继续使用单下划线。
5. Python 标准 dunder，例如 __all__、__init__，保留。
```

---

# 一、命名规则

## 允许保留下划线的情况

```python
class BrowserSessionManager:
    def __init__(self) -> None:
        self._session = None
        self._lock = asyncio.Lock()

    async def _cleanup_current_session(self) -> None:
        ...
```

允许：

```text
class 内部状态：self._session
class 内部方法：self._cleanup_current_session
Python dunder：__all__, __init__, __name__
```

---

## 不再允许的情况

模块级变量、函数、常量不要使用单下划线：

```python
_ALLOWED_SNAPSHOT_MODES = {"full", "focused"}
_SNAPSHOT_JS = load_snapshot_script()

def _format_snapshot_line(element: dict) -> str:
    ...
```

改成：

```python
ALLOWED_SNAPSHOT_MODES = {"full", "focused"}
SNAPSHOT_SCRIPT = load_snapshot_script()

def format_snapshot_line(element: dict) -> str:
    ...
```

是否允许外部 import，由 `__all__` 控制。

---

# 二、每个模块都显式写 `__all__`

## 示例：`snapshot_manager.py`

```python
__all__ = [
    "SnapshotManager",
    "SnapshotScriptProtocolError",
    "is_valid_ref",
    "ref_selector",
]
```

说明：

```text
只导出真正允许其他模块使用的对象。
其他函数即使没有下划线，也不放进 __all__。
```

例如这些可以不导出：

```text
normalize_snapshot_mode
normalize_snapshot_limit
select_snapshot_elements
format_snapshot_line
score_for_goal
base_element_score
load_snapshot_script
```

它们是模块内部实现，但不再用下划线命名。

---

# 三、推荐重命名映射

## `snapshot_manager.py`

### 常量

```text
_REF_PATTERN                  -> REF_PATTERN
_REF_ATTRIBUTE                -> REF_ATTRIBUTE
_ALLOWED_SNAPSHOT_MODES       -> ALLOWED_SNAPSHOT_MODES
_SNAPSHOT_JS                  -> SNAPSHOT_SCRIPT
SNAPSHOT_SCRIPT_SCHEMA_VERSION 保持不变
```

### 函数

```text
_load_snapshot_script         -> load_snapshot_script
_format_snapshot_line         -> format_snapshot_line
_base_element_score           -> base_element_score
_score_for_goal               -> score_for_goal
_tokenize_goal                -> tokenize_goal
_has_any                      -> has_any
_element_search_text          -> element_search_text
```

### `__all__`

```python
__all__ = [
    "SnapshotManager",
    "SnapshotScriptProtocolError",
    "is_valid_ref",
    "ref_selector",
]
```

---

## `keyboard_input.py`

### 当前可能有

```python
_KEY_SYNONYMS = {...}
```

改成：

```python
KEY_SYNONYMS = {...}
```

### `__all__`

```python
__all__ = [
    "split_keys",
    "normalize_key",
    "normalize_keys",
]
```

`KEY_SYNONYMS` 不放进 `__all__`。

---

## `intervention_detector.py`

### 常量

```text
_CAPTCHA_DETECTION_JS -> CAPTCHA_DETECTION_SCRIPT
```

### `__all__`

```python
__all__ = [
    "UserInterventionDetector",
]
```

---

## `tool.py`

### 当前可能有

```python
_TOOL_DESCRIPTION
_TOOL_SCHEMA
```

改成：

```python
TOOL_DESCRIPTION
TOOL_SCHEMA
```

### `__all__`

```python
__all__ = [
    "BrowseInteractTool",
]
```

`TOOL_DESCRIPTION` 和 `TOOL_SCHEMA` 不放入 `__all__`，除非其他模块确实需要 import。

---

## `dispatcher.py`

如果有：

```python
_ACTION_HANDLERS
_ACTION_HANDLER
```

建议改成：

```python
ACTION_HANDLERS
ActionHandler
```

其中类型别名不用全大写：

```python
ActionHandler = Callable[..., Awaitable[str]]
```

### `__all__`

```python
__all__ = [
    "BrowserInteractController",
]
```

---

## `action_responses.py`

如果当前有：

```python
_session_state
_action_error_response
```

上一轮应该已经改成：

```python
session_state
action_error_response
```

继续保持。

### `__all__`

```python
__all__ = [
    "session_state",
    "action_error_response",
]
```

---

## `action_guards.py`

如果当前有：

```python
_get_existing_page_or_error
_get_or_create_page_or_error
```

改成：

```python
get_existing_page_or_error
get_or_create_page_or_error
```

### `__all__`

```python
__all__ = [
    "get_existing_page_or_error",
    "get_or_create_page_or_error",
]
```

---

## `tool_errors.py`

内部 helper 不用下划线。

例如：

```text
_user_action_type_from_signal     -> user_action_type_from_signal
_user_action_message_from_signal  -> user_action_message_from_signal
_message_from_intervention_signal -> message_from_intervention_signal
```

### `__all__`

```python
__all__ = [
    "ErrorCategory",
    "ToolErrorCode",
    "make_no_action_error",
    "make_schema_error",
    "make_unknown_action_error",
    "make_session_error",
    "make_snapshot_required_error",
    "make_stale_ref_error",
    "make_ref_not_found_error",
    "make_action_failed_error",
    "make_user_intervention_error_from_signal",
    "make_browser_unavailable_error",
    "make_internal_error",
]
```

辅助函数不放入 `__all__`。

---

## `tool_results.py`

如果有：

```python
_error_to_dict
```

改成：

```python
error_to_dict
```

### `__all__`

```python
__all__ = [
    "build_success_response",
    "build_error_response",
    "get_page_state",
    "get_session_state",
]
```

`error_to_dict` 不放入 `__all__`。

---

# 四、actions 包规则

`actions/` 内部所有 handler 文件也遵守相同规则。

## `actions/ref_actions.py`

### 允许导出

```python
__all__ = [
    "handle_click_ref",
    "handle_fill_ref",
]
```

模块内部 helper 不用下划线，但不放进 `__all__`：

```python
def resolve_ref_locator(...):
    ...

def fill_element(...):
    ...
```

---

## `actions/navigation_actions.py`

```python
__all__ = [
    "handle_navigate",
    "handle_go_back",
    "handle_go_forward",
]
```

---

## `actions/observation_actions.py`

```python
__all__ = [
    "handle_snapshot",
    "handle_screenshot",
    "handle_get_content",
]
```

---

## `actions/control_actions.py`

```python
__all__ = [
    "handle_scroll",
    "handle_key",
    "handle_wait",
]
```

---

## `actions/status_actions.py`

```python
__all__ = [
    "handle_status",
]
```

---

## `actions/__init__.py`

只做 re-export：

```python
from .control_actions import handle_key, handle_scroll, handle_wait
from .navigation_actions import handle_go_back, handle_go_forward, handle_navigate
from .observation_actions import (
    handle_get_content,
    handle_screenshot,
    handle_snapshot,
)
from .ref_actions import handle_click_ref, handle_fill_ref
from .status_actions import handle_status

__all__ = [
    "handle_click_ref",
    "handle_fill_ref",
    "handle_get_content",
    "handle_go_back",
    "handle_go_forward",
    "handle_key",
    "handle_navigate",
    "handle_screenshot",
    "handle_scroll",
    "handle_snapshot",
    "handle_status",
    "handle_wait",
]
```

---

# 五、不要误删的内容

## 不要改 class 内部下划线

保留：

```python
self._session
self._snapshot_manager
self._intervention_detector
self._processor
self._execute_lock
```

理由：

```text
class 内部状态使用单下划线是清晰的。
本轮只清理模块级下划线。
```

---

## 不要改 Python dunder

保留：

```python
__all__
__init__
__name__
__file__
```

---

## 不要为了消除下划线改变语义

禁止：

```text
1. 改 action 行为
2. 改 error.recovery_hint
3. 改 snapshot/ref 生命周期
4. 改 session 生命周期
5. 改 fill_ref/click_ref invalidate 语义
6. 改 tool schema
```

---

# 六、执行步骤

## Phase 1：添加 `__all__`

先不重命名，给每个模块补 `__all__`。

优先模块：

```text
tool.py
dispatcher.py
protocol_models.py
tool_errors.py
tool_results.py
interact_constants.py
keyboard_input.py
url_safety.py
snapshot_manager.py
intervention_detector.py
action_guards.py
action_responses.py
actions/*.py
```

---

## Phase 2：重命名模块级常量

把模块级 `_XXX` 改成 `XXX`：

```text
_TOOL_DESCRIPTION       -> TOOL_DESCRIPTION
_TOOL_SCHEMA            -> TOOL_SCHEMA
_ACTION_HANDLERS        -> ACTION_HANDLERS
_ALLOWED_SNAPSHOT_MODES -> ALLOWED_SNAPSHOT_MODES
_SNAPSHOT_JS            -> SNAPSHOT_SCRIPT
_REF_PATTERN            -> REF_PATTERN
_KEY_SYNONYMS           -> KEY_SYNONYMS
_CAPTCHA_DETECTION_JS   -> CAPTCHA_DETECTION_SCRIPT
```

同步修改所有引用。

---

## Phase 3：重命名模块级 helper 函数

把模块级 `_xxx()` 改成 `xxx()`：

```text
_error_to_dict       -> error_to_dict
_format_snapshot_line -> format_snapshot_line
_score_for_goal      -> score_for_goal
_base_element_score  -> base_element_score
_tokenize_goal       -> tokenize_goal
_has_any             -> has_any
_element_search_text -> element_search_text
```

同步修改所有调用。

---

## Phase 4：确认 class 内部不动

不要批量替换 `self._xxx`。

---

# 七、最终风格示例

## 修改前

```python
_ALLOWED_SNAPSHOT_MODES = {"full", "focused"}


def _format_snapshot_line(element: dict[str, Any]) -> str:
    ...


class SnapshotManager:
    def __init__(self) -> None:
        self._current_snapshot_id = None
```

## 修改后

```python
__all__ = [
    "SnapshotManager",
    "SnapshotScriptProtocolError",
    "is_valid_ref",
    "ref_selector",
]

ALLOWED_SNAPSHOT_MODES = {"full", "focused"}


def format_snapshot_line(element: dict[str, Any]) -> str:
    ...


class SnapshotManager:
    def __init__(self) -> None:
        self._current_snapshot_id = None
```

注意：

```text
format_snapshot_line 没有下划线，但不在 __all__。
它仍然是模块内部实现细节。
```

---

# 八、给 Codex 的执行提示词

```text
请对 browse_interact 做一轮模块级下划线命名清理。

背景：
我不希望模块内部充满 _xxx 命名。
本项目约定：
- 模块级不要使用单下划线表示私有。
- 模块导出边界统一通过 __all__ 显式声明。
- 未出现在 __all__ 中的模块级对象，即使没有下划线，也视为模块内部实现。
- class 内部属性和内部方法可以继续使用单下划线。
- Python 标准 dunder，例如 __all__ / __init__，必须保留。

目标：
1. 给 browse_interact 相关模块补充 __all__。
2. 删除模块级变量、常量、函数名前的单下划线。
3. 保留 class 内部 self._xxx 和 class 内部 _method。
4. 不改变任何业务行为。
5. 不改变 tool schema。
6. 不改变 error.recovery_hint。
7. 不改变 snapshot/ref 生命周期。
8. 不改变 click_ref / fill_ref 行为。
9. 不改变 session 生命周期。
10. 不引入新抽象层。

需要重点处理：
- _TOOL_DESCRIPTION -> TOOL_DESCRIPTION
- _TOOL_SCHEMA -> TOOL_SCHEMA
- _ACTION_HANDLERS -> ACTION_HANDLERS
- _ALLOWED_SNAPSHOT_MODES -> ALLOWED_SNAPSHOT_MODES
- _SNAPSHOT_JS -> SNAPSHOT_SCRIPT
- _REF_PATTERN -> REF_PATTERN
- _REF_ATTRIBUTE -> REF_ATTRIBUTE
- _KEY_SYNONYMS -> KEY_SYNONYMS
- _CAPTCHA_DETECTION_JS -> CAPTCHA_DETECTION_SCRIPT
- _error_to_dict -> error_to_dict
- _format_snapshot_line -> format_snapshot_line
- _base_element_score -> base_element_score
- _score_for_goal -> score_for_goal
- _tokenize_goal -> tokenize_goal
- _has_any -> has_any
- _element_search_text -> element_search_text

actions 包也要处理：
- 每个 actions/*.py 文件添加 __all__
- __all__ 只导出 handle_xxx action handler
- actions/__init__.py 只做 re-export
- 不要在 actions 中保留 common.py
- 不要把 helper 放入 __all__，除非其他模块确实需要 import

必须保留：
- class 内部 self._session / self._controller / self._execute_lock 等
- class 内部私有方法
- Python dunder 名称，例如 __all__ / __init__

禁止：
- 不要把 self._xxx 批量改名。
- 不要删除 __all__。
- 不要用 from module import * 替代显式 import。
- 不要修改 action handler 行为。
- 不要修改错误协议。
- 不要修改 snapshot script 行为。
- 不要修改 browser_profile 逻辑。
- 不要引入兼容旧名字的 alias。
- 不要保留模块级 _xxx alias。

完成后：
- 模块级不应再出现 def _xxx。
- 模块级不应再出现 _XXX = ...
- 每个对外模块都有 __all__。
- class 内部下划线保持不变。
```

最终规则可以写进项目规范：

```text
Module-level visibility is controlled by __all__, not by leading underscores.
Leading underscores are reserved for class internals only.
```
