````md
# browse_interact 错误协议重构执行文档

## 目标

当前 `browse_interact` 的错误响应里使用了：

```json
{
  "error_code": "STALE_REF",
  "error_message": "...",
  "retryable": true,
  "requires_user_action": false,
  "recommended_next_action": {
    "type": "snapshot",
    "reason": "refresh refs before retrying"
  }
}
````

这个设计的问题是：`recommended_next_action` 容易被理解为“工具知道 agent 的任务级下一步”。但工具层只知道当前工具状态，不知道 agent 的完整任务链。

本次重构目标是把错误协议从：

```text
工具推荐 agent 下一步做什么
```

改成：

```text
工具说明当前工具状态为什么失败，以及恢复该工具状态需要什么前置条件
```

最终改为：

```json
{
  "success": false,
  "browser_session_id": "...",
  "session": {...},
  "page": {...},
  "error": {
    "category": "snapshot",
    "code": "STALE_REF",
    "message": "The provided snapshot_id is stale.",
    "retryable": true,
    "requires_user_action": false,
    "context": {
      "provided_snapshot_id": "old_id"
    },
    "recovery_hint": {
      "type": "refresh_snapshot",
      "scope": "tool_state",
      "required_before_retry": true,
      "reason": "A fresh snapshot is required before retrying ref-based actions."
    }
  }
}
```

---

## 非目标

本次只重构错误协议，不做以下事情：

```text
1. 不拆分 actions.py。
2. 不重构 browser_profile。
3. 不改 snapshot/ref 核心流程。
4. 不改 browser_session_id 机制。
5. 不改 Playwright session 生命周期。
6. 不做本地 runner / WebSocket 通信。
7. 不引入 JSON Schema oneOf。
8. 不改变成功响应的核心字段：success / browser_session_id / session / page / action_result / snapshot。
9. 不把 Playwright 原始异常直接返回给 agent。
```

---

## 核心设计原则

### 1. recovery_hint 不是任务级建议

`recovery_hint` 只描述工具状态恢复要求。

例如：

```json
{
  "type": "refresh_snapshot",
  "required_before_retry": true
}
```

含义是：

```text
如果 agent 还想继续使用 ref-based action，则必须先刷新 snapshot。
```

不是：

```text
agent 当前任务下一步一定应该 snapshot。
```

---

### 2. agent-facing error code 应该收敛

不要把所有内部细节都作为 agent-facing error code。

原来的：

```text
NAVIGATION_FAILED
NAVIGATION_TIMEOUT
FILL_FAILED
CLICK_FAILED
AUTH_REQUIRED
CAPTCHA_REQUIRED
AUTOMATION_PROFILE_LOCKED
BROWSER_LAUNCH_FAILED
```

这些不再作为主要 `error.code` 暴露给 agent。

它们改成：

```text
diagnostic_code
```

主要给日志、调试、维护者使用。

---

### 3. 错误分三层

#### agent-facing category

数量少，表示错误大类：

```text
invalid_request
session
snapshot
action
user_intervention
browser_unavailable
internal
```

#### agent-facing code

数量少，影响 agent 恢复路径：

```text
NO_ACTION
INVALID_ACTION_SCHEMA
UNKNOWN_ACTION

SESSION_REQUIRED
SESSION_NOT_FOUND
SESSION_MISMATCH
SESSION_EXPIRED

SNAPSHOT_REQUIRED
STALE_REF
REF_NOT_FOUND

