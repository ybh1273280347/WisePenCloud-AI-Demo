````md
# browse_interact 第二轮协议重构执行文档

## 背景

上一轮已经完成错误协议重构：

```text
recommended_next_action
  -> error.recovery_hint
````

错误响应已经从顶层 `error_code / error_message / recommended_next_action` 改为嵌套结构：

```json
{
  "success": false,
  "browser_session_id": "...",
  "session": {...},
  "page": {...},
  "error": {
    "category": "...",
    "code": "...",
    "message": "...",
    "retryable": true,
    "requires_user_action": false,
    "context": {...},
    "diagnostic_code": "...",
    "recovery_hint": {
      "type": "...",
      "scope": "tool_state",
      "required_before_retry": true,
      "reason": "..."
    }
  }
}
```

本轮继续处理两个问题：

```text
1. snapshot 目前只能返回全量交互树，页面复杂时 token 成本高，模型注意力容易被稀释。
2. UserInterventionDetector 当前直接返回 ToolError，语义上像一个可靠检测器，但实际只能做 best-effort 信号检测。
```

本轮重构目标：

```text
1. snapshot 支持 mode / goal / limit。
2. 保留 full snapshot 作为稳定 fallback。
3. 新增 focused / viewport / compact snapshot。
4. snapshot JS 返回结构化 elements metadata。
5. Python 层负责筛选、排序、格式化 tree。
6. UserInterventionDetector 改为返回 InterventionSignal。
7. errors.py 根据 InterventionSignal 构造 USER_INTERVENTION_REQUIRED。
8. 明确 intervention detection 是 best-effort，不承诺可靠检测所有登录、验证码、风控。
```

---

# 一、协议设计

## 1. snapshot action 扩展

`snapshot` action 支持可选字段：

```json
{
  "action": {
    "type": "snapshot",
    "mode": "focused",
    "goal": "find search box",
    "limit": 30
  }
}
```

字段语义：

```text
mode:
  full      返回完整交互树，默认模式。
  focused   根据 goal 对元素排序并截断。
  viewport  只返回当前视口内元素。
  compact   返回高信号元素子集。

goal:
  可选，只用于 focused 模式的候选排序。
  例如 "find search box"、"login button"、"submit form"。

limit:
  可选，只对 focused / viewport / compact 生效。
  用于限制返回元素数量。
```

## 2. snapshot response 扩展

`SnapshotPayload` 新增 metadata：

```json
{
  "snapshot": {
    "snapshot_id": "abc123",
    "tree": "[e1] textbox \"Search\" [fillable]",
    "refs_valid_for": "current_dom_only",
    "mode": "focused",
    "goal": "find search box",
    "returned_count": 8,
    "total_count": 132,
    "omitted_count": 124
  }
}
```

重要语义：

```text
1. mode 只影响返回给 agent 的 tree。
2. ref 仍然由 snapshot JS 写入 DOM。
3. click_ref / fill_ref 仍然通过 data-agent-ref 定位元素。
4. full 模式保持原来的完整 snapshot 行为。
5. focused / viewport / compact 是降低 token 的优化模式，不替代 full。
```

---

# 二、修改 `models.py`

## 目标

新增：

```text
InterventionSignal
SnapshotPayload metadata
```

保留上一轮错误协议中的：

```text
RecoveryHint
ToolError.context
ToolError.diagnostic_code
```

## 示例代码

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
    mode: str = "full"
    goal: Optional[str] = None
    returned_count: Optional[int] = None
    total_count: Optional[int] = None
    omitted_count: Optional[int] = None


@dataclass(frozen=True)
class InterventionSignal:
    type: str
    confidence: float
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionResult:
    type: str
    status: str
    detail: Dict[str, Any]
```

---

# 三、修改 `constants.py`

## 目标

增加 snapshot mode / limit 相关常量。

## 示例代码

```python
# Interaction behavior
SCROLL_STEP_PX = 100
FILL_FOCUS_WAIT_MS = 100
SETTLE_WAIT_MS = 800
WAIT_DURATION_MAX_S = 30

# Media
SCREENSHOT_JPEG_QUALITY = 40  # JPEG quality, 1-100

# Timeouts
NAVIGATION_TIMEOUT_MS = 60000

# IDs
SESSION_ID_LENGTH = 12
SNAPSHOT_ID_LENGTH = 8

# Snapshot
SNAPSHOT_DEFAULT_MODE = "full"
SNAPSHOT_DEFAULT_LIMIT = 80
SNAPSHOT_FOCUSED_DEFAULT_LIMIT = 30
SNAPSHOT_MAX_LIMIT = 200

# User intervention detection
AUTH_PAGE_INDICATORS = ("login.", "accounts.", "signin.", "auth.")
```

