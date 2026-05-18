import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from common.logger import log_fail
from playwright.async_api import Page


@dataclass(frozen=True, slots=True)
class PageState:
    url: str
    title: str
    ready_state: Optional[str]
    is_closed: bool


@dataclass(frozen=True, slots=True)
class SessionState:
    browser_session_id: Optional[str]
    valid: bool
    created: bool = False
    reused: bool = False


@dataclass(frozen=True, slots=True)
class RecoveryHint:
    type: str
    scope: str = "tool_state"
    required_before_retry: bool = False
    reason: Optional[str] = None


@dataclass(frozen=True, slots=True)
class UserActionRequest:
    type: str
    message: str
    detected_reason: Optional[str] = None


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
class SnapshotPayload:
    snapshot_id: str
    tree: str
    refs_valid_for: str = "current_dom_only"
    mode: str = "full"
    goal: Optional[str] = None


@dataclass(frozen=True, slots=True)
class InterventionSignal:
    type: str
    confidence: float
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionResult:
    type: str
    status: str
    detail: Dict[str, Any]


class ErrorCategory(str, Enum):
    INVALID_REQUEST = "invalid_request"
    SESSION = "session"
    SNAPSHOT = "snapshot"
    ACTION = "action"
    USER_INTERVENTION = "user_intervention"
    BROWSER_UNAVAILABLE = "browser_unavailable"
    INTERNAL = "internal"


class ToolErrorCode(str, Enum):
    # 无效请求类错误
    NO_ACTION = "NO_ACTION"
    INVALID_ACTION_SCHEMA = "INVALID_ACTION_SCHEMA"
    UNKNOWN_ACTION = "UNKNOWN_ACTION"

    # 会话类错误
    SESSION_REQUIRED = "SESSION_REQUIRED"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_MISMATCH = "SESSION_MISMATCH"
    SESSION_EXPIRED = "SESSION_EXPIRED"

    # 快照类错误
    SNAPSHOT_REQUIRED = "SNAPSHOT_REQUIRED"
    STALE_REF = "STALE_REF"
    REF_NOT_FOUND = "REF_NOT_FOUND"

    # 动作类错误
    ACTION_FAILED = "ACTION_FAILED"

    # 用户介入类错误
    USER_INTERVENTION_REQUIRED = "USER_INTERVENTION_REQUIRED"

    # 浏览器不可用类错误
    BROWSER_UNAVAILABLE = "BROWSER_UNAVAILABLE"
    PROFILE_LOCKED = "PROFILE_LOCKED"
    PROFILE_UNAVAILABLE = "PROFILE_UNAVAILABLE"
    INVALID_BROWSER_CHANNEL = "INVALID_BROWSER_CHANNEL"

    # 内部错误
    INTERNAL_ERROR = "INTERNAL_ERROR"


def make_no_action_error() -> ToolError:
    return ToolError(
        category=ErrorCategory.INVALID_REQUEST.value,
        code=ToolErrorCode.NO_ACTION.value,
        message="No action provided. Must specify an action with a 'type' field.",
        retryable=False,
        recovery_hint=RecoveryHint(
            type="fix_request",
            required_before_retry=True,
            reason="Provide an action object with a valid type field.",
        ),
    )


def make_schema_error(
    message: str,
    *,
    context: Optional[Dict[str, Any]] = None,
) -> ToolError:
    return ToolError(
        category=ErrorCategory.INVALID_REQUEST.value,
        code=ToolErrorCode.INVALID_ACTION_SCHEMA.value,
        message=message,
        retryable=False,
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
        retryable=False,
        context={"action_type": action_type},
        recovery_hint=RecoveryHint(
            type="fix_request",
            required_before_retry=True,
            reason="Use one of the supported browse_interact action types.",
        ),
    )


def make_session_error(
    error_code: ToolErrorCode,
    current_session_id: Optional[str] = None,
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


def make_snapshot_required_error() -> ToolError:
    return ToolError(
        category=ErrorCategory.SNAPSHOT.value,
        code=ToolErrorCode.SNAPSHOT_REQUIRED.value,
        message="A current snapshot is required before using refs.",
        retryable=True,
        recovery_hint=RecoveryHint(
            type="refresh_snapshot",
            required_before_retry=True,
            reason="The previous action invalidated refs. Take a fresh snapshot before using click_ref/fill_ref/select_ref/check_ref.",
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
    snapshot_id: Optional[str] = None,
) -> ToolError:
    context: Dict[str, Any] = {"ref": ref}
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
            required_before_retry=True,
            reason="A fresh snapshot is required before retrying ref-based actions.",
        ),
    )


def make_action_failed_error(
    *,
    action_type: str,
    message: str,
    diagnostic_code: str,
    retryable: bool = False,
    context: Optional[Dict[str, Any]] = None,
    recovery_hint: Optional[RecoveryHint] = None,
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
        recovery_hint=recovery_hint
        or RecoveryHint(
            type="inspect_page_state",
            required_before_retry=False,
            reason=(
                "Inspect current page state before deciding whether to retry, "
                "refresh snapshot, or change strategy."
            ),
        ),
    )


