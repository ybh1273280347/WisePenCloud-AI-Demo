````md
# browse_interact 第三轮工程成熟度补强执行文档

## 背景

上一轮已经完成：

```text
1. recommended_next_action -> error.recovery_hint
2. 错误响应改为嵌套 error 对象
3. snapshot 支持 mode / goal / limit
4. snapshot JS 返回 elements metadata
5. focused / viewport / compact snapshot 已落地
6. UserInterventionDetector 返回 InterventionSignal
7. USER_INTERVENTION_REQUIRED 携带 confidence / evidence
````

本轮不继续扩展浏览器能力，而是补强工程成熟度：

```text
1. 显式化 action 行为契约，降低跨文件认知负载。
2. 拆掉 common.py 的隐式控制流职责。
3. 把 snapshot_script.js 提升为一等核心组件。
4. 给 BrowserSession 增加生命周期可观测性。
5. 明确当前系统仍然是单活动 session 设计。
```

---

# 一、总目标

本轮重构目标：

```text
1. 新增 action_contracts.py，显式描述每个 action 的行为契约。
2. 将 common.py 中有副作用的 helper 迁移到 action_guards.py / action_responses.py。
3. 让 common.py 只保留无状态纯函数，或直接删除。
4. snapshot_script.js 增加 schema_version / diagnostics。
5. snapshot.py 校验 snapshot script 返回协议，避免 JS 侧错误被模糊吞掉。
6. BrowserSession 增加 created_at / last_used_at / operation_count / owner_id。
7. SessionState 响应增加可选生命周期 metadata。
8. dispatcher 在执行前后 touch session，维护 last_used_at。
```

---

# 二、非目标

本轮不做：

```text
1. 不继续改 snapshot focused 排序算法。
2. 不引入 embedding / LLM rerank / 视觉模型。
3. 不引入多 session 调度器。
4. 不引入完整 pipeline.py。
5. 不重构 browser_profile。
6. 不改变 click_ref / fill_ref 的核心执行语义。
7. 不改变 snapshot_id / ref 生命周期。
8. 不恢复 recommended_next_action。
9. 不把 Playwright 原始异常直接暴露给 agent。
10. 不做本地 runner / WebSocket 通信。
```

---

# 三、显式化 action 行为契约

## 目标

当前很多 action 行为是隐式约定：

```text
fill_ref 成功后不 invalidate snapshot
click_ref 成功后 invalidate snapshot
navigate 可以创建 session
snapshot 必须已有 session
wait 会检测 user intervention
```

这些约定不能只存在于 handler 代码里。
本轮新增 `action_contracts.py`，让这些行为变成机器可读和维护者可读的显式契约。

---

## 新增文件：`action_contracts.py`

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionContract:
    type: str
    requires_existing_session: bool
    can_create_session: bool = False
    requires_snapshot: bool = False
    uses_ref: bool = False
    invalidates_snapshot_on_success: bool = False
    detects_intervention: bool = False
    mutates_page: bool = False
    description: str = ""


ACTION_CONTRACTS: dict[str, ActionContract] = {
    "status": ActionContract(
        type="status",
        requires_existing_session=False,
        description="Inspect current browser session and page state.",
    ),
    "navigate": ActionContract(
        type="navigate",
        requires_existing_session=False,
        can_create_session=True,
        invalidates_snapshot_on_success=True,
        detects_intervention=True,
        mutates_page=True,
        description="Navigate to a URL, creating or recovering a browser session if needed.",
    ),
    "go_back": ActionContract(
        type="go_back",
        requires_existing_session=True,
        invalidates_snapshot_on_success=True,
        detects_intervention=True,
        mutates_page=True,
        description="Navigate backward in browser history.",
    ),
    "go_forward": ActionContract(
        type="go_forward",
        requires_existing_session=True,
        invalidates_snapshot_on_success=True,
        detects_intervention=True,
        mutates_page=True,
        description="Navigate forward in browser history.",
    ),
    "snapshot": ActionContract(
        type="snapshot",
        requires_existing_session=True,
        description="Capture current interactive DOM refs.",
    ),
    "screenshot": ActionContract(
        type="screenshot",
        requires_existing_session=True,
        description="Capture current page screenshot.",
    ),
    "click_ref": ActionContract(
        type="click_ref",
        requires_existing_session=True,
        requires_snapshot=True,
        uses_ref=True,
        invalidates_snapshot_on_success=True,
        detects_intervention=True,
        mutates_page=True,
        description="Click a ref from the current snapshot.",
    ),
    "fill_ref": ActionContract(
        type="fill_ref",
        requires_existing_session=True,
        requires_snapshot=True,
        uses_ref=True,
        invalidates_snapshot_on_success=False,
        mutates_page=True,
        description=(
            "Fill a ref from the current snapshot. "
            "Does not invalidate snapshot because normal input value changes usually preserve ref mapping."
        ),
    ),
    "scroll": ActionContract(
        type="scroll",
        requires_existing_session=True,
        invalidates_snapshot_on_success=True,
        mutates_page=True,
        description="Scroll the page or viewport.",
    ),
    "key": ActionContract(
        type="key",
        requires_existing_session=True,
        invalidates_snapshot_on_success=True,
        mutates_page=True,
        description="Send a keyboard action to the page.",
    ),
    "wait": ActionContract(
        type="wait",
        requires_existing_session=True,
        detects_intervention=True,
        description="Wait for a duration and optionally detect user intervention signals.",
    ),
    "get_content": ActionContract(
        type="get_content",
        requires_existing_session=True,
        description="Extract current page content.",
    ),
}


def get_action_contract(action_type: str) -> ActionContract | None:
    return ACTION_CONTRACTS.get(action_type)
```