---

# 四、重构 `snapshot.py`

## 目标

当前 snapshot JS 直接返回字符串 tree。重构后：

```text
1. JS 返回 elements metadata。
2. Python 层根据 mode / goal / limit 选择元素。
3. Python 层格式化 tree。
4. full 模式不排序、不截断。
5. focused 模式根据 goal 简单规则打分。
6. viewport 模式优先返回当前视口元素。
7. compact 模式返回高信号元素子集。
```

## `SnapshotManager.take` 目标接口

```python
async def take(
    self,
    page: Page,
    *,
    mode: str = SNAPSHOT_DEFAULT_MODE,
    goal: str | None = None,
    limit: int | None = None,
) -> SnapshotPayload:
    ...
```

## 示例代码骨架

```python
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional

from playwright.async_api import Page

from .constants import (
    SNAPSHOT_DEFAULT_LIMIT,
    SNAPSHOT_DEFAULT_MODE,
    SNAPSHOT_FOCUSED_DEFAULT_LIMIT,
    SNAPSHOT_ID_LENGTH,
    SNAPSHOT_MAX_LIMIT,
)
from .errors import make_snapshot_required_error, make_stale_ref_error
from .models import SnapshotPayload, ToolError


_REF_PATTERN = re.compile(r"^e[1-9][0-9]*$")
_REF_ATTRIBUTE = "data-agent-ref"
_ALLOWED_SNAPSHOT_MODES = {"full", "focused", "viewport", "compact"}


def is_valid_ref(ref: str) -> bool:
    return bool(_REF_PATTERN.fullmatch(ref))


def ref_selector(ref: str) -> str:
    if not is_valid_ref(ref):
        raise ValueError(f"invalid ref: {ref}")

    return f'[{_REF_ATTRIBUTE}="{ref}"]'


class SnapshotManager:
    def __init__(self) -> None:
        self._current_snapshot_id: Optional[str] = None

    @property
    def current_snapshot_id(self) -> Optional[str]:
        return self._current_snapshot_id

    def invalidate(self) -> None:
        self._current_snapshot_id = None

    def generate_id(self) -> str:
        snapshot_id = uuid.uuid4().hex[:SNAPSHOT_ID_LENGTH]
        self._current_snapshot_id = snapshot_id
        return snapshot_id

    def require_current(self, snapshot_id: Optional[str]) -> Optional[ToolError]:
        if self._current_snapshot_id is None:
            return make_snapshot_required_error()

        if not snapshot_id:
            return make_snapshot_required_error()

        if snapshot_id != self._current_snapshot_id:
            return make_stale_ref_error(snapshot_id)

        return None

    async def take(
        self,
        page: Page,
        *,
        mode: str = SNAPSHOT_DEFAULT_MODE,
        goal: str | None = None,
        limit: int | None = None,
    ) -> SnapshotPayload:
        normalized_mode = normalize_snapshot_mode(mode)
        normalized_limit = normalize_snapshot_limit(
            limit,
            mode=normalized_mode,
        )

        raw = await page.evaluate(_SNAPSHOT_JS)

        if not isinstance(raw, str):
            raise ValueError("Snapshot script returned non-string result")

        data = json.loads(raw)
        elements = data.get("elements")

        if not isinstance(elements, list):
            elements = []

        selected = select_snapshot_elements(
            elements,
            mode=normalized_mode,
            goal=goal,
            limit=normalized_limit,
        )

        tree = "\n".join(_format_snapshot_line(element) for element in selected)
        if not tree:
            tree = "(empty)"

        snapshot_id = self.generate_id()
        total_count = len(elements)
        returned_count = len(selected)

        return SnapshotPayload(
            snapshot_id=snapshot_id,
            tree=tree,
            mode=normalized_mode,
            goal=goal if normalized_mode == "focused" else None,
            returned_count=returned_count,
            total_count=total_count,
            omitted_count=max(0, total_count - returned_count),
        )
```