ACTION_FAILED
USER_INTERVENTION_REQUIRED
BROWSER_UNAVAILABLE
INTERNAL_ERROR
```

#### diagnostic_code

内部诊断：

```text
NAVIGATION_FAILED
NAVIGATION_TIMEOUT
CLICK_FAILED
FILL_FAILED
ELEMENT_DETACHED
PROFILE_LOCKED
PROFILE_UNAVAILABLE
INVALID_BROWSER_CHANNEL
CAPTCHA_DETECTED
AUTH_PAGE_DETECTED
```

---

## 影响文件

本次主要修改：

```text
models.py
errors.py
responses.py
intervention.py
actions.py
session.py
tool.py
测试断言 / 日志解析
```

不应该修改：

```text
snapshot.py
browser_profile/
utils.py
constants.py
dispatcher.py
```

除非 import 需要微调。

---

# Phase 1：修改 `models.py`

## 目标

引入：

```text
RecoveryHint
ToolError.category
ToolError.context
ToolError.diagnostic_code
```

删除或停止使用：

```text
RecommendedNextAction
```

如果当前还有成功响应暂时依赖 `RecommendedNextAction`，可以先保留类定义，但错误协议不再使用它。

## 推荐最终代码

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class PageState:
    url: str
    title: str
    ready_state: Optional[str]
    is_closed: bool


@dataclass(frozen=True)
class SessionState:
    browser_session_id: Optional[str]
    valid: bool
    created: bool = False
    reused: bool = False


@dataclass(frozen=True)
class RecoveryHint:
    type: str
    scope: str = "tool_state"
    required_before_retry: bool = False
    reason: Optional[str] = None


@dataclass(frozen=True)
class UserActionRequest:
    type: str
    message: str
    detected_reason: Optional[str] = None


@dataclass(frozen=True)
class ToolError:
    category: str
    code: str
    message: str
    retryable: bool
    requires_user_action: bool = False
    user_action: Optional[UserActionRequest] = None
    recovery_hint: Optional[RecoveryHint] = None
    context: Dict[str, Any] = field(default_factory=dict)
    diagnostic_code: Optional[str] = None


@dataclass(frozen=True)
class SnapshotPayload:
    snapshot_id: str
    tree: str
    refs_valid_for: str = "current_dom_only"


@dataclass(frozen=True)
class ActionResult:
    type: str
    status: str
    detail: Dict[str, Any]
```

---

# Phase 2：修改 `errors.py`

## 目标

将错误码收敛为少量 agent-facing code，并新增统一错误工厂函数。

## 推荐最终代码骨架

```python
from __future__ import annotations

from enum import Enum
from typing import Any

from .models import RecoveryHint, ToolError, UserActionRequest


class ErrorCategory(str, Enum):
    INVALID_REQUEST = "invalid_request"
    SESSION = "session"
    SNAPSHOT = "snapshot"
    ACTION = "action"
    USER_INTERVENTION = "user_intervention"
    BROWSER_UNAVAILABLE = "browser_unavailable"
    INTERNAL = "internal"


class ToolErrorCode(str, Enum):
    NO_ACTION = "NO_ACTION"
    INVALID_ACTION_SCHEMA = "INVALID_ACTION_SCHEMA"
    UNKNOWN_ACTION = "UNKNOWN_ACTION"

    SESSION_REQUIRED = "SESSION_REQUIRED"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_MISMATCH = "SESSION_MISMATCH"
    SESSION_EXPIRED = "SESSION_EXPIRED"

    SNAPSHOT_REQUIRED = "SNAPSHOT_REQUIRED"
    STALE_REF = "STALE_REF"
    REF_NOT_FOUND = "REF_NOT_FOUND"

    ACTION_FAILED = "ACTION_FAILED"
    USER_INTERVENTION_REQUIRED = "USER_INTERVENTION_REQUIRED"
    BROWSER_UNAVAILABLE = "BROWSER_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"
```

---

## 请求错误

```python
def make_no_action_error() -> ToolError:
    return ToolError(
        category=ErrorCategory.INVALID_REQUEST.value,
        code=ToolErrorCode.NO_ACTION.value,
        message="No action provided. Must specify an action with a 'type' field.",
        retryable=True,
        recovery_hint=RecoveryHint(
            type="fix_request",
            required_before_retry=True,
            reason="Provide an action object with a valid type field.",
        ),
    )


def make_schema_error(
    message: str,
    *,
    context: dict[str, Any] | None = None,
) -> ToolError:
    return ToolError(
        category=ErrorCategory.INVALID_REQUEST.value,
        code=ToolErrorCode.INVALID_ACTION_SCHEMA.value,
        message=message,
        retryable=True,
        context=context or {},
        recovery_hint=RecoveryHint(
            type="fix_request",
            required_before_retry=True,
            reason="Fix the action payload before retrying.",
        ),
    )


def make_unknown_action_error(action_type: str) -> ToolError:
    return ToolError(
        category=ErrorCategory.INVALID_REQUEST.value,
        code=ToolErrorCode.UNKNOWN_ACTION.value,
        message=f"Unknown action type: '{action_type}'",
        retryable=True,
        context={"action_type": action_type},
        recovery_hint=RecoveryHint(
            type="fix_request",
            required_before_retry=True,
            reason="Use one of the supported browse_interact action types.",
        ),
    )
```

