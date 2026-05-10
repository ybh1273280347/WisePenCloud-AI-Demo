````md
# 浏览器交互工具瘦身重构执行文档

## 目标

当前代码已经完成了一轮 agent-safe 架构重构，但总代码量从约 800 行膨胀到接近 2000 行。膨胀的主要原因不是功能增加本身，而是：

1. 错误响应模板重复。
2. action handler 重复构造 `session_state` / `page_state` / `ToolError`。
3. `actions.py` 承担过多具体动作。
4. browser profile resolver 单文件包含 catalog、models、paths、checker、config、resolver、presenter。
5. snapshot JS 大段嵌在 Python 文件中。
6. 结构化 agent 协议是必要的，但很多样板代码可以收敛。

本次瘦身目标不是回到原来的 800 行，而是让每个模块职责更窄、文件更短、review 成本更低。

最终目标：

```text
每个核心文件控制在 100-250 行左右。
保留 agent-safe 协议。
保留结构化错误码。
保留 persistent context。
保留 automation profile resolver。
减少重复 response 构造。
避免新的上帝文件。
````

---

## 非目标

本次不做以下事情：

```text
1. 不改变工具对外协议字段名。
2. 不删除 browser_session_id / session / page / error_code / recommended_next_action。
3. 不把结构化错误退化成字符串错误。
4. 不改回普通 playwright.launch()。
5. 不默认使用用户系统 Chrome 主 profile。
6. 不引入复杂泛型或过度类型抽象。
7. 不引入 oneOf JSON Schema。
```

---

## 当前模块问题摘要

### `actions.py`

当前问题：

```text
1. 每个 handler 重复构造 build_error_response。
2. 每个 handler 重复 get_session_state / get_page_state。
3. ToolError 在 handler 内大量手写。
4. 所有 action 放在一个文件，阅读压力高。
5. session 获取 helper 和具体 action 混在一起。
```

需要瘦身。

---

### `browser profile resolver`

当前问题：

```text
1. catalog、models、paths、checker、config、resolver、presenter 在一个文件。
2. 单文件承担过多职责。
3. 任意小改动都会影响整个文件 review。
```

需要拆包。

---

### `snapshot.py`

当前问题：

```text
1. Python snapshot 生命周期逻辑和大段 JS 字符串混在一起。
2. JS 很长，影响 Python 文件阅读。
```

可以把 JS 移出。

---

## 执行顺序

建议严格按以下顺序执行，避免一次性大改导致不可 review。

```text
Phase 1: 收敛 actions.py 的重复 response helper
Phase 2: 拆分 actions.py 为 actions/ 包
Phase 3: 拆分 browser profile resolver 为 browser_profile/ 包
Phase 4: 移出 snapshot JS
Phase 5: 清理残留重复和死代码
```

每完成一个 Phase，必须跑测试或至少跑 import 检查。

---

# Phase 1：先瘦身 `actions.py`，但暂不拆文件

## 目标

在不改变文件结构的前提下，先消除重复模板。

新增这些 helper：

```python
async def _build_action_error_response(
    session_manager: BrowserSessionManager,
    page,
    error: ToolError,
) -> str:
    return build_error_response(
        session_state=get_session_state(
            session_manager.session_id,
            valid=session_manager.is_session_alive,
        ),
        page_state=await get_page_state(page),
        error=error,
    )


def _build_session_state(
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
    )
```

然后把重复代码：

```python
return build_error_response(
    session_state=get_session_state(
        session_manager.session_id,
        valid=session_manager.is_session_alive,
    ),
    page_state=await get_page_state(page),
    error=ToolError(...),
)
```

改成：

```python
return await _build_action_error_response(
    session_manager,
    page,
    ToolError(...),
)
```

---

## 需要修改的地方

重点替换这些 handler 内的错误响应：

```text
handle_navigate
_handle_navigation_direction
handle_snapshot
handle_screenshot
handle_click_ref
handle_fill_ref
handle_scroll
handle_key
handle_wait
handle_get_content
```

---

## 成功标准

完成后：

```text
1. actions.py 行数减少。
2. 所有错误响应仍然包含 session/page/error_code/retryable/recommended_next_action。
3. 行为不变。
4. 不引入 ActionContext。
5. 不改 dispatcher handler 签名。
```

---

# Phase 2：拆分 `actions.py`

## 目标目录结构

把当前 `actions.py` 拆成包：

```text
browser_interact/
  actions/
    __init__.py
    common.py
    status.py
    navigation.py
    snapshot_actions.py
    ref_actions.py
    input_actions.py
    content.py