## mode / limit 规范化

```python
def normalize_snapshot_mode(mode: str | None) -> str:
    if not mode:
        return SNAPSHOT_DEFAULT_MODE

    normalized = mode.strip().lower()
    if normalized not in _ALLOWED_SNAPSHOT_MODES:
        return SNAPSHOT_DEFAULT_MODE

    return normalized


def normalize_snapshot_limit(
    limit: object,
    *,
    mode: str,
) -> int | None:
    if mode == "full":
        return None

    if mode == "focused":
        default = SNAPSHOT_FOCUSED_DEFAULT_LIMIT
    else:
        default = SNAPSHOT_DEFAULT_LIMIT

    if limit is None:
        return default

    if not isinstance(limit, int):
        return default

    return max(1, min(limit, SNAPSHOT_MAX_LIMIT))
```

## 元素选择逻辑

```python
def select_snapshot_elements(
    elements: list[dict[str, Any]],
    *,
    mode: str,
    goal: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    if mode == "full":
        return elements

    safe_limit = limit or SNAPSHOT_DEFAULT_LIMIT

    if mode == "viewport":
        selected = [
            element for element in elements
            if element.get("inViewport")
        ]
        if not selected:
            selected = elements
        return selected[:safe_limit]

    if mode == "compact":
        scored = [
            (_base_element_score(element), index, element)
            for index, element in enumerate(elements)
        ]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [element for _, _, element in scored[:safe_limit]]

    if mode == "focused":
        scored = [
            (_score_for_goal(element, goal), index, element)
            for index, element in enumerate(elements)
        ]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [element for _, _, element in scored[:safe_limit]]

    return elements
```

## 打分逻辑

```python
def _base_element_score(element: dict[str, Any]) -> float:
    score = 0.0

    if element.get("inViewport"):
        score += 3.0

    if element.get("fillable"):
        score += 3.0

    if element.get("clickable"):
        score += 2.0

    if element.get("label"):
        score += 1.0

    role = element.get("role")
    if role in {"textbox", "searchbox", "combobox", "button", "link"}:
        score += 1.0

    return score


def _score_for_goal(
    element: dict[str, Any],
    goal: str | None,
) -> float:
    score = _base_element_score(element)

    if not goal:
        return score

    goal_tokens = _tokenize_goal(goal)
    text = _element_search_text(element)

    for token in goal_tokens:
        if token in text:
            score += 5.0

    if _has_any(goal_tokens, {"search", "find", "query", "搜索"}):
        if element.get("role") in {"textbox", "searchbox", "combobox"}:
            score += 6.0
        if "search" in text or "搜索" in text:
            score += 6.0

    if _has_any(goal_tokens, {"login", "signin", "sign", "登录", "登陆"}):
        if element.get("role") == "textbox":
            score += 3.0
        if "password" in text or "密码" in text:
            score += 5.0
        if element.get("role") == "button":
            score += 2.0

    if _has_any(goal_tokens, {"submit", "confirm", "continue", "提交", "确认", "继续"}):
        if element.get("role") == "button":
            score += 6.0

    return score


def _tokenize_goal(goal: str) -> set[str]:
    lowered = goal.lower()
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", lowered)
    return {token for token in tokens if token}


def _has_any(tokens: set[str], values: set[str]) -> bool:
    return bool(tokens & values)


def _element_search_text(element: dict[str, Any]) -> str:
    parts = [
        str(element.get("label", "")),
        str(element.get("role", "")),
        str(element.get("tag", "")),
        str(element.get("type", "")),
    ]
    return " ".join(parts).lower()
```

## tree 格式化

```python
def _format_snapshot_line(element: dict[str, Any]) -> str:
    ref = str(element.get("ref") or "")
    role = str(element.get("role") or "")
    label = str(element.get("label") or "")
    flags = element.get("flags") or []

    line = f"[{ref}] {role}"

    if label:
        line += f" {json.dumps(label, ensure_ascii=False)}"

    if flags:
        line += " [" + ",".join(str(flag) for flag in flags) + "]"

    return line
```

---

# 五、修改 snapshot JS

## 目标

JS 不再直接返回 `tree`，而是返回 `elements`。

每个 element 至少包含：

```text
ref
role
label
flags
tag
type
fillable
clickable
inViewport
x
y
width
height
```