---

## session 错误

```python
def make_session_error(
    error_code: ToolErrorCode,
    current_session_id: str | None = None,
) -> ToolError:
    if error_code is ToolErrorCode.SESSION_MISMATCH:
        return ToolError(
            category=ErrorCategory.SESSION.value,
            code=ToolErrorCode.SESSION_MISMATCH.value,
            message="browser_session_id mismatch.",
            retryable=True,
            context={"current_session_id": current_session_id},
            recovery_hint=RecoveryHint(
                type="refresh_status",
                required_before_retry=True,
                reason="Recover the current browser_session_id before retrying session-bound actions.",
            ),
        )

    if error_code is ToolErrorCode.SESSION_EXPIRED:
        return ToolError(
            category=ErrorCategory.SESSION.value,
            code=ToolErrorCode.SESSION_EXPIRED.value,
            message="Browser page has been closed. The session is expired.",
            retryable=True,
            recovery_hint=RecoveryHint(
                type="restart_session",
                required_before_retry=True,
                reason="A new browser session must be created before further page actions.",
            ),
        )

    if error_code is ToolErrorCode.SESSION_REQUIRED:
        return ToolError(
            category=ErrorCategory.SESSION.value,
            code=ToolErrorCode.SESSION_REQUIRED.value,
            message="browser_session_id is required for this action.",
            retryable=True,
            context={"current_session_id": current_session_id},
            recovery_hint=RecoveryHint(
                type="refresh_status" if current_session_id else "start_session",
                required_before_retry=True,
                reason=(
                    "Recover current session state before retrying."
                    if current_session_id
                    else "Start a new browser session before retrying."
                ),
            ),
        )

    if error_code is ToolErrorCode.SESSION_NOT_FOUND:
        return ToolError(
            category=ErrorCategory.SESSION.value,
            code=ToolErrorCode.SESSION_NOT_FOUND.value,
            message="No active browser session matches the provided browser_session_id.",
            retryable=True,
            recovery_hint=RecoveryHint(
                type="start_session",
                required_before_retry=True,
                reason="A new session must be started before continuing.",
            ),
        )

    return make_internal_error(
        message=f"Unexpected session error: {error_code}",
        diagnostic_code="UNEXPECTED_SESSION_ERROR",
    )
```

---

## snapshot/ref 错误

```python
def make_snapshot_required_error() -> ToolError:
    return ToolError(
        category=ErrorCategory.SNAPSHOT.value,
        code=ToolErrorCode.SNAPSHOT_REQUIRED.value,
        message="A current snapshot_id is required for ref-based actions.",
        retryable=True,
        recovery_hint=RecoveryHint(
            type="refresh_snapshot",
            required_before_retry=True,
            reason="A fresh snapshot is required before click_ref or fill_ref.",
        ),
    )


def make_stale_ref_error(snapshot_id: str) -> ToolError:
    return ToolError(
        category=ErrorCategory.SNAPSHOT.value,
        code=ToolErrorCode.STALE_REF.value,
        message="The provided snapshot_id is stale.",
        retryable=True,
        context={"provided_snapshot_id": snapshot_id},
        recovery_hint=RecoveryHint(
            type="refresh_snapshot",
            required_before_retry=True,
            reason="A fresh snapshot is required before retrying ref-based actions.",
        ),
    )


def make_ref_not_found_error(
    ref: str,
    snapshot_id: str | None = None,
) -> ToolError:
    context: dict[str, Any] = {"ref": ref}
    if snapshot_id:
        context["snapshot_id"] = snapshot_id

    return ToolError(
        category=ErrorCategory.SNAPSHOT.value,
        code=ToolErrorCode.REF_NOT_FOUND.value,
        message="The provided ref was not found in the current page.",
        retryable=True,
        context=context,
        recovery_hint=RecoveryHint(
            type="refresh_snapshot",
            required_before_retry=False,
            reason="A fresh snapshot may be needed before choosing another ref.",
        ),
    )
```

