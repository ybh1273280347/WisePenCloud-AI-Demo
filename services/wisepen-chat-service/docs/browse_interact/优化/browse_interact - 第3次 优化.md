同意。**agent-facing response 里不应该塞调试信息**，尤其是：

```text
age_seconds
idle_seconds
created_at
last_used_at
operation_count
diagnostics
visitedNodeCount
skippedElementCount
```

这些对 agent 完成任务没有必要，只会增加 token、污染上下文、诱导模型关注不该关注的实现细节。

核心原则改成：

> **工具响应只返回 agent 决策所需的协议状态；调试信息只进日志，不进入 tool output。**

---

# 最终保留的 session 字段

`SessionState` 应该收敛回：

```python
@dataclass(frozen=True)
class SessionState:
    browser_session_id: Optional[str]
    valid: bool
    created: bool = False
    reused: bool = False
```

删除：

```python
created_at
last_used_at
age_seconds
idle_seconds
operation_count
owner_id
session_context_id
```

其中：

```text
created / reused 是协议字段，不是调试字段。
```

它们告诉 agent 这次 action 是否创建了新 session，是否复用了旧 session，仍然有用。

---

# BrowserSession 内部也可以简化

如果不做空闲回收，`BrowserSession` 也不需要这些字段：

```python
created_at
last_used_at
operation_count
```

最终保留：

```python
@dataclass
class BrowserSession:
    session_id: str
    playwright: Playwright
    context: BrowserContext
    page: Page
    automation_user_data_dir: Path | None = None
    browser_channel: str | None = None
```

如果未来真要做空闲回收，再加内部字段，但**不要返回给 agent**。

---

# `get_session_state()` 改回最小形态

```python
def get_session_state(
    session_id: Optional[str],
    *,
    valid: bool,
    created: bool = False,
    reused: bool = False,
) -> SessionState:
    return SessionState(
        browser_session_id=session_id,
        valid=valid,
        created=created,
        reused=reused,
    )
```

---

# `action_responses.py` 同步简化

```python
def session_state(
    session_manager: BrowserSessionManager,
    *,
    created: bool = False,
    reused: bool = False,
) -> SessionState:
    return get_session_state(
        session_manager.session_id,
        valid=session_manager.is_session_alive,
        created=created,
        reused=reused,
    )
```

删除所有：

```python
created_at=session_manager.created_at
last_used_at=session_manager.last_used_at
age_seconds=session_manager.age_seconds
idle_seconds=session_manager.idle_seconds
operation_count=session_manager.operation_count
```

---

# Snapshot 里的调试信息也要删

如果之前引入了：

```python
diagnostics
visitedNodeCount
exposedElementCount
skippedElementCount
selectedElementCount
totalElementCount
omittedElementCount
```

这些不要进入 `SnapshotPayload`。

最终 `SnapshotPayload` 建议保留：

```python
@dataclass(frozen=True)
class SnapshotPayload:
    snapshot_id: str
    tree: str
    refs_valid_for: str = "current_dom_only"
    mode: str = "full"
    goal: Optional[str] = None
```

如果你决定 snapshot mode 只保留 `full / focused`，那 `mode / goal` 仍然有协议意义，可以保留。

删除：

```python
returned_count
total_count
omitted_count
diagnostics
```

这些都偏调试/统计，不是 agent 必须知道的。

---

# Snapshot JS 可以继续有内部统计吗？

不建议返回。

JS 只需要返回：

```json
{
  "schemaVersion": 1,
  "elements": [...]
}
```

不要返回：

```json
{
  "diagnostics": {...}
}
```

`schemaVersion` 可以保留，因为它是协议兼容字段，不是调试信息。

---

# 最终 response 示例

## success

```json
{
  "success": true,
  "browser_session_id": "abc123",
  "session": {
    "browser_session_id": "abc123",
    "valid": true,
    "created": false,
    "reused": true
  },
  "page": {
    "url": "https://example.com",
    "title": "Example",
    "ready_state": "complete",
    "is_closed": false
  },
  "action_result": {
    "type": "snapshot",
    "status": "completed",
    "detail": {
      "snapshot_id": "f7eb60db",
      "mode": "focused",
      "goal": "find search box"
    }
  },
  "snapshot": {
    "snapshot_id": "f7eb60db",
    "tree": "[e1] textbox \"Search\" [fillable]",
    "refs_valid_for": "current_dom_only",
    "mode": "focused",
    "goal": "find search box"
  }
}
```

## session 部分不再出现

```json
{
  "created_at": "...",
  "last_used_at": "...",
  "age_seconds": 123,
  "idle_seconds": 3,
  "operation_count": 8,
  "owner_id": "..."
}
```

---

# 最终清理清单

全局删除这些字段：

```text
owner_id
session_context_id
created_at
last_used_at
age_seconds
idle_seconds
operation_count
diagnostics
visitedNodeCount
exposedElementCount
skippedElementCount
selectedElementCount
totalElementCount
omittedElementCount
returned_count
total_count
omitted_count
```

但注意：

```text
schemaVersion 可以保留。
created / reused 可以保留。
mode / goal 可以保留。
```

---

# 给 AI 执行的提示词

```text
请清理 browse_interact 中所有 agent-facing 调试信息。

背景：
browse_interact 是给 agent 调用的浏览器交互工具。
工具响应应该只返回 agent 完成任务所需的协议状态，不返回调试信息。
调试信息应进入日志，不进入 tool output。

目标：
1. 删除 SessionState 中的 created_at / last_used_at / age_seconds / idle_seconds / operation_count / owner_id / session_context_id。
2. SessionState 只保留 browser_session_id / valid / created / reused。
3. 删除 BrowserSession 中的 owner_id / session_context_id。
4. 如果没有空闲回收需求，也删除 BrowserSession 中的 created_at / last_used_at / operation_count。
5. 删除 BrowserSessionManager 中对应的 created_at / last_used_at / age_seconds / idle_seconds / operation_count / owner_id 属性。
6. 删除 get_session_state() 中对应参数。
7. 删除 action_responses.session_state() 中对应传参。
8. 删除 SnapshotPayload 中的 returned_count / total_count / omitted_count / diagnostics。
9. 删除 snapshot JS 返回值中的 diagnostics。
10. snapshot JS 可以保留 schemaVersion。
11. snapshot response 保留 snapshot_id / tree / refs_valid_for / mode / goal。
12. action_result.detail 中不要返回 returned_count / total_count / omitted_count。
13. error.response 中不要返回调试统计信息。
14. 不改变 click_ref / fill_ref / snapshot / session 的行为。

必须保留：
- session.browser_session_id
- session.valid
- session.created
- session.reused
- page.url / page.title / page.ready_state / page.is_closed
- snapshot.snapshot_id
- snapshot.tree
- snapshot.refs_valid_for
- snapshot.mode
- snapshot.goal
- error.recovery_hint
- error.context 中和 agent 恢复有关的必要信息，例如 ref / snapshot_id / action_type
- diagnostic_code 可以保留在 error 中，仅用于粗粒度错误定位

完成后，全局不应再出现：
age_seconds
idle_seconds
created_at
last_used_at
operation_count
owner_id
session_context_id
returned_count
total_count
omitted_count
diagnostics
visitedNodeCount
skippedElementCount
```

结论：**删。**
`age_seconds / idle_seconds` 这类字段对 agent 没意义，属于实现侧调试信息，不应该进入工具协议。