## 示例 `_SNAPSHOT_JS`

```python
_SNAPSHOT_JS = r"""() => {
    const elements = [];
    let idx = 0;

    const REF_ATTR = 'data-agent-ref';

    const skip = new Set([
        'SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG', 'HEAD', 'META', 'LINK',
        'PATH', 'CIRCLE', 'RECT', 'POLYGON', 'USE', 'DEFS', 'G', 'BR', 'HR'
    ]);

    document
        .querySelectorAll('[' + REF_ATTR + ']')
        .forEach(el => el.removeAttribute(REF_ATTR));

    function isVisible(el) {
        const style = window.getComputedStyle(el);

        if (style.display === 'none') return false;
        if (style.visibility === 'hidden') return false;
        if (style.opacity === '0') return false;

        const rect = el.getBoundingClientRect();

        if (rect.width === 0 && rect.height === 0) return false;

        return true;
    }

    function isDisabled(el) {
        return Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true';
    }

    function getRectInfo(el) {
        const rect = el.getBoundingClientRect();

        const inViewport =
            rect.bottom >= 0 &&
            rect.right >= 0 &&
            rect.top <= window.innerHeight &&
            rect.left <= window.innerWidth;

        return {
            x: rect.x,
            y: rect.y,
            width: rect.width,
            height: rect.height,
            inViewport
        };
    }

    function isClickable(el) {
        if (isDisabled(el)) return false;

        const style = window.getComputedStyle(el);
        if (style.pointerEvents === 'none') return false;

        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return false;

        const cx = rect.left + rect.width / 2;
        const cy = rect.top + rect.height / 2;

        if (
            cx < 0 ||
            cy < 0 ||
            cx > window.innerWidth ||
            cy > window.innerHeight
        ) {
            return false;
        }

        const topEl = document.elementFromPoint(cx, cy);

        if (
            topEl &&
            topEl !== el &&
            !el.contains(topEl) &&
            !topEl.contains(el)
        ) {
            return false;
        }

        return true;
    }

    function isFillable(el) {
        if (isDisabled(el)) return false;

        const tag = el.tagName.toLowerCase();
        const type = (el.getAttribute('type') || '').toLowerCase();

        if (tag === 'textarea') return true;

        if (tag === 'input') {
            return ![
                'hidden',
                'submit',
                'button',
                'checkbox',
                'radio',
                'image',
                'file',
                'reset',
                'color',
                'range'
            ].includes(type);
        }

        if (el.isContentEditable) return true;

        return false;
    }

    function getRole(el) {
        const explicitRole = el.getAttribute('role') || '';
        if (explicitRole) return explicitRole;

        const tag = el.tagName.toLowerCase();
        const type = (el.getAttribute('type') || '').toLowerCase();

        if (el.isContentEditable) return 'textbox';

        if (tag === 'input') {
            if (type === 'hidden') return '';

            if (
                type === 'submit' ||
                type === 'button' ||
                type === 'reset' ||
                type === 'image'
            ) {
                return 'button';
            }

            if (type === 'checkbox') return 'checkbox';
            if (type === 'radio') return 'radio';
            if (type === 'search') return 'searchbox';

            return 'textbox';
        }

        if (tag === 'textarea') return 'textbox';
        if (tag === 'select') return 'combobox';
        if (tag === 'button') return 'button';
        if (tag === 'a' && el.href) return 'link';
        if (tag === 'iframe') return 'iframe';
        if (tag === 'img') return 'img';
        if (tag === 'video') return 'video';

        return '';
    }

    function getLabel(el) {
        const direct =
            el.getAttribute('aria-label') ||
            el.getAttribute('placeholder') ||
            el.getAttribute('title') ||
            el.getAttribute('alt') ||
            el.getAttribute('name') ||
            '';

        if (direct) {
            return direct.trim().slice(0, 80);
        }

        const labelledBy = el.getAttribute('aria-labelledby');
        if (labelledBy) {
            const parts = labelledBy
                .split(/\s+/)
                .map(id => document.getElementById(id))
                .filter(Boolean)
                .map(labelEl => (labelEl.innerText || labelEl.textContent || '').trim())
                .filter(Boolean);

            if (parts.length > 0) {
                return parts.join(' ').slice(0, 80);
            }
        }

        if (el.id) {
            const labelEl = document.querySelector(
                'label[for="' + CSS.escape(el.id) + '"]'
            );

            if (labelEl) {
                const text = (
                    labelEl.innerText ||
                    labelEl.textContent ||
                    ''
                ).trim().slice(0, 80);

                if (text) return text;
            }
        }

        const parentLabel = el.closest('label');
        if (parentLabel) {
            const text = (
                parentLabel.innerText ||
                parentLabel.textContent ||
                ''
            ).trim().slice(0, 80);

            if (text) return text;
        }

        const value = el.getAttribute('value');
        if (value) {
            return value.trim().slice(0, 80);
        }

        const selfText = (
            el.innerText ||
            el.textContent ||
            ''
        ).trim().slice(0, 80);

        return selfText;
    }

    function shouldExpose(el, role, visible, clickable, fillable) {
        if (!role || !visible) return false;

        if (role === 'iframe') return true;
        if (fillable) return true;
        if (clickable) return true;

        return false;
    }

    function buildFlags(el, role, clickable, fillable) {
        const flags = [];

        if (fillable) {
            flags.push('fillable');
        }

        if (!clickable && !fillable && role !== 'iframe') {
            flags.push('not-clickable');
        }

        if (role === 'iframe') {
            flags.push('frame');
        }

        return flags;
    }

    function exposeElement(el, role, clickable, fillable) {
        idx += 1;

        const ref = 'e' + idx;
        el.setAttribute(REF_ATTR, ref);

        const label = getLabel(el);
        const flags = buildFlags(el, role, clickable, fillable);
        const rect = getRectInfo(el);

        elements.push({
            ref,
            role,
            label,
            flags,
            tag: el.tagName.toLowerCase(),
            type: (el.getAttribute('type') || '').toLowerCase(),
            fillable,
            clickable,
            inViewport: rect.inViewport,
            x: rect.x,
            y: rect.y,
            width: rect.width,
            height: rect.height
        });
    }

    function walk(root, depth) {
        const children = root.shadowRoot ? root.shadowRoot.children : root.children;

        if (!children) return;

        for (const el of children) {
            if (!el || !el.tagName || skip.has(el.tagName)) continue;

            const tag = el.tagName.toLowerCase();
            const type = (el.getAttribute('type') || '').toLowerCase();

            if (tag === 'input' && type === 'hidden') continue;

            const role = getRole(el);
            const visible = isVisible(el);
            const clickable = visible ? isClickable(el) : false;
            const fillable = visible ? isFillable(el) : false;

            if (shouldExpose(el, role, visible, clickable, fillable)) {
                exposeElement(el, role, clickable, fillable);

                if (role !== 'iframe' && depth < 12) {
                    walk(el, depth + 1);
                }
            } else if (visible && depth < 12) {
                walk(el, depth + 1);
            }
        }
    }

    if (document.body) {
        walk(document.body, 0);
    }

    return JSON.stringify({
        elements
    });
}"""
```