---

## 使用原则

`action_contracts.py` 先作为显式契约层，不强制所有 handler 都依赖它执行逻辑。

也就是说：

```text
它不是 pipeline。
它不是 dispatcher 替代品。
它不是自动执行器。
它是 action 行为事实表。
```

后续若某些行为需要统一校验，再逐步让 dispatcher 或 handler 引用它。

---

# 四、拆掉 common.py 的隐式控制流职责

## 当前问题

`common.py` 如果包含：

```text
_get_existing_page_or_error
_get_or_create_page_or_error
_action_error_response
_session_state
```

它就不是普通 common。
这些函数有副作用、参与控制流、构造 response，是 action execution support。

本轮目标：

```text
1. common.py 不再承载 session/page guard。
2. 有副作用的 action 前置条件逻辑迁移到 action_guards.py。
3. response 构造 helper 迁移到 action_responses.py。
4. common.py 只保留纯函数；若没有纯函数，删除 common.py。
```

---

## 新增文件：`action_responses.py`

```python
from __future__ import annotations

from typing import Optional

from playwright.async_api import Page

from .models import ToolError
from .responses import (
    build_error_response,
    get_page_state,
    get_session_state,
)
from .session import BrowserSessionManager


def session_state(
    session_manager: BrowserSessionManager,
    *,
    created: bool = False,
    reused: bool = False,
):
    return get_session_state(
        session_manager.session_id,
        valid=session_manager.is_session_alive,
        created=created,
        reused=reused,
        created_at=session_manager.created_at,
        last_used_at=session_manager.last_used_at,
        age_seconds=session_manager.age_seconds,
        idle_seconds=session_manager.idle_seconds,
        operation_count=session_manager.operation_count,
        owner_id=session_manager.owner_id,
    )


async def action_error_response(
    session_manager: BrowserSessionManager,
    page: Optional[Page],
    error: ToolError,
) -> str:
    return build_error_response(
        session_state=session_state(session_manager),
        page_state=await get_page_state(page),
        error=error,
    )
```

---

## 新增文件：`action_guards.py`

