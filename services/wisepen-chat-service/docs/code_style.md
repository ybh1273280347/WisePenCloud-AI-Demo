````md
# 代码协作与工程风格规范

## 0. 目标

本文档用于约束代码生成、重构、清理和 review 的工程风格。

核心目标：

```text
1. 删除无意义防御。
2. 避免伪抽象。
3. 减少噪音代码。
4. 降低维护者认知负担。
5. 保持代码边界清晰。
6. 让正常路径足够直接。
````

本规范不讨论具体业务实现，只约束通用代码风格。

---

# 1. 不做宽容式参数转换

## 1.1 原则

如果输入结构已经有明确约定，代码中不要再写“猜测式转换”。

禁止把错误类型悄悄转换成可用值。

错误示例：

```python
def normalize_bool(value):
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}

    return bool(value)
```

问题：

```text
1. "false" 这类字符串容易被错误处理。
2. bool("false") 是 True。
3. 调用方传错类型时，错误被隐藏。
4. 正常逻辑被防御代码淹没。
```

正确做法：

```python
enabled = options.get("enabled", False)
```

如果输入契约要求 `enabled` 是布尔值，就直接按布尔值使用。

---

## 1.2 不接受字符串数字

禁止：

```python
count = int(value)
```

如果约定 `count` 是整数，就只接受整数。

错误示例：

```python
def normalize_count(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
```

正确示例：

```python
count = options.get("count", DEFAULT_COUNT)
count = max(1, min(count, MAX_COUNT))
```

说明：

```text
字段缺失可以使用默认值。
字段存在时应按原始约定类型使用。
数值范围限制可以保留。
```

---

## 1.3 不接受字符串布尔值

禁止支持：

```text
"true"
"false"
"yes"
"no"
"1"
"0"
```

错误示例：

```python
def parse_enabled(value):
    return str(value).lower() in {"true", "1", "yes"}
```

正确示例：

```python
enabled = options.get("enabled", False)
```

---

## 1.4 不做单值自动包装列表

禁止：

```python
if isinstance(items, str):
    items = [items]
```

如果约定是列表，就只处理列表。

正确示例：

```python
items = options.get("items", [])
```

---

## 1.5 字段缺失和字段错误要区分

允许：

```python
limit = options.get("limit", DEFAULT_LIMIT)
```

不允许：

```python
try:
    limit = int(options.get("limit"))
except Exception:
    limit = DEFAULT_LIMIT
```

原则：

```text
字段缺失：可以默认。
字段类型错误：不要猜，不要吞。
```

---

# 2. 减少无意义 `isinstance`

## 2.1 只在真实边界检查类型

允许在以下位置做结构校验：

```text
1. JSON 反序列化结果。
2. 外部接口返回值。
3. 文件读取结果。
4. 跨语言调用结果。
5. 不可信数据源。
6. 安全边界，例如 path、URL、selector、id。
```

示例：

```python
data = json.loads(raw)

if not isinstance(data, dict):
    raise ValueError("invalid payload")
```

这是合理的。

---

## 2.2 内部对象不要层层检查

如果对象只由内部代码构造，不要重复检查。

错误示例：

```python
def build_error_payload(error):
    if not isinstance(error, ErrorPayload):
        raise TypeError("error must be ErrorPayload")

    return {
        "code": error.code,
        "message": error.message,
    }
```

正确示例：

```python
def build_error_payload(error):
    return {
        "code": error.code,
        "message": error.message,
    }
```

---

## 2.3 不要为了心理安全写检查

错误示例：

```python
if isinstance(name, str):
    name = name.strip()
else:
    name = ""
```

如果 `name` 的来源已经明确是字符串，直接写：

```python
name = name.strip()
```

---

# 3. 不写空壳类和伪抽象

## 3.1 禁止只有默认值的配置类

错误示例：

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class LockRule:
    prefixes: tuple[str, ...] = ("SingletonLock",)


DEFAULT_LOCK_RULE = LockRule()
```

正确示例：

```python
LOCK_FILE_PREFIXES = ("SingletonLock",)
```

---

## 3.2 单一默认实例不要建类

错误示例：

```python
@dataclass(frozen=True)
class RetryOptions:
    count: int = 3
    delay_seconds: float = 0.5


DEFAULT_RETRY_OPTIONS = RetryOptions()
```

如果没有多个实例、没有外部注入、没有行为方法，改成常量：

```python
RETRY_COUNT = 3
RETRY_DELAY_SECONDS = 0.5
```

---

## 3.3 什么时候可以保留 dataclass

以下情况可以保留：

```text
1. 表达真实领域对象。
2. 表达结构化结果。
3. 表达跨层传递的数据。
4. 有多个合法实例。
5. 会被调用方显式构造。
6. 有明确行为或派生属性。
```

示例：

```python
@dataclass(frozen=True)
class ParseResult:
    success: bool
    content: str
    error_message: str | None = None
```

这是有意义的数据结构。

---

## 3.4 判断一个类是否是伪抽象

看到一个类时，按以下问题判断：

```text
1. 它是否只有字段，没有方法？
2. 它是否只有一个 DEFAULT 实例？
3. 它是否没有多个变体？
4. 它是否没有被外部传入？
5. 它是否只是把常量包了一层？
```

如果答案大多为“是”，优先删除，改成常量。

---

# 4. 禁止 pass-only 异常类

## 4.1 不写空异常类

错误示例：

```python
class SnapshotError(Exception):
    pass
```

错误示例：

```python
class ConfigError(RuntimeError):
    pass
```

这类异常类没有信息承载能力，只是噪音。

---

## 4.2 用普通异常或结构化结果表达失败

如果只是运行时失败：

```python
raise RuntimeError("snapshot failed")
```

如果是参数或结构错误：

```python
raise ValueError("invalid snapshot payload")
```

如果需要返回结构化失败结果，使用明确的数据结构：

```python
@dataclass(frozen=True)
class Failure:
    reason: str
    message: str
```

---

## 4.3 什么时候可以保留自定义异常

只有满足以下条件时保留：

```text
1. 有自定义字段。
2. 有明确跨层语义。
3. 会被上层差异化捕获。
4. 不只是类型标签。
```

示例：

```python
class SessionError(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
```

---

# 5. 模块命名规范

## 5.1 禁止万能文件名

禁止新增：

```text
common.py
utils.py
helpers.py
misc.py
shared.py
base.py
```

这些名字通常意味着：

```text
不知道放哪，先放这里。
```

---

## 5.2 文件名必须表达真实职责

错误示例：

```text
utils.py
  normalize_key
  redact_url
  build_result
  get_session
```

正确示例：

```text
keyboard.py
  normalize_key
  split_keys

protocol.py
  build_result
  build_error

session.py
  get_session
  create_session
```

---

## 5.3 按稳定领域拆文件

优先按领域拆：

```text
session.py
snapshot.py
protocol.py
intervention.py
```

不要按函数形态拆：

```text
validators.py
formatters.py
builders.py
helpers.py
```

---

## 5.4 不过度微模块化

如果一个文件只有两三个简单函数，且不能形成稳定领域，优先合并到所属领域模块。

错误示例：

```text
keyboard_input.py
url_safety.py
result_builder.py
error_factory.py
models.py
```

如果这些文件都很薄，可以收敛为：

```text
protocol.py
control.py
navigation.py
```

---

# 6. 模块级下划线规范

## 6.1 模块级不要用 `_xxx`

禁止：

```python
_ALLOWED_MODES = {"full", "focused"}


def _format_line(item):
    ...
```

改成：

```python
ALLOWED_MODES = {"full", "focused"}


def format_line(item):
    ...
```

---

## 6.2 用 `__all__` 表达导出边界

示例：

```python
__all__ = [
    "SnapshotManager",
    "ref_selector",
]
```

未出现在 `__all__` 中的模块级对象，即使没有下划线，也视为模块内部实现。

---

## 6.3 class 内部可以用下划线

允许：

```python
class Manager:
    def __init__(self) -> None:
        self._state = None

    def _cleanup(self) -> None:
        ...
```

规则：

```text
模块级：不用单下划线。
class 内部：可以用单下划线。
Python dunder：保留。
```

---

# 7. 常量规范

## 7.1 不要全局常量桶

禁止创建一个大而全的：

```text
constants.py
```

然后塞入所有常量。

---

## 7.2 常量放到拥有它的模块

示例：

```text
SESSION_ID_LENGTH -> session.py
SNAPSHOT_ID_LENGTH -> snapshot.py
LOCK_FILE_PREFIXES -> lock.py
DEFAULT_TIMEOUT -> 对应使用它的模块
```

原则：

```text
谁拥有语义，常量放谁旁边。
```

---

# 8. 响应和结果对象规范

## 8.1 不返回调试字段

禁止在对外结果中加入：

```text
debug
diagnostics
trace
operation_count
created_at
last_used_at
internal_state
```

这些信息应进入日志，而不是进入业务结果。

---

## 8.2 结果只包含调用方需要的信息

错误示例：

```json
{
  "success": true,
  "created_at": "...",
  "operation_count": 8,
  "diagnostics": {
    "visitedNodeCount": 100
  }
}
```

正确示例：

```json
{
  "success": true,
  "result": {
    "id": "abc",
    "content": "..."
  }
}
```

---

# 9. 错误设计规范

## 9.1 不制造过多错误类型

不要为每个细节建一个错误类。

错误示例：

```python
class ClickFailed(Exception):
    pass


class FillFailed(Exception):
    pass


class NavigationTimeout(Exception):
    pass
```

更好的方式：

```python
@dataclass(frozen=True)
class ErrorResult:
    category: str
    code: str
    message: str
```

用字段表达分类，不用空类堆层级。

---

## 9.2 内部异常和对外错误分离

内部可以抛异常。

对外应该返回结构化结果。

原则：

```text
内部异常用于控制流和中断。
对外错误用于协议和恢复。
不要把内部异常原样暴露。
```

---

# 10. 默认值规范

## 10.1 默认值只处理缺失

允许：

```python
timeout = options.get("timeout", DEFAULT_TIMEOUT)
```

禁止：

```python
try:
    timeout = int(options.get("timeout"))
except Exception:
    timeout = DEFAULT_TIMEOUT
```

---

## 10.2 默认值不负责纠错

默认值不是错误输入的兜底。

错误输入应该暴露，而不是被吞掉。

---

# 11. 典型反模式

## 11.1 宽容 bool

```python
def normalize_bool(value):
    return str(value).lower() in {"true", "1", "yes"}
```

删除。

---

## 11.2 宽容 int

```python
def normalize_int(value, default):
    try:
        return int(value)
    except Exception:
        return default
```

删除。

---

## 11.3 空异常

```python
class ParseError(Exception):
    pass
```

删除。

---

## 11.4 空壳配置类

```python
@dataclass(frozen=True)
class Rule:
    value: str = "default"


DEFAULT_RULE = Rule()
```

改成常量。

---

## 11.5 万能文件

```text
common.py
utils.py
helpers.py
```

重命名或拆分到明确领域。

---

## 11.6 模块级下划线泛滥

```python
_DEFAULT_VALUE = 1


def _helper():
    ...
```

改成无下划线，并用 `__all__` 控制导出。

---

# 12. Review 检查清单

每次 review 时检查：

```text
[ ] 是否出现 common.py / utils.py / helpers.py？
[ ] 是否出现 pass-only exception？
[ ] 是否出现单实例 dataclass 配置壳？
[ ] 是否出现 normalize_bool / normalize_int / coerce_xxx？
[ ] 是否对明确类型输入做了 int/float/bool/str 宽容转换？
[ ] 是否出现无意义 isinstance？
[ ] 是否把调试字段放进对外结果？
[ ] 是否模块级使用了 _xxx？
[ ] 是否每个对外模块都有 __all__？
[ ] 是否为了未来可能性引入了当前不需要的机制？
[ ] 是否把简单逻辑拆成过多薄文件？
[ ] 是否存在只为“看起来更工程化”的抽象？
```

---

# 13. 总结原则

```text
不要宽容错误输入。
不要伪装抽象。
不要空异常类。
不要万能文件。
不要调试字段污染结果。
不要模块级下划线。
不要为了未来想象写复杂机制。
不要用代码噪音掩盖边界不清。

缺失可以默认。
错误不要吞。
常量优先于空壳类。
结构化结果优先于空异常。
真实边界才校验。
稳定领域才拆文件。
模块导出靠 __all__。
正常路径应该清楚、直接、短。
```

```
```