```

---

## 文件职责

### `actions/common.py`

放公共 helper：

```python
_get_existing_page_or_error
_get_or_create_page_or_error
_build_action_error_response
_build_session_state
```

如果需要，也可以放：

```python
_invalid_ref_response
_schema_error_response
```

但不要过度抽象。

---

### `actions/status.py`

只放：

```python
handle_status
```

`status` 规则：

```text
1. 不创建 session。
2. 有 browser_session_id 时校验。
3. 没有 session 时返回 page=null。
4. session alive 时推荐 snapshot。
5. session 不存在或过期时推荐 navigate。
```

---

### `actions/navigation.py`

放：

```python
handle_navigate
handle_go_back
handle_go_forward
_handle_navigation_direction
```

规则：

```text
navigate 使用 get_or_create_page。
go_back / go_forward 使用 get_existing_page。
导航类动作成功后 invalidate snapshot。
导航类动作成功后检测 user intervention。
```

---

### `actions/snapshot_actions.py`

放：

```python
handle_snapshot
handle_screenshot
```

规则：

```text
snapshot 使用 get_existing_page。
screenshot 使用 get_existing_page。
snapshot 成功返回 SnapshotPayload。
screenshot 成功返回 base64 jpeg。
```

---

### `actions/ref_actions.py`

放：

```python
handle_click_ref
handle_fill_ref
```

规则：

```text
1. 使用 get_existing_page。
2. 必须校验 snapshot_id。
3. 必须通过 snapshot_manager.require_current。
4. 必须通过 ref_selector(ref) 构造 selector。
5. 不允许手写 [data-ref=...] 或 [data-agent-ref=...]。
6. click/fill 成功后 invalidate snapshot。
```

---

### `actions/input_actions.py`

放：

```python
handle_scroll
handle_key
handle_wait
```

规则：

```text
scroll/key/wait 都使用 get_existing_page。
scroll/key 成功后 invalidate snapshot。
wait 不一定 invalidate snapshot。
wait duration 需要限制在 0 到 WAIT_DURATION_MAX_S。
```

---

### `actions/content.py`

放：

```python
handle_get_content
```

规则：

```text
get_content 使用 get_existing_page。
保留 content 和 content_length。
后续如 payload 过大，再单独做截断策略。
```

---

### `actions/__init__.py`

导出 handler，保持 dispatcher import 简单：

```python
from .status import handle_status
from .navigation import handle_navigate, handle_go_back, handle_go_forward
from .snapshot_actions import handle_snapshot, handle_screenshot
from .ref_actions import handle_click_ref, handle_fill_ref
from .input_actions import handle_scroll, handle_key, handle_wait
from .content import handle_get_content
```

这样 `dispatcher.py` 不需要大改。

---

## 成功标准

完成后：

```text
1. 原来的 actions.py 删除或变成兼容 re-export。
2. dispatcher.py 的 import 可以继续 from .actions import handle_xxx。
3. 每个 action 子文件不超过 250 行。
4. common.py 不变成新的上帝文件。
5. 所有 handler 签名保持不变：
   (
       session_manager,
       snapshot_manager,
       intervention,
       processor,
       browser_session_id,
       action,
   ) -> str
```

---

# Phase 3：拆分 browser profile resolver

## 目标目录结构

把原来的 profile resolver 单文件拆成：

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

---

## `browser_profile/constants.py`

只放常量：

```python
APP_NAME = "WisePenCloud"
CONFIG_FILE_NAME = "config.json"
CONFIG_PROFILE_KEY = "automation_user_data_dir"
CONFIG_CHANNEL_KEY = "browser_channel"

