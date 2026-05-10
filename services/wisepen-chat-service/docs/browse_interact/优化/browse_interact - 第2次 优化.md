下面是专门针对 **`owner_id / context ownership` 清理** 的可执行方案。结论很明确：**当前阶段全部移除 owner 归属设计，只保留 session 生命周期观测字段。**

````md
# browse_interact session owner_id 清理执行文档

## 背景

当前 `browse_interact` 的运行模型是：

```text
云端 agent 发起 browse_interact 调用
用户电脑上的本地 runner 执行浏览器操作
一个本地 runner 对应一个用户环境
一个用户连续发多个指令，复用同一个浏览器 session
````

在这个模型下，不存在多个用户同时竞争同一个本地 browser session 的问题。

因此之前引入的：

```text
owner_id
session_context_id
set_owner()
_resolve_owner_id()
```

都不应该保留。

`context` 虽然仍然是 `BaseTool.execute(context, **kwargs)` 的接口参数，但当前 browse_interact 不依赖 `context` 中的 `user_id / conversation_id / task_id`，也不应该从中派生 session ownership。

---

# 一、最终目标

本次清理目标：

```text
1. 删除 owner_id 字段。
2. 删除 set_owner()。
3. 删除 _resolve_owner_id()。
4. dispatcher 不再从 context 中提取任何 session owner 信息。
5. SessionState 只保留生命周期观测字段。
6. BrowserSession 只保留生命周期观测字段。
7. context 可以继续保留在 execute 签名中，但 browse_interact 不使用它。
```

最终保留的 session metadata：

```text
created_at
last_used_at
age_seconds
idle_seconds
operation_count
```

这些字段只用于：

```text
调试
日志分析
空闲回收
观察 session 生命周期
```

不用于：

```text
权限控制
多用户隔离
任务归属判断
session 拒绝策略
```

---

# 二、需要删除的设计

必须删除：

```text
owner_id
session_context_id
set_owner
_resolve_owner_id
SESSION_OWNER_MISMATCH
owner mismatch 相关逻辑
```

如果当前代码里没有 `SESSION_OWNER_MISMATCH`，不要新增。

---

# 三、修改 `protocol_models.py`

## 当前如果有

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

## 改成

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
```

原则：

```text
SessionState 不再表达归属。
SessionState 只表达 session 是否有效，以及生命周期状态。
```

---

# 四、修改 `session.py`

## 1. 修改 `BrowserSession`

### 当前如果有

```python
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

### 改成

```python
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

    def touch(self) -> None:
        self.last_used_at = datetime.now(timezone.utc)
        self.operation_count += 1
```

删除：

```text
owner_id
```

---

## 2. 修改 `BrowserSessionManager.__init__`

### 删除

```python
self._owner_id: str | None = None
```

不要保留任何 owner 相关状态。

---

## 3. 删除 `set_owner()`

### 删除整个方法

```python
def set_owner(self, owner_id: str | None) -> None:
    self._owner_id = owner_id

    if self._session is not None and self._session.owner_id is None:
        self._session.owner_id = owner_id
```

---

## 4. 删除 `owner_id` property

### 删除整个属性

```python
@property
def owner_id(self) -> str | None:
    return self._session.owner_id if self._session else self._owner_id
```

---

## 5. 创建 session 时删除 owner_id

### 当前如果有

```python
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

### 改成

```python
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
)
```

---

## 6. 保留生命周期观测属性

这些继续保留：

```python
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


def touch_session(self) -> None:
    if self._session is not None and not self._session.page.is_closed():
        self._session.touch()
```

---

# 五、修改 `tool_results.py`

## 当前如果有

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

## 改成

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
    )
```

---

# 六、修改 `action_responses.py`

## 当前如果有

```python
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
```

## 改成

```python
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
    )
```

删除：

```text
owner_id=session_manager.owner_id
```

---

# 七、修改 `dispatcher.py`

## 1. 删除 `_resolve_owner_id()`

### 删除整个函数

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

---

## 2. 删除 owner 绑定逻辑

### 当前如果有

```python
owner_id = _resolve_owner_id(context)
self._session_manager.set_owner(owner_id)
```

### 直接删除

`dispatcher` 不再读取 `context`。

---

## 3. `execute()` 是否保留 context 参数？

保留。

原因：

```text
BaseTool.execute(context, **kwargs) 仍然需要 context 参数。
上层框架可能仍然传 context。
browse_interact 只是当前不使用它。
```

推荐写法：

```python
async def execute(
    self,
    *,
    context: Dict[str, Any] | None = None,
    request: Dict[str, Any],
) -> str:
    async with self._execute_lock:
        return await self._execute_inner(request)