---

# 六、修改 `actions.py` 的 `handle_snapshot`

## 目标

从 action 中读取 `mode / goal / limit`，并传给 `SnapshotManager.take()`。

## 示例代码

```python
async def handle_snapshot(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    processor: ContentProcessor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    page, session_error_response = await _get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    mode = action.get("mode", "full")
    goal = action.get("goal")
    limit = action.get("limit")

    if mode is not None and not isinstance(mode, str):
        return await _action_error_response(
            session_manager,
            page,
            make_schema_error("snapshot 'mode' must be a string."),
        )

    if goal is not None and not isinstance(goal, str):
        return await _action_error_response(
            session_manager,
            page,
            make_schema_error("snapshot 'goal' must be a string."),
        )

    if limit is not None and not isinstance(limit, int):
        return await _action_error_response(
            session_manager,
            page,
            make_schema_error("snapshot 'limit' must be an integer."),
        )

    try:
        snapshot_payload = await snapshot_manager.take(
            page,
            mode=mode,
            goal=goal,
            limit=limit,
        )
    except Exception as error:
        log_fail("浏览器快照", str(error))
        page_state = await get_page_state(page)
        return build_error_response(
            session_state=_session_state(session_manager),
            page_state=page_state,
            error=make_internal_error(
                message="Snapshot failed.",
                diagnostic_code="SNAPSHOT_FAILED",
            ),
        )

    page_state = await get_page_state(page)
    return build_success_response(
        session_state=_session_state(session_manager, reused=True),
        page_state=page_state,
        action_result=ActionResult(
            type="snapshot",
            status="completed",
            detail={
                "snapshot_id": snapshot_payload.snapshot_id,
                "mode": snapshot_payload.mode,
                "goal": snapshot_payload.goal,
                "returned_count": snapshot_payload.returned_count,
                "total_count": snapshot_payload.total_count,
                "omitted_count": snapshot_payload.omitted_count,
            },
        ),
        snapshot=snapshot_payload,
    )
```