BROWSER_CHANNELS = ("chrome", "msedge", "chromium")
DEFAULT_BROWSER_CHANNEL = "chrome"
SUPPORTED_PLATFORMS = {"win32", "darwin", "linux"}
```

不 import logger，不读环境变量。

---

## `browser_profile/catalog.py`

只放系统浏览器 catalog：

```python
PathBase
PathTemplate
SystemBrowserDefinition
SYSTEM_BROWSER_CATALOG
```

注意：

```text
catalog 只用于识别系统浏览器主 User Data 目录并给 warning。
resolver 默认不应该选择系统主 profile。
```

---

## `browser_profile/models.py`

只放：

```python
ProfileDirCheck
ResolveSource
ResolveFailureReason
ResolveSuccess
ResolveFailure
ResolveResult
```

不要 import：

```text
os
sys
json
common.logger
```

---

## `browser_profile/paths.py`

放路径计算：

```python
default_automation_profile_dir
default_config_file
find_system_browser_dir
normalize_channel
mask_home
```

以及内部 helper：

```python
_resolve_home
_resolve_env
_resolve_template_base
```

规则：

```text
1. 不在默认参数里调用 Path.home()。
2. 不在默认参数里绑定 os.environ。
3. 所有 home/env 都支持注入。
4. paths.py 不做文件检查。
5. paths.py 不读写配置。
```

---

## `browser_profile/checker.py`

放 profile 可用性检查：

```python
LockRule
DEFAULT_LOCK_RULE
is_profile_locked
check_profile_dir
ensure_directory
```

规则：

```text
1. locked 视为不可用。
2. readable 要检查 R_OK 和 X_OK。
3. writable 默认使用 os.access(path, os.W_OK)。
4. probe_writable=True 时创建探针文件并立即清理。
5. 不可写目录必须返回 writable=False。
```

---

## `browser_profile/config.py`

放配置读写：

```python
AutomationProfileConfig
```

规则：

```text
1. 只负责 load/save。
2. 不检查 profile 是否 usable。
3. 不决定 resolver 策略。
4. 无效 browser_channel 读取时忽略 channel。
5. 保存失败返回 warning 字符串。
```

---

## `browser_profile/resolver.py`

只放：

```python
BrowserAutomationProfileResolver
```

负责策略：

```text
1. 校验平台。
2. 校验 browser_channel。
3. CLI profile 优先。
4. persisted profile 次之。
5. default automation profile 最后。
6. CLI 目录不存在时可以创建。
7. 默认 profile 不存在时创建。
8. locked profile 返回 PROFILE_LOCKED。
9. default profile 创建失败返回 PROFILE_UNAVAILABLE。
10. unsupported platform 只在平台不支持时返回。
```

---

## `browser_profile/presenter.py`

只放：

```python
describe_resolve_result
_describe_success
_describe_failure
_summarize_check
```

中文文案集中在这里。

---

## `browser_profile/__init__.py`

导出稳定 API：

```python
from .config import AutomationProfileConfig
from .models import (
    ProfileDirCheck,
    ResolveFailure,
    ResolveFailureReason,
    ResolveResult,
    ResolveSource,
    ResolveSuccess,
)
from .presenter import describe_resolve_result
from .resolver import BrowserAutomationProfileResolver
```

---

## 修改外部 import

`session.py` 从：

```python
from chat.application.browser_data_detector import (
    BrowserAutomationProfileResolver,
    ResolveFailure,
    describe_resolve_result,
)
```

改成：

```python
from chat.application.browser_profile import (
    BrowserAutomationProfileResolver,
    ResolveFailure,
    ResolveFailureReason,
    describe_resolve_result,
)
```

如果旧模块不需要兼容，删除旧文件。

---

## 成功标准

拆完后：

```text
1. resolver.py 只包含 BrowserAutomationProfileResolver。
2. presenter.py 不被 resolver.py import。
3. catalog.py 不被 config.py import。
4. config.py 不 import resolver.py。
5. paths.py 不写日志。
6. checker.py 不写 resolver 策略。
7. session.py 只依赖 browser_profile 的公开 API。
```

---

# Phase 4：移出 snapshot JS

## 目标

把 `_SNAPSHOT_JS` 从 `snapshot.py` 移到独立 JS 文件：

```text
browser_interact/
  snapshot.py
  snapshot_script.js
```

---

## 修改方式

`snapshot.py` 中：

```python
from pathlib import Path