---

## action 错误

```python
def make_action_failed_error(
    *,
    action_type: str,
    message: str,
    diagnostic_code: str,
    retryable: bool = True,
    context: dict[str, Any] | None = None,
) -> ToolError:
    return ToolError(
        category=ErrorCategory.ACTION.value,
        code=ToolErrorCode.ACTION_FAILED.value,
        message=message,
        retryable=retryable,
        diagnostic_code=diagnostic_code,
        context={
            "action_type": action_type,
            **(context or {}),
        },
        recovery_hint=RecoveryHint(
            type="inspect_page_state",
            required_before_retry=False,
            reason=(
                "Inspect current page state before deciding whether to retry, "
                "refresh snapshot, or change strategy."
            ),
        ),
    )
```

---

## 用户介入错误

```python
def make_user_intervention_error(
    *,
    detected_reason: str,
    message: str,
    user_action_type: str,
    user_action_message: str,
) -> ToolError:
    return ToolError(
        category=ErrorCategory.USER_INTERVENTION.value,
        code=ToolErrorCode.USER_INTERVENTION_REQUIRED.value,
        message=message,
        retryable=True,
        requires_user_action=True,
        user_action=UserActionRequest(
            type=user_action_type,
            message=user_action_message,
            detected_reason=detected_reason,
        ),
        context={"detected_reason": detected_reason},
        recovery_hint=RecoveryHint(
            type="wait_for_user",
            required_before_retry=True,
            reason=(
                "User must complete the requested action in the visible browser "
                "window before the agent continues."
            ),
        ),
    )
```

---

## browser unavailable / internal

```python
def make_browser_unavailable_error(
    *,
    message: str,
    diagnostic_code: str,
    context: dict[str, Any] | None = None,
) -> ToolError:
    return ToolError(
        category=ErrorCategory.BROWSER_UNAVAILABLE.value,
        code=ToolErrorCode.BROWSER_UNAVAILABLE.value,
        message=message,
        retryable=True,
        requires_user_action=True,
        diagnostic_code=diagnostic_code,
        context=context or {},
        recovery_hint=RecoveryHint(
            type="restart_browser_runtime",
            required_before_retry=True,
            reason=(
                "The browser runtime or automation profile must be restored "
                "before continuing."
            ),
        ),
    )


def make_internal_error(
    *,
    message: str,
    diagnostic_code: str = "INTERNAL_ERROR",
    context: dict[str, Any] | None = None,
) -> ToolError:
    return ToolError(
        category=ErrorCategory.INTERNAL.value,
        code=ToolErrorCode.INTERNAL_ERROR.value,
        message=message,
        retryable=True,
        diagnostic_code=diagnostic_code,
        context=context or {},
        recovery_hint=RecoveryHint(
            type="inspect_status",
            required_before_retry=False,
            reason="Inspect current tool state before deciding whether to retry.",
        ),
    )
```

---

# Phase 3：修改 `responses.py`

## 目标

将错误响应从扁平字段：

```json
{
  "error_code": "...",
  "error_message": "...",
  "recommended_next_action": {...}
}
```

改成嵌套：

```json
{
  "error": {
    "category": "...",
    "code": "...",
    "message": "...",
    "recovery_hint": {...}
  }
}
```

## 修改点

保留 `build_success_response()` 的主要结构。

修改 `build_error_response()`：