```python
from __future__ import annotations

from typing import Optional

from playwright.async_api import Page

from .action_responses import session_state
from .errors import make_browser_unavailable_error, make_session_error
from .responses import build_error_response, get_page_state
from .session import BrowserSessionError, BrowserSessionManager


async def get_existing_page_or_error(
    session_manager: BrowserSessionManager,
    browser_session_id: Optional[str],
) -> tuple[Page | None, str | None]:
    page, error_code = await session_manager.get_existing_page(browser_session_id)

    if error_code is None:
        session_manager.touch_session()
        return page, None

    return None, build_error_response(
        session_state=session_state(session_manager),
        page_state=await get_page_state(session_manager.page),
        error=make_session_error(error_code, session_manager.session_id),
    )


async def get_or_create_page_or_error(
    session_manager: BrowserSessionManager,
    browser_session_id: Optional[str],
) -> tuple[Page | None, str | None]:
    try:
        page, error_code = await session_manager.get_or_create_page(browser_session_id)
    except BrowserSessionError as error:
        return None, build_error_response(
            session_state=session_state(session_manager),
            page_state=await get_page_state(session_manager.page),
            error=make_browser_unavailable_error(
                message="Browser runtime is unavailable.",
                diagnostic_code=error.diagnostic_code,
            ),
        )

    if error_code is None:
        session_manager.touch_session()
        return page, None

    return None, build_error_response(
        session_state=session_state(session_manager),
        page_state=await get_page_state(session_manager.page),
        error=make_session_error(error_code, session_manager.session_id),
    )
```

---

## actions.py import 调整

从：

```python
from .common import (
    _action_error_response,
    _get_existing_page_or_error,
    _get_or_create_page_or_error,
    _session_state,
)
```

改成：

```python
from .action_guards import (
    get_existing_page_or_error,
    get_or_create_page_or_error,
)
from .action_responses import (
    action_error_response,
    session_state,
)
```

命名去掉下划线，因为这些已经是正式模块 API。

---

## handler 内部调用调整

原来：

```python
page, session_error_response = await _get_existing_page_or_error(
    session_manager,
    browser_session_id,
)
```

改成：

```python
page, session_error_response = await get_existing_page_or_error(
    session_manager,
    browser_session_id,
)
```

原来：

```python
return await _action_error_response(
    session_manager,
    page,
    make_schema_error("...")
)
```

改成：

```python
return await action_error_response(
    session_manager,
    page,
    make_schema_error("...")
)
```

原来：

```python
session_state=_session_state(session_manager, reused=True)
```

改成：

```python
session_state=session_state(session_manager, reused=True)
```

---

# 五、snapshot_script.js 协议化

## 目标

`snapshot_script.js` 是 agent 的 DOM 感知层。
本轮将它从“普通 JS 字符串”升级为“带协议版本和诊断信息的脚本”。

JS 返回结构从：

```json
{
  "elements": [...]
}
```

升级为：

```json
{
  "schemaVersion": 1,
  "elements": [...],
  "diagnostics": {
    "visitedNodeCount": 1234,
    "exposedElementCount": 56,
    "skippedElementCount": 789
  }
}
```

---

## snapshot.py 新增常量

```python
SNAPSHOT_SCRIPT_SCHEMA_VERSION = 1
```

建议放在 `snapshot.py` 内部，不放全局 constants。
原因：这是 snapshot script 私有协议版本，不是外部 action 协议。

---

## snapshot.py 校验脚本返回

在 `SnapshotManager.take()` 中，`json.loads(raw)` 后增加：

```python
schema_version = data.get("schemaVersion")
if schema_version != SNAPSHOT_SCRIPT_SCHEMA_VERSION:
    raise SnapshotScriptProtocolError(
        f"Unsupported snapshot script schema version: {schema_version}"
    )

elements = data.get("elements")
if not isinstance(elements, list):
    raise SnapshotScriptProtocolError("Snapshot script returned invalid elements payload")

diagnostics = data.get("diagnostics")
if not isinstance(diagnostics, dict):
    diagnostics = {}
```

新增异常：

```python
class SnapshotScriptProtocolError(ValueError):
    pass
```

---

## SnapshotPayload 增加 diagnostics

在 `models.py` 的 `SnapshotPayload` 中增加：