_SNAPSHOT_JS = (
    Path(__file__)
    .with_name("snapshot_script.js")
    .read_text(encoding="utf-8")
)
```

或者为了避免运行时反复读文件：

```python
_SNAPSHOT_JS = Path(__file__).with_name("snapshot_script.js").read_text(encoding="utf-8")
```

`snapshot_script.js` 内容直接放：

```javascript
() => {
  ...
}
```

注意不要加 `export`，因为 Playwright `page.evaluate()` 直接执行该字符串。

---

## 成功标准

```text
1. snapshot.py 行数明显下降。
2. snapshot_script.js 独立 review。
3. snapshot.py 仍然提供 SnapshotManager / ref_selector。
4. actions.py 不直接读取 JS。
```

---

# Phase 5：最后清理

## 全局搜索清单

完成拆分后全局搜索：

```text
[data-ref
data-ref
_SESSION
_SNAPSHOT
_WAIT
_SCROLL
_NAVIGATION
_SCREENSHOT
_REDIRECT
PAGE_CLOSED
code=ToolErrorCode.
valid=session_manager.has_session
```

应满足：

```text
1. 不再使用 data-ref，统一 data-agent-ref。
2. 不再跨模块使用下划线常量。
3. 不再使用 PAGE_CLOSED。
4. ToolError(code=ToolErrorCode.xxx) 改成 ToolErrorCode.xxx.value。
5. SessionState.valid 统一用 is_session_alive。
```

---

## 建议测试清单

至少覆盖这些行为：

```text
1. status 无 session 不创建浏览器，返回 success=true, page=null。
2. navigate 无 session 创建 session。
3. snapshot 无 session 返回 SESSION_NOT_FOUND 或 SESSION_REQUIRED。
4. click_ref 无 snapshot_id 返回 SNAPSHOT_REQUIRED。
5. click_ref stale snapshot_id 返回 STALE_REF。
6. click_ref 非法 ref 返回 INVALID_ACTION_SCHEMA。
7. fill_ref 非法 ref 返回 INVALID_ACTION_SCHEMA。
8. profile locked 返回 AUTOMATION_PROFILE_LOCKED。
9. invalid browser_channel 返回 BROWSER_LAUNCH_FAILED 或对应映射。
10. default profile 不存在时会创建。
11. default profile 不可写时失败。
12. persisted channel 与显式 browser_channel 冲突时跳过 persisted。
```

---

# `session.py` 需要同步的 profile failure 映射

拆分 profile resolver 后，`session.py` 不应把所有 `ResolveFailure` 都当成 locked。

建议：

```python
def _map_profile_failure_to_error_code(
    failure: ResolveFailure,
) -> ToolErrorCode:
    if failure.reason is ResolveFailureReason.PROFILE_LOCKED:
        return ToolErrorCode.AUTOMATION_PROFILE_LOCKED

    return ToolErrorCode.BROWSER_LAUNCH_FAILED
```

使用：

```python
if isinstance(profile, ResolveFailure):
    raise BrowserSessionError(
        _map_profile_failure_to_error_code(profile),
        describe_resolve_result(profile),
    )
```

---

# 验收标准

最终必须满足：

```text
1. browse_interact/tool.py 仍然只是 BaseTool adapter。
2. dispatcher.py 仍然只是 action dispatcher。
3. session.py 只负责 Playwright persistent context 生命周期。
4. actions/ 下按动作类型拆分，没有新的巨型 actions.py。
5. browser_profile/ 下按 catalog/models/paths/checker/config/resolver/presenter 拆分。
6. snapshot JS 独立为 snapshot_script.js。
7. 对外工具协议不变。
8. 错误码语义不变，PAGE_CLOSED 除外，统一收敛为 SESSION_EXPIRED。
9. 不引入 oneOf schema。
10. 不引入复杂泛型。
11. 不默认使用系统 Chrome 主 profile。
12. 默认使用工具专用 automation profile。
```

---

# 推荐给 Codex 的执行提示词

```text
请按以下阶段对当前 browser interaction 代码做瘦身重构。

目标不是改变功能，而是减少重复样板、拆分过大的文件、让每个模块职责单一。

必须保持：
- agent-safe response 协议不变
- browser_session_id/session/page/error_code/recommended_next_action 字段不变
- Playwright 使用 launch_persistent_context
- 默认使用工具专用 automation profile
- 不默认使用用户系统 Chrome 主 profile
- 不引入 oneOf JSON schema
- 不引入复杂泛型

执行顺序：
1. 在 actions.py 中先抽公共 helper，减少重复 build_error_response / get_session_state / get_page_state。
2. 将 actions.py 拆成 actions/ 包：
   common.py, status.py, navigation.py, snapshot_actions.py, ref_actions.py, input_actions.py, content.py, __init__.py。
3. 将 browser profile resolver 拆成 browser_profile/ 包：
   constants.py, catalog.py, models.py, paths.py, checker.py, config.py, resolver.py, presenter.py, __init__.py。
4. 将 snapshot.py 中的大段 JS 移到 snapshot_script.js。
5. 清理旧常量名、PAGE_CLOSED、data-ref、ToolErrorCode enum 直接赋值等残留。

不要一次性改变协议字段。
不要把 action handler 改成复杂泛型。
不要引入 ActionContext。
不要引入 oneOf。
每完成一个阶段，保证 import 正常、测试通过。
```

```
```