```python
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Dict, Optional

from playwright.async_api import Page

from .models import (
    ActionResult,
    PageState,
    SessionState,
    SnapshotPayload,
    ToolError,
)


def build_success_response(
    *,
    session_state: SessionState,
    page_state: Optional[PageState],
    action_result: Optional[ActionResult] = None,
    snapshot: Optional[SnapshotPayload] = None,
    screenshot: Optional[str] = None,
) -> str:
    payload: Dict[str, Any] = {
        "success": True,
        "browser_session_id": session_state.browser_session_id,
        "session": asdict(session_state),
        "page": asdict(page_state) if page_state else None,
    }

    if action_result is not None:
        payload["action_result"] = asdict(action_result)

    if snapshot is not None:
        payload["snapshot"] = asdict(snapshot)

    if screenshot is not None:
        payload["screenshot"] = screenshot

    return json.dumps(payload, ensure_ascii=False)


def build_error_response(
    *,
    session_state: SessionState,
    page_state: Optional[PageState],
    error: ToolError,
) -> str:
    payload: Dict[str, Any] = {
        "success": False,
        "browser_session_id": session_state.browser_session_id,
        "session": asdict(session_state),
        "page": asdict(page_state) if page_state else None,
        "error": _error_to_dict(error),
    }

    return json.dumps(payload, ensure_ascii=False)


def _error_to_dict(error: ToolError) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "category": error.category,
        "code": error.code,
        "message": error.message,
        "retryable": error.retryable,
        "requires_user_action": error.requires_user_action,
    }

    if error.user_action is not None:
        payload["user_action"] = asdict(error.user_action)

    if error.recovery_hint is not None:
        payload["recovery_hint"] = asdict(error.recovery_hint)

    if error.context:
        payload["context"] = error.context

    if error.diagnostic_code is not None:
        payload["diagnostic_code"] = error.diagnostic_code

    return payload
```

`get_page_state()` 和 `get_session_state()` 保持原样。

---

# Phase 4：修改 `intervention.py`

## 目标

不再返回：

```text
AUTH_REQUIRED
CAPTCHA_REQUIRED
```

统一返回：

```text
USER_INTERVENTION_REQUIRED
```

具体原因放在：

```text
user_action.detected_reason
error.context.detected_reason
diagnostic_code 可选
```

## 修改方式

原来：

```python
make_user_intervention_error(
    code=ToolErrorCode.AUTH_REQUIRED,
    ...
)
```

改成：

```python
make_user_intervention_error(
    detected_reason="auth_page",
    message=(
        "Authentication or verification is required "
        "in the visible browser window."
    ),
    user_action_type="login_or_verification",
    user_action_message=(
        "请在打开的浏览器窗口中完成登录或验证，然后让 agent 继续。"
    ),
)
```

captcha：

```python
make_user_intervention_error(
    detected_reason="captcha",
    message="CAPTCHA detected on the page. User intervention required.",
    user_action_type="captcha",
    user_action_message=(
        "请在打开的浏览器窗口中完成验证码，然后让 agent 继续。"
    ),
)
```

---

# Phase 5：修改 `actions.py`

## 目标

替换所有直接构造 `ToolError(...)` 的地方。

优先使用：

```text
make_schema_error
make_session_error
make_ref_not_found_error
make_action_failed_error
make_browser_unavailable_error
make_internal_error
```

---

## import 修改

从：

```python
from .errors import (
    ToolErrorCode,
    make_ref_not_found_error,
    make_schema_error,
    make_session_error,
)
from .models import ActionResult, RecommendedNextAction, ToolError
```

改成：

```python
from .errors import (
    ToolErrorCode,
    make_action_failed_error,
    make_browser_unavailable_error,
    make_internal_error,
    make_no_action_error,
    make_ref_not_found_error,
    make_schema_error,
    make_session_error,
    make_unknown_action_error,
)
from .models import ActionResult, ToolError
```

如果 `make_no_action_error` / `make_unknown_action_error` 只在 dispatcher 用，actions 里不用 import。

---

## navigate 错误

原来：

```python
ToolError(
    code=ToolErrorCode.NAVIGATION_FAILED.value,
    message=f"Navigation failed: {error_message}",
    retryable=True,
    recommended_next_action=RecommendedNextAction(type="status"),
)
```

改成：

```python
make_action_failed_error(
    action_type="navigate",
    message="Navigation failed.",
    diagnostic_code="NAVIGATION_TIMEOUT" if is_timeout else "NAVIGATION_FAILED",
    context={"url": redact_url(url)},
)
```

日志保留详细异常：

```python
log_fail("浏览器导航", error_message, url=redact_url(url))
```

---

## click_ref 错误

原来：

```python
ToolError(
    code=ToolErrorCode.CLICK_FAILED.value,
    message=f"Click failed: {error}",
    retryable=True,
    recommended_next_action=RecommendedNextAction(type="snapshot"),
)
```

改成：

```python
make_action_failed_error(
    action_type="click_ref",
    message="Click action failed.",
    diagnostic_code="CLICK_FAILED",
    context={
        "ref": ref,
        "snapshot_id": snapshot_id,
    },
)
```