---

# 七、修改 `tool.py` schema

## 目标

在 action properties 中增加：

```text
mode
goal
limit
```

## 示例 schema 片段

```python
"mode": {
    "type": "string",
    "enum": ["full", "focused", "viewport", "compact"],
    "description": (
        "Snapshot mode. "
        "full returns the complete interactive tree. "
        "focused ranks and limits candidates using goal. "
        "viewport returns elements currently visible in the viewport. "
        "compact returns a compact high-signal subset."
    ),
},
"goal": {
    "type": "string",
    "description": (
        "Optional goal for focused snapshot, e.g. 'find search box'. "
        "Used only as a ranking hint; it is not a task-level instruction."
    ),
},
"limit": {
    "type": "integer",
    "description": (
        "Maximum number of elements to return for focused, viewport, or compact snapshot."
    ),
},
```

更新 action description：

```python
"description": (
    "A single action to perform. Each action type has specific required fields:\n"
    "- navigate: requires url\n"
    "- click_ref: requires snapshot_id and ref\n"
    "- fill_ref: requires snapshot_id, ref, and text\n"
    "- key: requires text\n"
    "- wait: duration is optional and must be a number\n"
    "- scroll: scroll_direction and scroll_amount are optional\n"
    "- snapshot: type only; optional mode, goal, limit\n"
    "- status, screenshot, get_content, go_back, go_forward: only type is needed"
),
```

更新 `_TOOL_DESCRIPTION`，强调 focused snapshot 是筛选辅助，不是任务规划：

```python
_TOOL_DESCRIPTION = (
    "Agent-safe browser interaction tool based on snapshot+ref. "
    "Operates a local persistent browser with dedicated automation profile. "
    "Each call performs ONE action. "
    "Flow: 1) navigate to a page, 2) snapshot to get refs with snapshot_id, "
    "3) use click_ref/fill_ref with snapshot_id and ref to interact. "
    "Snapshot supports optional mode/goal/limit. "
    "Use full snapshot for complete page state, or focused snapshot to reduce "
    "token usage when searching for a specific kind of element. "
    "Always pass browser_session_id from the previous response when available. "
    "On errors, inspect error.recovery_hint. It describes tool-state recovery "
    "requirements, not a task-level plan. "
    "User intervention detection is best-effort and may not catch all login, "
    "verification, CAPTCHA, or anti-bot challenges."
)
```

---

# 八、重构 `intervention.py`

## 目标

`UserInterventionDetector` 只返回 `InterventionSignal`，不直接构造 `ToolError`。

## 示例代码

