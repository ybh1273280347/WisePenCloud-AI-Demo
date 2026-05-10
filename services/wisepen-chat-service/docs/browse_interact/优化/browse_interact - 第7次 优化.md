请对项目做一轮“无意义 dataclass / 空壳配置对象 / 伪抽象”清理。

背景：
当前代码中存在一些只有一两个字段、只有默认值、没有行为、没有多态、没有实际配置来源的 dataclass，例如：

@dataclass(frozen=True)
class LockRule:
    prefixes: tuple[str, ...] = ("SingletonLock",)

DEFAULT_LOCK_RULE = LockRule()

这类抽象没有提供真实建模价值，只增加跳转和认知负担。
如果全项目只有一个默认实例、没有多个实现、没有运行时配置、没有行为方法，就应该删除，改成直接常量或普通函数参数。

目标：
1. 删除无意义的 dataclass 配置壳。
2. 删除 DEFAULT_XXX = XxxConfig() / XxxRule() 这种只有默认实例的伪抽象。
3. 将单字段或少字段默认配置对象改为清晰常量。
4. 保留真正表达领域状态、协议结构、跨层返回结果的 dataclass。
5. 不改变任何业务行为。
6. 不改变 tool schema。
7. 不改变错误协议。
8. 不改变 snapshot/ref 生命周期。
9. 不改变 browser_profile resolver 语义。

需要清理的典型模式：

1. 单字段 dataclass + 单默认实例

例如：

@dataclass(frozen=True)
class LockRule:
    prefixes: tuple[str, ...] = ("SingletonLock",)

DEFAULT_LOCK_RULE = LockRule()

改为：

LOCK_FILE_PREFIXES = ("SingletonLock",)

调用处从：

for prefix in DEFAULT_LOCK_RULE.prefixes:
    ...

改为：

for prefix in LOCK_FILE_PREFIXES:
    ...

2. 只有默认值、没有行为、没有多个实例的配置对象

例如：

@dataclass(frozen=True)
class BrowserCheckRule:
    timeout_ms: int = 1000
    retry_count: int = 2

DEFAULT_BROWSER_CHECK_RULE = BrowserCheckRule()

如果没有外部传入不同 BrowserCheckRule，则改为：

BROWSER_CHECK_TIMEOUT_MS = 1000
BROWSER_CHECK_RETRY_COUNT = 2

3. 只是把常量包一层的 dataclass

例如：

@dataclass(frozen=True)
class ProfileConfigNames:
    file_name: str = "browser_profile.json"
    profile_key: str = "profile_dir"

DEFAULT_PROFILE_CONFIG_NAMES = ProfileConfigNames()

改为：

PROFILE_CONFIG_FILE_NAME = "browser_profile.json"
PROFILE_CONFIG_PROFILE_KEY = "profile_dir"

4. 没有行为的 Rule / Config / Options / Settings，且只在一个模块内部使用

如果只在一个模块内部使用，直接改成模块级常量。
不要为了“看起来可扩展”保留 dataclass。

应该保留的 dataclass：

1. agent-facing 协议模型

例如：
PageState
SessionState
RecoveryHint
UserActionRequest
ToolError
SnapshotPayload
InterventionSignal
ActionResult

这些是工具协议结构，必须保留。

2. browser_profile resolver 的结果模型

例如：
ResolveSuccess
ResolveFailure
ProfileDirCheck
ResolveResult 相关模型

这些表达 resolver 的结构化结果，不是伪抽象，必须保留。

3. 具有多个合法实例、会被调用方传入、或者确实表达领域对象的 dataclass

例如：
如果某个 Options 对象真的由外部构造并传入多个模块，可以保留。
如果某个 Rule 有多个不同实例组成规则集，可以保留。
如果 dataclass 有方法、校验逻辑、派生属性，谨慎保留。

4. 已经作为公开 API 的 dataclass

如果外部模块或用户代码可能直接 import 并构造，不要轻易删除。
但当前未上线、内部模块可以破坏性清理。

删除判断标准：

对于每个 dataclass，请判断：

1. 它是否只有字段，没有方法？
2. 是否只有一个 DEFAULT_XXX 实例？
3. 是否没有任何调用方传入自定义实例？
4. 是否没有多个 rule/config 组合？
5. 是否没有作为 agent-facing/tool-facing 协议返回？
6. 是否没有作为 resolver/action 的结构化结果？
7. 删除后是否可以用一组模块级常量表达得更清楚？

如果以上大部分为是，则删除 dataclass。

替换规则：

1. 单字段 dataclass：
   改成一个模块级常量。

2. 少字段但强相关的配置：
   改成几个带清晰前缀的模块级常量。

3. 只为函数提供默认参数的 dataclass：
   改成函数默认参数或模块常量。

4. 只用于读取 attribute 的默认实例：
   把 `DEFAULT_X.y` 改成 `X_Y` 常量。

命名规则：

1. 常量使用全大写。
2. 常量名要表达具体用途，不要叫 RULE / CONFIG 这种泛名。
3. 例如：
   DEFAULT_LOCK_RULE.prefixes
   -> LOCK_FILE_PREFIXES

   DEFAULT_CHECK_RULE.timeout_ms
   -> PROFILE_CHECK_TIMEOUT_MS

   DEFAULT_CONFIG_NAMES.file_name
   -> PROFILE_CONFIG_FILE_NAME

禁止事项：

1. 不要删除协议 dataclass。
2. 不要删除 resolver 结果 dataclass。
3. 不要删除真正有多个实例或外部传入的配置对象。
4. 不要引入新的 Config / Rule / Options dataclass 替代旧的。
5. 不要把清理变成大规模重构。
6. 不要修改业务行为。
7. 不要修改错误码。
8. 不要修改 tool response。
9. 不要修改 snapshot/ref 生命周期。
10. 不要修改 browser session 行为。
11. 不要引入 Pydantic、attrs 或其他配置框架。
12. 不要保留兼容 alias，功能未上线，可以直接替换。

执行步骤：

1. 全局搜索：
   - @dataclass
   - DEFAULT_
   - Rule
   - Config
   - Options
   - Settings

2. 对每个 dataclass 分类：
   - protocol_model：保留
   - result_model：保留
   - domain_entity：保留
   - meaningful_options：谨慎保留
   - constant_wrapper：删除
   - single_default_rule：删除

3. 对所有 constant_wrapper / single_default_rule：
   - 删除 dataclass 定义
   - 删除 DEFAULT_XXX 实例
   - 添加清晰模块级常量
   - 更新所有调用点

4. 删除因此不再需要的 dataclasses import。

5. 输出清理报告：
   - 删除了哪些 dataclass
   - 替换成了哪些常量
   - 哪些 dataclass 被保留以及保留理由

示例：

清理前：

@dataclass(frozen=True)
class LockRule:
    prefixes: tuple[str, ...] = ("SingletonLock",)

DEFAULT_LOCK_RULE = LockRule()


def is_lock_file(path: Path) -> bool:
    return any(path.name.startswith(prefix) for prefix in DEFAULT_LOCK_RULE.prefixes)

清理后：

LOCK_FILE_PREFIXES = ("SingletonLock",)


def is_lock_file(path: Path) -> bool:
    return any(path.name.startswith(prefix) for prefix in LOCK_FILE_PREFIXES)

最终要求：

- 不再存在只有一个 DEFAULT 实例的单字段 Rule/Config dataclass。
- 不再存在只是包装常量的 dataclass。
- 保留的 dataclass 必须有明确协议、结果或领域语义。