def make_user_intervention_error_from_signal(signal: InterventionSignal) -> ToolError:
    if signal.type == "auth_page":
        user_action_type = "login_or_verification"
        user_action_message = (
            "请在打开的浏览器窗口中完成登录或验证，然后让 agent 继续。"
        )
        message = (
            "Authentication or verification is required in the visible browser window."
        )
    elif signal.type == "captcha":
        user_action_type = "captcha"
        user_action_message = "请在打开的浏览器窗口中完成验证码，然后让 agent 继续。"
        message = "CAPTCHA detected on the page. User intervention required."
    else:
        user_action_type = signal.type
        user_action_message = signal.reason
        message = signal.reason

    return ToolError(
        category=ErrorCategory.USER_INTERVENTION.value,
        code=ToolErrorCode.USER_INTERVENTION_REQUIRED.value,
        message=message,
        retryable=True,
        requires_user_action=True,
        user_action=UserActionRequest(
            type=user_action_type,
            message=user_action_message,
            detected_reason=signal.type,
        ),
        context={
            "detected_reason": signal.type,
        },
        recovery_hint=RecoveryHint(
            type="wait_for_user",
            required_before_retry=True,
            reason=(
                "User must complete the requested action in the visible browser "
                "window before the agent continues."
            ),
        ),
    )


def make_browser_unavailable_error(
    *,
    message: str,
    diagnostic_code: str,
    context: Optional[Dict[str, Any]] = None,
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


def make_profile_locked_error(
    *,
    message: str,
    diagnostic_code: str = "PROFILE_LOCKED",
    context: Optional[Dict[str, Any]] = None,
) -> ToolError:
    return ToolError(
        category=ErrorCategory.BROWSER_UNAVAILABLE.value,
        code=ToolErrorCode.PROFILE_LOCKED.value,
        message=message,
        retryable=True,
        requires_user_action=True,
        diagnostic_code=diagnostic_code,
        context=context or {},
        recovery_hint=RecoveryHint(
            type="close_browser_and_retry",
            required_before_retry=True,
            reason=(
                "The browser profile directory is locked by another browser process. "
                "Close all browser instances using this profile before retrying."
            ),
        ),
    )


def make_profile_unavailable_error(
    *,
    message: str,
    diagnostic_code: str = "PROFILE_UNAVAILABLE",
    context: Optional[Dict[str, Any]] = None,
) -> ToolError:
    return ToolError(
        category=ErrorCategory.BROWSER_UNAVAILABLE.value,
        code=ToolErrorCode.PROFILE_UNAVAILABLE.value,
        message=message,
        retryable=True,
        requires_user_action=True,
        diagnostic_code=diagnostic_code,
        context=context or {},
        recovery_hint=RecoveryHint(
            type="fix_profile_dir",
            required_before_retry=True,
            reason=(
                "The browser profile directory is not usable. "
                "Ensure the path exists, is a directory, and is readable/writable."
            ),
        ),
    )


def make_invalid_browser_channel_error(
    *,
    message: str,
    diagnostic_code: str = "INVALID_BROWSER_CHANNEL",
    context: Optional[Dict[str, Any]] = None,
) -> ToolError:
    return ToolError(
        category=ErrorCategory.BROWSER_UNAVAILABLE.value,
        code=ToolErrorCode.INVALID_BROWSER_CHANNEL.value,
        message=message,
        retryable=True,
        requires_user_action=True,
        diagnostic_code=diagnostic_code,
        context=context or {},
        recovery_hint=RecoveryHint(
            type="install_browser_or_change_channel",
            required_before_retry=True,
            reason=(
                "The specified browser channel is not available. "
                "Install the browser or switch to an available channel."
            ),
        ),
    )


def make_internal_error(
    *,
    message: str,
    diagnostic_code: str = "INTERNAL_ERROR",
    context: Optional[Dict[str, Any]] = None,
) -> ToolError:
    return ToolError(
        category=ErrorCategory.INTERNAL.value,
        code=ToolErrorCode.INTERNAL_ERROR.value,
        message=message,
        retryable=False,
        diagnostic_code=diagnostic_code,
        context=context or {},
        recovery_hint=RecoveryHint(
            type="inspect_status",
            required_before_retry=False,
            reason="Inspect current tool state before deciding whether to retry.",
        ),
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
        "error": error_to_dict(error),
    }

    return json.dumps(payload, ensure_ascii=False)


def error_to_dict(error: ToolError) -> Dict[str, Any]:
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


async def get_page_state(page: Optional[Page]) -> Optional[PageState]:
    if page is None:
        return None

    try:
        if page.is_closed():
            return PageState(
                url="",
                title="",
                ready_state=None,
                is_closed=True,
            )

        url = page.url
        title = await page.title()
        ready_state = await page.evaluate("() => document.readyState")

        return PageState(
            url=url,
            title=title,
            ready_state=ready_state,
            is_closed=False,
        )

    except Exception:
        log_fail("获取页面状态", "")
        return PageState(
            url=getattr(page, "url", ""),
            title="",
            ready_state=None,
            is_closed=page.is_closed() if page else True,
        )


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