---

## fill_ref 错误

原来：

```python
ToolError(
    code=ToolErrorCode.FILL_FAILED.value,
    message=f"Fill failed: {error}",
    retryable=True,
    recommended_next_action=RecommendedNextAction(type="snapshot"),
)
```

改成：

```python
make_action_failed_error(
    action_type="fill_ref",
    message="Fill action failed.",
    diagnostic_code="FILL_FAILED",
    context={
        "ref": ref,
        "snapshot_id": snapshot_id,
    },
)
```

注意：

```text
fill_ref 成功响应仍然只返回 text_length，不返回 text。
fill_ref 成功后仍然不 invalidate snapshot。
```

---

## screenshot / snapshot / scroll / key / wait / get_content 错误

统一用：

```python
make_action_failed_error(...)
```

或者对于纯内部异常：

```python
make_internal_error(...)
```

建议：

```text
snapshot 脚本失败 -> INTERNAL_ERROR + diagnostic_code SNAPSHOT_FAILED
screenshot 失败 -> ACTION_FAILED + diagnostic_code SCREENSHOT_FAILED
scroll 失败 -> ACTION_FAILED + diagnostic_code SCROLL_FAILED
key 失败 -> ACTION_FAILED + diagnostic_code KEY_FAILED
wait 失败 -> ACTION_FAILED + diagnostic_code WAIT_FAILED
get_content 失败 -> ACTION_FAILED + diagnostic_code GET_CONTENT_FAILED
```

---

## profile / browser 启动失败

`_get_or_create_page_or_error()` 里捕获 `BrowserSessionError` 时，从：

```python
ToolError(
    code=error.code.value,
    message=error.message,
    retryable=True,
    recommended_next_action=RecommendedNextAction(type="status"),
)
```

改成：

```python
make_browser_unavailable_error(
    message="Browser runtime is unavailable.",
    diagnostic_code=error.code.value,
)
```

如果希望保留更具体上下文：

```python
make_browser_unavailable_error(
    message=error.message,
    diagnostic_code=error.code.value,
)
```

但注意：不要把敏感本地路径过多暴露给 agent。路径细节更适合日志。

---

# Phase 6：修改 `dispatcher.py`

## 目标

`dispatcher.py` 不再直接构造旧式 `ToolError(...)`。

## 修改点

### no action

从：

```python
ToolError(
    code=ToolErrorCode.NO_ACTION.value,
    message="No action provided...",
    retryable=True,
    recommended_next_action=...
)
```

改成：

```python
make_no_action_error()
```

### invalid schema

从：

```python
ToolError(
    code=ToolErrorCode.INVALID_ACTION_SCHEMA.value,
    ...
)
```

改成：

```python
make_schema_error(message)
```

### unknown action

从：

```python
ToolError(
    code=ToolErrorCode.UNKNOWN_ACTION.value,
    ...
)
```

改成：

```python
make_unknown_action_error(act_type)
```

需要 import：

```python
from .errors import (
    make_no_action_error,
    make_schema_error,
    make_unknown_action_error,
)
```

---

# Phase 7：修改 `session.py`

## 目标

`BrowserSessionError.code` 可以暂时继续使用 `ToolErrorCode`，但它应该只作为内部诊断码。

当前：

```python
raise BrowserSessionError(
    ToolErrorCode.BROWSER_LAUNCH_FAILED,
    ...
)
```

需要改，因为 `ToolErrorCode.BROWSER_LAUNCH_FAILED` 不再存在。

建议新增内部诊断字符串，或者让 `BrowserSessionError.code` 变成 `str`。

## 推荐改法

```python
class BrowserSessionError(Exception):
    def __init__(self, diagnostic_code: str, message: str) -> None:
        super().__init__(message)
        self.diagnostic_code = diagnostic_code
        self.message = message
```

profile failure 映射：