```python
from __future__ import annotations

from typing import Optional

from playwright.async_api import Page

from .constants import AUTH_PAGE_INDICATORS
from .models import InterventionSignal


class UserInterventionDetector:
    """
    Best-effort detector for user intervention signals.

    This detector does not guarantee reliable detection of all login,
    CAPTCHA, verification, or anti-bot challenges. It only reports cheap,
    observable signals from URL/title/DOM evidence.
    """

    async def detect(self, page: Page) -> Optional[InterventionSignal]:
        try:
            url = page.url.lower()
            title = (await page.title()).lower()

            auth_signal = self._detect_auth_page(url, title)
            if auth_signal is not None:
                return auth_signal

            captcha_signal = await self._detect_captcha(page)
            if captcha_signal is not None:
                return captcha_signal

        except Exception:
            return None

        return None

    def _detect_auth_page(
        self,
        url: str,
        title: str,
    ) -> Optional[InterventionSignal]:
        matched = [
            indicator
            for indicator in AUTH_PAGE_INDICATORS
            if indicator in url or indicator in title
        ]

        if not matched:
            return None

        return InterventionSignal(
            type="auth",
            confidence=0.65,
            reason="auth indicator found in URL or title",
            evidence={
                "matched_indicators": matched,
                "url": url,
                "title": title,
            },
        )

    async def _detect_captcha(self, page: Page) -> Optional[InterventionSignal]:
        result = await page.evaluate(_CAPTCHA_DETECTION_JS)

        if not isinstance(result, dict) or not result.get("detected"):
            return None

        matched_selectors = result.get("matchedSelectors") or []
        matched_text = result.get("matchedText") or []

        confidence = 0.75 if matched_selectors else 0.55

        return InterventionSignal(
            type="captcha",
            confidence=confidence,
            reason="captcha indicators found in DOM",
            evidence={
                "matched_selectors": matched_selectors,
                "matched_text": matched_text,
            },
        )


_CAPTCHA_DETECTION_JS = """() => {
    const matchedSelectors = [];
    const selectorIndicators = [
        '[class*="captcha"]',
        '[id*="captcha"]',
        '[class*="recaptcha"]',
        '[id*="recaptcha"]',
        '[class*="hcaptcha"]',
        '[id*="hcaptcha"]',
        'iframe[src*="captcha"]',
        'iframe[src*="recaptcha"]',
        'iframe[src*="hcaptcha"]'
    ];

    for (const selector of selectorIndicators) {
        if (document.querySelector(selector)) {
            matchedSelectors.push(selector);
        }
    }

    const bodyText = (document.body && document.body.innerText || '').toLowerCase();

    const textIndicators = [
        'captcha',
        'recaptcha',
        'hcaptcha',
        'verify you are human',
        'human verification',
        'security check',
        'are you a human'
    ];

    const matchedText = textIndicators.filter(text => bodyText.includes(text));

    return {
        detected: matchedSelectors.length > 0 || matchedText.length > 0,
        matchedSelectors,
        matchedText
    };
}"""
```

---

# 九、修改 `errors.py`

## 目标

新增：

```python
make_user_intervention_error_from_signal(signal: InterventionSignal) -> ToolError
```

继续统一输出：

```text
USER_INTERVENTION_REQUIRED
```

具体信号放入：

```text
error.context.signal_type
error.context.confidence
error.context.evidence
user_action.detected_reason
```

## 示例代码

```python
from .models import InterventionSignal, RecoveryHint, ToolError, UserActionRequest


def make_user_intervention_error_from_signal(
    signal: InterventionSignal,
) -> ToolError:
    user_action_type = _user_action_type_from_signal(signal.type)
    user_action_message = _user_action_message_from_signal(signal.type)

    return ToolError(
        category=ErrorCategory.USER_INTERVENTION.value,
        code=ToolErrorCode.USER_INTERVENTION_REQUIRED.value,
        message=_message_from_intervention_signal(signal),
        retryable=True,
        requires_user_action=True,
        user_action=UserActionRequest(
            type=user_action_type,
            message=user_action_message,
            detected_reason=signal.type,
        ),
        context={
            "signal_type": signal.type,
            "confidence": signal.confidence,
            "reason": signal.reason,
            "evidence": signal.evidence,
        },
        recovery_hint=RecoveryHint(
            type="wait_for_user",
            required_before_retry=True,
            reason=(
                "User may need to complete an action in the visible browser "
                "window before the agent continues."
            ),
        ),
    )


def _user_action_type_from_signal(signal_type: str) -> str:
    if signal_type == "captcha":
        return "captcha"

    if signal_type == "auth":
        return "login_or_verification"

    return "review_browser_window"


def _user_action_message_from_signal(signal_type: str) -> str:
    if signal_type == "captcha":
        return "请在打开的浏览器窗口中完成验证码，然后让 agent 继续。"

    if signal_type == "auth":
        return "请在打开的浏览器窗口中完成登录或验证，然后让 agent 继续。"

    return "请检查打开的浏览器窗口，并在必要时完成页面要求的操作。"


def _message_from_intervention_signal(signal: InterventionSignal) -> str:
    if signal.type == "captcha":
        return "Possible CAPTCHA or human verification challenge detected."

    if signal.type == "auth":
        return "Possible authentication or verification page detected."

    return "Possible user intervention is required."
```

---

# 十、修改 `actions.py` 中 intervention 调用

## 目标

