同意。**只有 `pass` 的错误类，如果只是为了开发时区分异常类型，但最终不承载结构化语义，就应该清理。**

尤其你现在已经有：

```text
ToolError
ToolErrorCode
ErrorCategory
diagnostic_code
recovery_hint
```

这套 agent-facing 错误协议后，很多 Python exception class 就不再需要存在。

---

````md
# browse_interact 简单 pass 错误类清理执行文档

## 背景

当前代码中存在一些只有 `pass` 的异常类，例如：

```python
class SnapshotError(Exception):
    pass


class BrowserProfileError(Exception):
    pass


class ActionError(Exception):
    pass
````

这类异常类如果没有额外字段、没有统一捕获语义、没有明确的跨层边界价值，就只是“为了开发方便”的类型标签。

当前 browse_interact 已经有结构化错误协议：

```text
ToolError
ToolErrorCode
ErrorCategory
diagnostic_code
recovery_hint
```

所以 agent-facing 错误不应该依赖大量 Python exception class 区分。

本轮目标是：

```text
删除无意义的 pass-only exception class。
保留真正跨边界、有数据、有语义的异常类。
不要改变工具对 agent 的错误协议。
```

---

# 一、清理原则

## 应该删除的错误类

满足以下条件的异常类应删除：

```text
1. 类体只有 pass。
2. 没有额外字段。
3. 没有自定义 __init__。
4. 没有被外部模块明确捕获并产生不同恢复逻辑。
5. 只是为了“看起来更语义化”。
6. 最终都会被转成 ACTION_FAILED / INTERNAL_ERROR / BROWSER_UNAVAILABLE 等结构化 ToolError。
```

例如：

```python
class ActionError(Exception):
    pass


class SnapshotError(Exception):
    pass


class RefError(Exception):
    pass


class NavigationError(Exception):
    pass
```

如果它们只是被 raise 后又统一 catch 成 `Exception`，就直接删除。

---

## 应该保留的错误类

以下类型可以保留：

```text
1. 有额外字段。
2. 跨模块边界传递结构化诊断信息。
3. 被上层明确捕获，并映射成不同 ToolError。
4. 不只是 Python exception，而是内部错误载体。
```

例如这种可以保留：

```python
class BrowserSessionError(Exception):
    def __init__(self, diagnostic_code: str, message: str) -> None:
        super().__init__(message)
        self.diagnostic_code = diagnostic_code
        self.message = message
```

它有实际价值，因为它从 `session.py` 向 action/runtime 层传递：

```text
diagnostic_code
message
```

然后上层可以映射为：

```text
BROWSER_UNAVAILABLE
```

---

# 二、推荐保留的异常类

当前阶段建议最多保留这些：

```text
BrowserSessionError
SnapshotScriptProtocolError
```

## `BrowserSessionError`

保留条件：

```text
1. 用于 session.py 内部浏览器启动/profile解析失败。
2. 携带 diagnostic_code。
3. 上层会转成 BROWSER_UNAVAILABLE。
```

示例：

```python
class BrowserSessionError(Exception):
    def __init__(self, diagnostic_code: str, message: str) -> None:
        super().__init__(message)
        self.diagnostic_code = diagnostic_code
        self.message = message
```

---

## `SnapshotScriptProtocolError`

可以保留，也可以不用异常类。

如果 snapshot JS 协议校验失败，需要和普通 `ValueError` 区分，可以保留：

```python
class SnapshotScriptProtocolError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
```

如果只是 raise 后在 `handle_snapshot` 里统一转成：

```text
INTERNAL_ERROR + diagnostic_code="SNAPSHOT_PROTOCOL_ERROR"
```

那它仍然有一点价值。

但如果它只是：

```python
class SnapshotScriptProtocolError(ValueError):
    pass
```

也可以删除，直接用：

```python
raise ValueError("Invalid snapshot script payload")
```

我的建议：

```text
如果你们想极简：删除 SnapshotScriptProtocolError，用 ValueError。
如果你们想保留协议边界：保留，但不要只写 pass，至少加 message 字段或清晰 docstring。
```

---

# 三、应删除的典型错误类

全局搜索：

```text
class .*Error
class .*Exception
pass
```

重点检查这些类型：

```text
ActionError
NavigationError
ClickError
FillError
SnapshotError
RefError
InterventionError
ToolProtocolError
BrowserProfileError
ConfigError
ResolverError
```

只要它们没有字段，也没有被差异化捕获，就删除。

---

# 四、替换策略

## 1. 原来 raise 自定义 pass error

例如：

```python
raise SnapshotError("Snapshot failed")
```

改成：

```python
raise RuntimeError("Snapshot failed")
```

或者如果这是协议校验：

```python
raise ValueError("Invalid snapshot payload")
```

如果最终都会在 handler 里捕获并转为 ToolError，那么普通标准异常足够。

---

## 2. 原来 except 自定义 pass error

例如：

```python
except SnapshotError as error:
    return build_error_response(...)
```

如果没有特殊处理，改成更通用的：

```python
except Exception as error:
    return build_error_response(...)
```

但要注意：**不要让错误响应退化**。

原来如果返回：

```text
INTERNAL_ERROR + diagnostic_code="SNAPSHOT_FAILED"
```

清理后仍然要返回：

```text
INTERNAL_ERROR + diagnostic_code="SNAPSHOT_FAILED"
```

---

## 3. 如果不同异常只是为了 diagnostic_code

不要用异常类型区分，直接在捕获点指定 diagnostic_code。

例如不要这样：

```python
class FillError(Exception):
    pass


class ClickError(Exception):
    pass