```python
def _map_profile_failure_to_diagnostic_code(
    failure: ResolveFailure,
) -> str:
    if failure.reason is ResolveFailureReason.PROFILE_LOCKED:
        return "PROFILE_LOCKED"

    if failure.reason is ResolveFailureReason.INVALID_BROWSER_CHANNEL:
        return "INVALID_BROWSER_CHANNEL"

    if failure.reason is ResolveFailureReason.PROFILE_UNAVAILABLE:
        return "PROFILE_UNAVAILABLE"

    if failure.reason is ResolveFailureReason.UNSUPPORTED_PLATFORM:
        return "UNSUPPORTED_PLATFORM"

    return "BROWSER_PROFILE_RESOLVE_FAILED"
```

使用：

```python
if isinstance(profile, ResolveFailure):
    raise BrowserSessionError(
        _map_profile_failure_to_diagnostic_code(profile),
        describe_resolve_result(profile),
    )
```

启动失败：

```python
raise BrowserSessionError(
    "BROWSER_LAUNCH_FAILED",
    f"Failed to launch browser persistent context: {exc}",
) from exc
```

然后 `actions.py` 里：

```python
make_browser_unavailable_error(
    message="Browser runtime is unavailable.",
    diagnostic_code=error.diagnostic_code,
)
```

---

# Phase 8：修改 `tool.py`

## 目标

更新工具描述，避免继续说“follow recommended_next_action”。

## 修改前

```text
On errors, follow recommended_next_action from the response.
```

## 修改后

```python
_TOOL_DESCRIPTION = (
    "Agent-safe browser interaction tool based on snapshot+ref. "
    "Operates a local persistent browser with dedicated automation profile. "
    "Each call performs ONE action. "
    "Flow: 1) navigate to a page, 2) snapshot to get refs with snapshot_id, "
    "3) use click_ref/fill_ref with snapshot_id and ref to interact. "
    "Always pass browser_session_id from the previous response when available. "
    "On errors, inspect error.recovery_hint. It describes tool-state recovery "
    "requirements, not a task-level plan. "
    "On STALE_REF or SNAPSHOT_REQUIRED, a fresh snapshot is required before "
    "retrying ref-based actions. "
    "On USER_INTERVENTION_REQUIRED, prompt the user to act in the visible browser window."
)
```

---

# Phase 9：测试和验收

## 必须新增或更新的测试

### 1. stale ref

输入旧 snapshot_id，期望：

```json
{
  "success": false,
  "error": {
    "category": "snapshot",
    "code": "STALE_REF",
    "recovery_hint": {
      "type": "refresh_snapshot",
      "required_before_retry": true
    }
  }
}
```

---

### 2. snapshot required

未 snapshot 直接 `click_ref`，期望：

```json
{
  "success": false,
  "error": {
    "category": "snapshot",
    "code": "SNAPSHOT_REQUIRED",
    "recovery_hint": {
      "type": "refresh_snapshot",
      "required_before_retry": true
    }
  }
}
```

---

### 3. fill_ref 不回显 text

成功响应中只能有：

```json
{
  "action_result": {
    "detail": {
      "text_length": 6
    }
  }
}
```

不能有：

```json
{
  "text": "123456"
}
```

---

### 4. session expired

关闭 page 后执行非 navigate 动作，期望：

```json
{
  "success": false,
  "error": {
    "category": "session",
    "code": "SESSION_EXPIRED",
    "recovery_hint": {
      "type": "restart_session",
      "required_before_retry": true
    }
  }
}
```

---

### 5. navigate 能恢复 expired session

关闭 page 后执行 `navigate`，应成功创建新 session。

---

### 6. user intervention

mock auth/captcha 检测，期望：

```json
{
  "success": false,
  "error": {
    "category": "user_intervention",
    "code": "USER_INTERVENTION_REQUIRED",
    "requires_user_action": true,
    "user_action": {
      "type": "captcha",
      "detected_reason": "captcha"
    },
    "recovery_hint": {
      "type": "wait_for_user",
      "required_before_retry": true
    }
  }
}
```

---

### 7. browser unavailable

mock profile locked，期望：

```json
{
  "success": false,
  "error": {
    "category": "browser_unavailable",
    "code": "BROWSER_UNAVAILABLE",
    "diagnostic_code": "PROFILE_LOCKED",
    "requires_user_action": true
  }
}
```

---

## 主流程回归

至少跑：

```text
1. 维基百科首页搜索 apple。
2. GitHub 首页 -> Sign in -> 填账号密码 -> submit -> snapshot 错误提示。
```