```

或者如果你当前签名是：

```python
async def execute(self, context: Dict[str, Any], request: Dict[str, Any]) -> str:
```

也可以保留，但内部不要使用 `context`：

```python
async def execute(self, context: Dict[str, Any], request: Dict[str, Any]) -> str:
    async with self._execute_lock:
        return await self._execute_inner(request)
```

不要为了未使用参数写 owner 逻辑。

---

# 八、修改 `tool.py`

如果当前：

```python
async def execute(self, context: dict[str, Any], **kwargs: Any) -> str:
    return await self._controller.execute(
        context=context,
        request=kwargs,
    )
```

可以保留。

但要明确：`context` 只是传递给 controller 的框架上下文，controller 当前不使用它。

更简洁的做法是：

```python
async def execute(self, context: dict[str, Any], **kwargs: Any) -> str:
    return await self._controller.execute(
        context=context,
        request=kwargs,
    )
```

不用改。

如果你想进一步清晰，可以在 controller 里把参数命名为 `_context`：

```python
async def execute(
    self,
    *,
    context: Dict[str, Any] | None = None,
    request: Dict[str, Any],
) -> str:
    async with self._execute_lock:
        return await self._execute_inner(request)
```

这表示 context 当前被有意忽略。

---

# 九、最终响应示例

清理后，session response 应该是：

```json
{
  "session": {
    "browser_session_id": "abc123",
    "valid": true,
    "created": false,
    "reused": true,
    "created_at": "2026-05-09T10:12:30.123456+00:00",
    "last_used_at": "2026-05-09T10:15:01.456789+00:00",
    "age_seconds": 151.33,
    "idle_seconds": 0.02,
    "operation_count": 8
  }
}
```

不应该再出现：

```json
{
  "owner_id": "conversation_xxx"
}
```

也不应该出现：

```json
{
  "session_context_id": "conversation_xxx"
}
```

---

# 十、全局清理清单

全局搜索并删除：

```text
owner_id
session_context_id
set_owner
_resolve_owner_id
SESSION_OWNER_MISMATCH
owner mismatch
context owner
```

确认这些地方不再出现：

```text
protocol_models.py
session.py
tool_results.py
action_responses.py
dispatcher.py
tool_errors.py
```

允许继续存在：

```text
context
```

但仅限于：

```text
BaseTool.execute(context, **kwargs)
BrowserInteractController.execute(context=..., request=...)
```

不允许用 context 派生 session 归属。

---

# 十一、最终设计说明

最终语义：

```text
browse_interact 当前是单用户本地 runner + 单活动 browser session 设计。

browser_session_id 是唯一的 session 连续性凭证。

created_at / last_used_at / age_seconds / idle_seconds / operation_count
用于观测 session 生命周期。

context 当前不参与 browse_interact 的 session 管理。
```

一句话：

```text
不要为当前不存在的多用户并发问题引入 owner 语义。
```

---

# 十二、给 AI 执行时的提示词

```text
请清理 browse_interact 中 owner_id / session_context_id 相关设计。

背景：
我们的 browse_interact 最终运行在用户电脑上的本地 browser runner 中。
一个本地 runner 对应一个用户环境，不存在多个用户同时共享同一个本地浏览器 session 的问题。
用户连续发多个指令时，应直接复用当前 browser_session_id 对应的浏览器 session。
当前 tool context 不传 user_id / conversation_id / task_id 等字段，因此不应从 context 派生 session owner。

目标：
1. 删除 owner_id。
2. 删除 session_context_id，如果存在。
3. 删除 BrowserSession.owner_id。
4. 删除 SessionState.owner_id。
5. 删除 BrowserSessionManager._owner_id。
6. 删除 BrowserSessionManager.set_owner()。
7. 删除 BrowserSessionManager.owner_id property。
8. 删除 dispatcher.py 中的 _resolve_owner_id()。
9. 删除 dispatcher.py 中调用 set_owner() 的逻辑。
10. tool/controller 的 execute 签名可以继续保留 context 参数，但 browse_interact 不使用 context。
11. 保留 created_at / last_used_at / age_seconds / idle_seconds / operation_count。
12. session metadata 只用于调试、日志、空闲回收，不用于权限控制或 session 隔离。

必须保持：
- browser_session_id 校验逻辑不变。
- SESSION_MISMATCH / SESSION_EXPIRED / SESSION_NOT_FOUND 语义不变。
- 单活动 session 设计不变。
- execute lock 不变。
- click_ref / fill_ref / snapshot 行为不变。
- error.recovery_hint 不变。
- 不引入 SESSION_OWNER_MISMATCH。
- 不引入多 session manager。
- 不从 context 中读取 user_id / conversation_id / task_id。

完成后：
- 全局不应再出现 owner_id。
- 全局不应再出现 session_context_id。
- 全局不应再出现 set_owner。
- 全局不应再出现 _resolve_owner_id。
- session response 中不应再有 owner_id。
```

```
```