```python
diagnostics: Dict[str, Any] = field(default_factory=dict)
```

完整示例：

```python
@dataclass(frozen=True)
class SnapshotPayload:
    snapshot_id: str
    tree: str
    refs_valid_for: str = "current_dom_only"
    mode: str = "full"
    goal: Optional[str] = None
    returned_count: Optional[int] = None
    total_count: Optional[int] = None
    omitted_count: Optional[int] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)
```

---

## SnapshotManager.take 返回 diagnostics

```python
return SnapshotPayload(
    snapshot_id=snapshot_id,
    tree=tree,
    mode=normalized_mode,
    goal=goal if normalized_mode == "focused" else None,
    returned_count=returned_count,
    total_count=total_count,
    omitted_count=max(0, total_count - returned_count),
    diagnostics={
        **diagnostics,
        "selectedElementCount": returned_count,
        "totalElementCount": total_count,
        "omittedElementCount": max(0, total_count - returned_count),
    },
)
```

---

## snapshot JS 顶层返回示例

`snapshot_script.js` 或 `_SNAPSHOT_JS` 最末尾改成：

```javascript
return JSON.stringify({
    schemaVersion: 1,
    elements,
    diagnostics: {
        visitedNodeCount,
        exposedElementCount: elements.length,
        skippedElementCount
    }
});
```

---

## JS 内部计数变量

在 JS 顶部增加：

```javascript
let visitedNodeCount = 0;
let skippedElementCount = 0;
```

在 walk 中：

```javascript
for (const el of children) {
    visitedNodeCount += 1;

    if (!el || !el.tagName || skip.has(el.tagName)) {
        skippedElementCount += 1;
        continue;
    }

    ...
}
```

这样 Python 侧可以知道脚本大致处理了多少 DOM 节点。

---

# 六、snapshot script 加载方式标准化

## 目标

不要把大段 JS 混在 Python 字符串里。
将 JS 文件作为 runtime 资源加载。

目标结构：

```text
browser_interact/
  snapshot.py
  snapshot_script.js
```

---

## snapshot.py 加载 JS

```python
from importlib.resources import files


def _load_snapshot_script() -> str:
    return (
        files(__package__)
        .joinpath("snapshot_script.js")
        .read_text(encoding="utf-8")
    )


_SNAPSHOT_JS = _load_snapshot_script()
```

若当前包结构不适合 `importlib.resources`，可以临时使用：

```python
from pathlib import Path

_SNAPSHOT_JS = Path(__file__).with_name("snapshot_script.js").read_text(
    encoding="utf-8"
)
```

长期建议使用 `importlib.resources`，更适合打包。

---

# 七、BrowserSession 生命周期可观测性

## 目标

当前 `BrowserSessionManager` 是单活动 session 设计，但 session 生命周期不够显式。

本轮增加：

```text
created_at
last_used_at
operation_count
owner_id
```

注意：

```text
owner_id 只做观测字段，不做强制隔离。
当前不引入多 session 调度。
当前不做复杂所有权校验。
```

---

## 修改 BrowserSession

```python
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright


@dataclass
class BrowserSession:
    session_id: str
    playwright: Playwright
    context: BrowserContext
    page: Page
    automation_user_data_dir: Path | None = None
    browser_channel: str | None = None
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    operation_count: int = 0
    owner_id: str | None = None

    def touch(self) -> None:
        self.last_used_at = datetime.now(timezone.utc)
        self.operation_count += 1
```

创建 session 时：

```python
now = datetime.now(timezone.utc)

self._session = BrowserSession(
    session_id=uuid.uuid4().hex[:SESSION_ID_LENGTH],
    playwright=playwright,
    context=context,
    page=page,
    automation_user_data_dir=profile.automation_user_data_dir,
    browser_channel=profile.browser_channel,
    created_at=now,
    last_used_at=now,
    operation_count=0,
    owner_id=self._owner_id,
)
```

---

## BrowserSessionManager 增加属性