要求：

```text
1. 主流程成功。
2. 错误数为 0，除非测试刻意触发错误。
3. fill_ref output 不包含 text 原文。
4. 错误响应不再出现 error_code / error_message / recommended_next_action 顶层字段。
5. 错误响应出现 error.recovery_hint。
```

---

# 全局搜索清单

改完后执行全局搜索。

## 不应该再出现

```text
recommended_next_action
RecommendedNextAction
error_code
error_message
AUTH_REQUIRED
CAPTCHA_REQUIRED
FILL_FAILED
CLICK_FAILED
NAVIGATION_FAILED
NAVIGATION_TIMEOUT
BROWSER_LAUNCH_FAILED
AUTOMATION_PROFILE_LOCKED
```

注意：

```text
FILL_FAILED / CLICK_FAILED / NAVIGATION_FAILED 可以作为字符串 diagnostic_code 出现。
但不应该作为 ToolErrorCode enum 出现。
```

## 仍然允许出现

```text
diagnostic_code="FILL_FAILED"
diagnostic_code="CLICK_FAILED"
diagnostic_code="NAVIGATION_FAILED"
```

---

# 兼容性说明

因为功能未上线，本次可以做破坏性协议修改。

不需要保留：

```json
{
  "error_code": "...",
  "error_message": "...",
  "recommended_next_action": {...}
}
```

直接切换为：

```json
{
  "error": {
    "category": "...",
    "code": "...",
    "message": "...",
    "recovery_hint": {...}
  }
}
```

---

---

# 给 Codex 的执行提示词

```text
请对 browse_interact 做一次错误协议重构。

背景：
当前错误响应使用顶层 error_code / error_message / recommended_next_action。
其中 recommended_next_action 语义过强，容易被误解为工具层知道 agent 的任务级下一步。
实际目标是让工具只描述当前工具状态恢复所需的前置条件。

目标：
1. 将 recommended_next_action 改为 error.recovery_hint。
2. recovery_hint 只表示 tool_state recovery requirement，不表示 task-level plan。
3. 将错误响应从顶层 error_code / error_message 改为嵌套 error 对象。
4. 收敛 agent-facing error code。
5. 把 NAVIGATION_FAILED / FILL_FAILED / CLICK_FAILED / PROFILE_LOCKED 等细节改为 diagnostic_code。
6. 用户介入类统一为 USER_INTERVENTION_REQUIRED，具体原因放到 user_action.detected_reason 和 error.context.detected_reason。
7. 系统/浏览器不可用类统一为 BROWSER_UNAVAILABLE，具体原因放 diagnostic_code。
8. 保持成功响应核心字段不变。
9. 不改变 browse_interact 的 action 行为。
10. 不拆分 actions.py。
11. 不重构 browser_profile。
12. 不改 snapshot/ref 生命周期。
13. fill_ref 成功响应仍然只能返回 text_length，不能返回 text。
14. fill_ref 成功后仍然不 invalidate snapshot。
15. click_ref 成功后仍然 invalidate snapshot。

需要修改：
- models.py
- errors.py
- responses.py
- intervention.py
- actions.py
- dispatcher.py
- session.py
- tool.py
- 相关测试

最终错误响应格式：
{
  "success": false,
  "browser_session_id": "...",
  "session": {...},
  "page": {...},
  "error": {
    "category": "snapshot",
    "code": "STALE_REF",
    "message": "The provided snapshot_id is stale.",
    "retryable": true,
    "requires_user_action": false,
    "context": {...},
    "diagnostic_code": "... optional ...",
    "recovery_hint": {
      "type": "refresh_snapshot",
      "scope": "tool_state",
      "required_before_retry": true,
      "reason": "..."
    }
  }
}

验收：
- 不再出现顶层 error_code / error_message / recommended_next_action。
- 不再使用 RecommendedNextAction。
- ToolErrorCode 只保留 agent-facing code。
- action 细节失败统一为 ACTION_FAILED + diagnostic_code。
- 用户介入统一为 USER_INTERVENTION_REQUIRED。
- browser/profile 启动失败统一为 BROWSER_UNAVAILABLE + diagnostic_code。
- 原主流程测试仍然通过。
```

```
```