```

而是在 action handler 里：

```python
make_action_failed_error(
    action_type="fill_ref",
    message="Fill action failed.",
    diagnostic_code="FILL_FAILED",
    context={...},
)
```

和：

```python
make_action_failed_error(
    action_type="click_ref",
    message="Click action failed.",
    diagnostic_code="CLICK_FAILED",
    context={...},
)
```

---

# 五、browser_profile 模块清理原则

`browser_profile` 里如果有 pass-only error class，也要清理。

但是这里要区分：

```text
ResolveFailure / ResolveSuccess 这类 dataclass 结果模型应该保留。
pass-only exception class 应删除。
```

如果 profile resolver 已经使用：

```text
ResolveFailure
ResolveFailureReason
ResolveResult
```

那就不需要再有：

```python
class BrowserProfileError(Exception):
    pass
```

profile 解析失败应该通过：

```python
ResolveFailure(...)
```

返回，而不是抛一个空异常类。

---

# 六、最终推荐错误流

## session/browser 启动失败

```text
session.py
  profile resolve failed / browser launch failed
  -> raise BrowserSessionError(diagnostic_code, message)

action_runtime.py / actions
  catch BrowserSessionError
  -> make_browser_unavailable_error(...)
```

---

## action 执行失败

```text
actions/ref.py
  Playwright click/fill failed
  -> catch Exception
  -> make_action_failed_error(..., diagnostic_code="CLICK_FAILED" / "FILL_FAILED")
```

不需要：

```text
ClickError
FillError
```

---

## snapshot 脚本协议失败

二选一。

### 极简方案

```text
snapshot.py
  invalid snapshot JS payload
  -> raise ValueError

actions/observation.py
  catch Exception
  -> INTERNAL_ERROR + diagnostic_code="SNAPSHOT_FAILED"
```

### 稍微明确方案

```text
snapshot.py
  invalid snapshot JS payload
  -> raise SnapshotScriptProtocolError

actions/observation.py
  catch SnapshotScriptProtocolError
  -> INTERNAL_ERROR + diagnostic_code="SNAPSHOT_PROTOCOL_ERROR"
```

如果选第二种，`SnapshotScriptProtocolError` 不要只是 pass。

---

# 七、完成后代码中不应存在

```python
class SomeError(Exception):
    pass
```

```python
class SomeError(RuntimeError):
    pass
```

```python
class SomeError(ValueError):
    pass
```

除非有非常明确的注释说明它作为公共异常边界存在。当前 browse_interact 不需要这种设计。

---

# 八、给 Codex 的执行提示词

```text
请清理 browse_interact 和 browser_profile 中无意义的 pass-only 错误类。

背景：
当前项目已经有结构化 agent-facing 错误协议：
- ToolError
- ToolErrorCode
- ErrorCategory
- diagnostic_code
- recovery_hint

因此不需要大量只有 pass 的 Python exception class。
这些 pass-only error class 只是开发方便，会增加维护噪音。

目标：
1. 全局搜索所有 class XXXError / XXXException。
2. 删除类体只有 pass 的异常类。
3. 如果异常类没有额外字段、没有自定义 __init__、没有差异化捕获逻辑，则删除。
4. 如果原来 raise 这些 pass-only error，改为标准异常：
   - ValueError 用于参数/协议结构错误
   - RuntimeError 用于运行时失败
   - Exception 捕获 Playwright 或未知运行时错误
5. 如果原来 except 这些 pass-only error，但处理逻辑和普通异常一样，则改为捕获 Exception 或更合适的标准异常。
6. 不改变 agent-facing ToolError 协议。
7. 不改变 error.category / error.code / diagnostic_code / recovery_hint。
8. 不改变 action 行为。
9. 不改变 snapshot/ref 生命周期。
10. 不引入新的异常层级。

允许保留：
1. BrowserSessionError，前提是它携带 diagnostic_code 和 message，并被上层映射为 BROWSER_UNAVAILABLE。
2. SnapshotScriptProtocolError，前提是它真的用于区分 snapshot JS 协议错误；如果保留，不要只写 pass，至少实现 __init__ 或明确 docstring。
3. dataclass 结果模型，例如 ResolveFailure / ResolveSuccess，不属于异常类，不要删除。

重点清理：
- ActionError
- NavigationError
- ClickError
- FillError
- SnapshotError
- RefError
- InterventionError
- BrowserProfileError
- ConfigError
- ResolverError
- ToolProtocolError
- 任何 class XXXError(Exception): pass
- 任何 class XXXError(RuntimeError): pass
- 任何 class XXXError(ValueError): pass

替换原则：
- 用 diagnostic_code 表达具体失败原因，不用异常类名表达。
- 用 ToolError 表达 agent-facing 错误，不用 Python exception class 表达。
- Python exception 只作为内部控制流或异常传播机制，不作为协议模型。

禁止：
- 不要删除 ToolError / ToolErrorCode / ErrorCategory。
- 不要把结构化错误改成纯字符串。
- 不要把 Playwright 原始异常直接返回给 agent。
- 不要改变 fill_ref / click_ref / navigate / snapshot 行为。
- 不要改文件结构。
- 不要引入新框架。
- 不要用 assert 替代错误处理。

完成后：
- 全局不应再出现 class XXXError(...): pass。
- 保留下来的异常类必须有字段、__init__、或清晰注释说明其跨层边界意义。
- 所有 agent-facing 错误仍然通过 ToolError 返回。
```

---

# 九、最终判断

这轮应该清理。

你现在的错误协议已经足够表达：

```text
发生了什么
属于哪类错误
agent 是否可恢复
是否需要用户介入
内部诊断码是什么
恢复工具状态需要什么
```

所以大量空异常类只会制造噪音。

最终原则：

```text
Python exception 用于内部异常传播；
ToolError 用于 agent-facing 协议；
diagnostic_code 用于细分失败原因；
不要用一堆 pass-only exception class 模拟错误体系。
```

```
```