```python
from datetime import datetime, timezone


class BrowserSessionManager:
    ...

    @property
    def created_at(self) -> str | None:
        if self._session is None or self._session.created_at is None:
            return None
        return self._session.created_at.isoformat()

    @property
    def last_used_at(self) -> str | None:
        if self._session is None or self._session.last_used_at is None:
            return None
        return self._session.last_used_at.isoformat()

    @property
    def operation_count(self) -> int:
        return self._session.operation_count if self._session else 0

    @property
    def owner_id(self) -> str | None:
        return self._session.owner_id if self._session else self._owner_id

    @property
    def age_seconds(self) -> float | None:
        if self._session is None or self._session.created_at is None:
            return None
        return (
            datetime.now(timezone.utc) - self._session.created_at
        ).total_seconds()

    @property
    def idle_seconds(self) -> float | None:
        if self._session is None or self._session.last_used_at is None:
            return None
        return (
            datetime.now(timezone.utc) - self._session.last_used_at
        ).total_seconds()

    def set_owner(self, owner_id: str | None) -> None:
        self._owner_id = owner_id

        if self._session is not None and self._session.owner_id is None:
            self._session.owner_id = owner_id

    def touch_session(self) -> None:
        if self._session is not None and not self._session.page.is_closed():
            self._session.touch()
```

`__init__` 中增加：

```python
self._owner_id: str | None = None
```

---

## SessionState 扩展

`models.py` 中：

```python
@dataclass(frozen=True)
class SessionState:
    browser_session_id: Optional[str]
    valid: bool
    created: bool = False
    reused: bool = False
    created_at: Optional[str] = None
    last_used_at: Optional[str] = None
    age_seconds: Optional[float] = None
    idle_seconds: Optional[float] = None
    operation_count: int = 0
    owner_id: Optional[str] = None
```

---

## responses.py 修改 get_session_state

```python
def get_session_state(
    session_id: Optional[str],
    *,
    valid: bool,
    created: bool = False,
    reused: bool = False,
    created_at: Optional[str] = None,
    last_used_at: Optional[str] = None,
    age_seconds: Optional[float] = None,
    idle_seconds: Optional[float] = None,
    operation_count: int = 0,
    owner_id: Optional[str] = None,
) -> SessionState:
    return SessionState(
        browser_session_id=session_id,
        valid=valid,
        created=created,
        reused=reused,
        created_at=created_at,
        last_used_at=last_used_at,
        age_seconds=age_seconds,
        idle_seconds=idle_seconds,
        operation_count=operation_count,
        owner_id=owner_id,
    )
```

---

# 八、dispatcher 绑定 owner_id

## 目标

当前不做多 session 隔离，但让 session 响应里能看出是谁创建或使用了 session。

从 `context` 中提取一个弱 owner_id。

建议优先级：

```text
context["user_id"]
context["conversation_id"]
context["task_id"]
None
```

---

## dispatcher.py 增加 helper

```python
def _resolve_owner_id(context: Optional[Dict[str, Any]]) -> str | None:
    if not context:
        return None

    for key in ("user_id", "conversation_id", "task_id"):
        value = context.get(key)
        if isinstance(value, str) and value:
            return value

    return None
```

在 `_execute_inner()` 开头：

```python
owner_id = _resolve_owner_id(context)
self._session_manager.set_owner(owner_id)
```

注意：

```text
1. 不因为 owner_id 不同而拒绝请求。
2. 不引入 SESSION_OWNER_MISMATCH。
3. 这里只做可观测性，不做权限控制。
```

---

# 九、session 状态探活语义澄清

## 当前原则

保持当前轻量验证：

```text
1. page is None -> no session
2. page.is_closed() -> SESSION_EXPIRED
3. session_id mismatch -> SESSION_MISMATCH
```

本轮不增加昂贵的每次 evaluate 探活。

原因：

```text
1. evaluate 探活可能拖慢每次 action。
2. 某些页面状态下 evaluate 自身可能产生噪声。
3. Playwright action 失败时已经会走 ACTION_FAILED。
```

## 增加注释

在 `validate_session()` 上方增加：