把：

```python
intervention_error = await intervention.detect(page)
if intervention_error:
    ...
    error=intervention_error
```

改成：

```python
intervention_signal = await intervention.detect(page)
if intervention_signal:
    ...
    error=make_user_intervention_error_from_signal(intervention_signal)
```

## 示例代码

```python
from .errors import make_user_intervention_error_from_signal
```

在以下位置替换：

```text
handle_navigate
_handle_navigation_direction
handle_click_ref
handle_wait
```

示例：

```python
intervention_signal = await intervention.detect(page)
if intervention_signal:
    page_state = await get_page_state(page)
    return build_error_response(
        session_state=_session_state(session_manager),
        page_state=page_state,
        error=make_user_intervention_error_from_signal(intervention_signal),
    )
```

---

# 十一、不要改变的既有语义

本轮重构必须保持：

```text
1. fill_ref 成功后不 invalidate snapshot。
2. fill_ref 成功响应只返回 text_length，不返回 text。
3. click_ref 成功后 invalidate snapshot。
4. navigate / go_back / go_forward 成功后 invalidate snapshot。
5. scroll / key 成功后 invalidate snapshot。
6. wait / screenshot / get_content / status 不主动 invalidate snapshot。
7. snapshot_id / ref 生命周期不变。
8. error.recovery_hint 不变回 recommended_next_action。
9. User intervention 仍然通过 USER_INTERVENTION_REQUIRED 表达。
10. detector 只报告 best-effort signal，不承诺可靠覆盖。
```

---

# 十二、给 AI 执行时的关键约束

执行本轮重构时，必须遵守：

```text
1. 不要删除 full snapshot。
2. 不要让 focused snapshot 替代 full snapshot。
3. 不要引入 embedding、LLM rerank、视觉模型。
4. 不要把 goal 当成任务指令，只能当作排序 hint。
5. 不要把 UserInterventionDetector 设计成可靠判定器。
6. 不要让 detector 直接返回 ToolError。
7. 不要把 intervention evidence 丢掉。
8. 不要把 Playwright 原始异常直接返回给 agent。
9. 不要修改 browser_profile。
10. 不要修改 session.py 生命周期语义。
11. 不要重构 actions.py 文件结构。
12. 不要恢复 recommended_next_action。
```

---

# 十三、最终期望形态

## focused snapshot 输入

```json
{
  "action": {
    "type": "snapshot",
    "mode": "focused",
    "goal": "find search box",
    "limit": 10
  }
}
```

## focused snapshot 输出

```json
{
  "success": true,
  "browser_session_id": "...",
  "session": {...},
  "page": {...},
  "action_result": {
    "type": "snapshot",
    "status": "completed",
    "detail": {
      "snapshot_id": "abc123",
      "mode": "focused",
      "goal": "find search box",
      "returned_count": 10,
      "total_count": 132,
      "omitted_count": 122
    }
  },
  "snapshot": {
    "snapshot_id": "abc123",
    "tree": "[e5] searchbox \"Search\" [fillable]\n[e6] button \"Search\"",
    "refs_valid_for": "current_dom_only",
    "mode": "focused",
    "goal": "find search box",
    "returned_count": 10,
    "total_count": 132,
    "omitted_count": 122
  }
}
```

## intervention signal 对应错误输出

```json
{
  "success": false,
  "browser_session_id": "...",
  "session": {...},
  "page": {...},
  "error": {
    "category": "user_intervention",
    "code": "USER_INTERVENTION_REQUIRED",
    "message": "Possible CAPTCHA or human verification challenge detected.",
    "retryable": true,
    "requires_user_action": true,
    "user_action": {
      "type": "captcha",
      "message": "请在打开的浏览器窗口中完成验证码，然后让 agent 继续。",
      "detected_reason": "captcha"
    },
    "context": {
      "signal_type": "captcha",
      "confidence": 0.75,
      "reason": "captcha indicators found in DOM",
      "evidence": {
        "matched_selectors": ["iframe[src*=\"recaptcha\"]"],
        "matched_text": ["verify you are human"]
      }
    },
    "recovery_hint": {
      "type": "wait_for_user",
      "scope": "tool_state",
      "required_before_retry": true,
      "reason": "User may need to complete an action in the visible browser window before the agent continues."
    }
  }
}
```

```
```