```python
async def validate_session(
    self,
    browser_session_id: str | None,
):
    """
    Validate the currently tracked browser session.

    This is intentionally a lightweight validation:
    - It checks whether a session exists.
    - It checks whether the tracked page is closed.
    - It checks browser_session_id consistency.

    It does not perform an eager page.evaluate probe on every action.
    Runtime failures from crashed or detached browser targets are handled
    by individual actions and reported as ACTION_FAILED or BROWSER_UNAVAILABLE.
    """
```

这样明确生命周期边界。

---

# 十、action handler 顶部增加流程注释

## 目标

降低跨文件跳转带来的认知负载。
每个复杂 handler 顶部用短注释写清执行阶段。

## click_ref 示例

```python
async def handle_click_ref(...):
    # Flow:
    # 1. Require an existing live session.
    # 2. Require current snapshot_id.
    # 3. Resolve safe ref selector.
    # 4. Execute click.
    # 5. Invalidate snapshot because click may mutate DOM or navigate.
    # 6. Detect best-effort user intervention signal.
    # 7. Return action result or structured error.
```

## fill_ref 示例

```python
async def handle_fill_ref(...):
    # Flow:
    # 1. Require an existing live session.
    # 2. Require current snapshot_id.
    # 3. Resolve safe ref selector.
    # 4. Fill text without returning sensitive text in response.
    # 5. Do not invalidate snapshot on success.
    # 6. Return text_length only.
```

## navigate 示例

```python
async def handle_navigate(...):
    # Flow:
    # 1. Normalize URL.
    # 2. Get or create browser session.
    # 3. Navigate page.
    # 4. Invalidate snapshot after navigation.
    # 5. Detect best-effort user intervention signal.
    # 6. Return page state and action result.
```

---

# 十一、模块职责最终形态

本轮后模块职责应保持：

```text
tool.py
  BaseTool adapter + schema description。

dispatcher.py
  action 分发、request schema 粗校验、session_id 自动补全、execute lock、owner_id 绑定。

action_contracts.py
  每个 action 的行为契约事实表。

action_guards.py
  有副作用的 session/page 前置条件处理。

action_responses.py
  action handler 内部共用的 response/session_state helper。

actions.py
  具体 action 业务流程。

session.py
  Playwright persistent context 生命周期、单活动 session 管理、session metadata。

snapshot.py
  snapshot_id/ref 生命周期、snapshot script 加载和协议校验、mode/goal/limit 筛选。

snapshot_script.js
  浏览器内 DOM 元信息采集。

intervention.py
  best-effort user intervention signal detection。

errors.py
  error/recovery_hint 构造。

responses.py
  success/error JSON 序列化。

utils.py
  纯函数工具，例如 URL 脱敏、按键标准化。
```

---

# 十二、关键禁止事项

执行本轮重构时，不允许：

```text
1. 把 action_guards.py 写成新的大 pipeline。
2. 把所有 action 流程抽象成统一执行器。
3. 让 common.py 继续承载有副作用的控制流。
4. 让 detector 重新返回 ToolError。
5. 让 snapshot_script.js 重新只返回 tree。
6. 删除 full snapshot。
7. 改变 fill_ref 不 invalidate snapshot 的语义。
8. 改变 click_ref invalidate snapshot 的语义。
9. 在 session owner_id 不同时直接拒绝请求。
10. 引入多 session manager。
11. 将原始 Playwright 异常完整暴露给 agent。
12. 恢复 recommended_next_action。
```

---

# 十三、最终期望示例

## status response 中 session metadata

```json
{
  "success": true,
  "browser_session_id": "abc123",
  "session": {
    "browser_session_id": "abc123",
    "valid": true,
    "created": false,
    "reused": true,
    "created_at": "2026-05-09T10:12:30.123456+00:00",
    "last_used_at": "2026-05-09T10:15:01.456789+00:00",
    "age_seconds": 151.33,
    "idle_seconds": 0.02,
    "operation_count": 8,
    "owner_id": "conversation_xxx"
  },
  "page": {
    "url": "https://github.com/login",
    "title": "Sign in to GitHub · GitHub",
    "ready_state": "complete",
    "is_closed": false
  },
  "action_result": {
    "type": "status",
    "status": "completed",
    "detail": {
      "has_session": true,
      "is_session_alive": true
    }
  }
}
```

---

## snapshot response 中 diagnostics

```json
{
  "success": true,
  "browser_session_id": "abc123",
  "session": {...},
  "page": {...},
  "action_result": {
    "type": "snapshot",
    "status": "completed",
    "detail": {
      "snapshot_id": "f7eb60db",
      "mode": "focused",
      "goal": "find search box",
      "returned_count": 10,
      "total_count": 132,
      "omitted_count": 122
    }
  },
  "snapshot": {
    "snapshot_id": "f7eb60db",
    "tree": "[e5] searchbox \"Search\" [fillable]",
    "refs_valid_for": "current_dom_only",
    "mode": "focused",
    "goal": "find search box",
    "returned_count": 10,
    "total_count": 132,
    "omitted_count": 122,
    "diagnostics": {
      "visitedNodeCount": 542,
      "exposedElementCount": 132,
      "skippedElementCount": 301,
      "selectedElementCount": 10,
      "totalElementCount": 132,
      "omittedElementCount": 122
    }
  }
}
```

---

# 十四、给 AI 执行时的提示词

```text
请在上一轮 browse_interact 重构已经完成的基础上，继续做第三轮工程成熟度补强。

上一轮已完成：
- error.recovery_hint 已替代 recommended_next_action
- 错误响应已改为嵌套 error 对象
- snapshot 已支持 mode / goal / limit
- snapshot JS 已返回 elements metadata
- UserInterventionDetector 已返回 InterventionSignal
- 用户介入错误统一为 USER_INTERVENTION_REQUIRED

本轮目标：
1. 新增 action_contracts.py，显式描述每个 action 的行为契约。
2. 拆掉 common.py 的隐式控制流职责。
3. 将有副作用的 session/page guard 移到 action_guards.py。
4. 将 action 内部 response/session_state helper 移到 action_responses.py。
5. common.py 只能保留无状态纯函数；如果没有纯函数则删除。
6. snapshot_script.js 增加 schemaVersion 和 diagnostics。
7. snapshot.py 校验 snapshot script schemaVersion 和 elements payload。
8. SnapshotPayload 增加 diagnostics。
9. BrowserSession 增加 created_at / last_used_at / operation_count / owner_id。
10. SessionState 增加 created_at / last_used_at / age_seconds / idle_seconds / operation_count / owner_id。
11. BrowserSessionManager 增加 touch_session / set_owner / age_seconds / idle_seconds 等可观测属性。
12. dispatcher 从 context 中提取 user_id / conversation_id / task_id 作为弱 owner_id，并传给 session manager。
13. owner_id 只做观测，不做请求拒绝。
14. validate_session 保持轻量，不要每次 page.evaluate 探活。
15. 在复杂 action handler 顶部增加流程注释，显式说明执行阶段。

必须保持：
- 不改变 click_ref / fill_ref 行为。
- fill_ref 成功后仍然不 invalidate snapshot。
- fill_ref 成功响应仍然只返回 text_length，不返回 text。
- click_ref 成功后仍然 invalidate snapshot。
- snapshot_id / ref 生命周期不变。
- error.recovery_hint 不变回 recommended_next_action。
- UserInterventionDetector 不重新返回 ToolError。
- 不引入多 session manager。
- 不引入 pipeline.py 统一执行器。
- 不修改 browser_profile。
- 不做本地 runner / WebSocket 通信。

最终模块职责：
- action_contracts.py：action 行为契约事实表
- action_guards.py：session/page 前置条件处理
- action_responses.py：handler 共用 response helper
- actions.py：具体 action 业务流程
- snapshot_script.js：浏览器内 DOM 元信息采集
- snapshot.py：snapshot 协议校验、筛选、ref 生命周期
- session.py：单活动 session 生命周期和 metadata
```

```
```
