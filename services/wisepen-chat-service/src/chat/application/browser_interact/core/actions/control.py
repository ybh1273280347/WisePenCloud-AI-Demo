from typing import Dict, List, Optional

from chat.application.web_fetch.content_processor import ContentProcessor
from common.logger import log_fail

from ..protocol import (
    ActionResult,
    build_error_response,
    build_success_response,
    get_page_state,
    make_action_failed_error,
    make_schema_error,
    make_user_intervention_error_from_signal,
)
from ..intervention import UserInterventionDetector
from ..session import BrowserSessionManager
from ..snapshot import SnapshotManager
from ..action_runtime import (
    action_error_response,
    get_existing_page_or_error,
    session_state,
)

_SCROLL_STEP_PX = 100
_SETTLE_WAIT_MS = 800
_WAIT_DURATION_MAX_S = 30
WAIT_DURATION_MAX_S = _WAIT_DURATION_MAX_S

_KEY_ALIASES = {
    "ctrl": "Control",
    "control": "Control",
    "alt": "Alt",
    "shift": "Shift",
    "meta": "Meta",
    "cmd": "Meta",
    "command": "Meta",
    "enter": "Enter",
    "return": "Enter",
    "esc": "Escape",
    "escape": "Escape",
    "tab": "Tab",
    "space": " ",
    "backspace": "Backspace",
    "delete": "Delete",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "home": "Home",
    "end": "End",
}


def _split_keys(keys_str: str) -> List[str]:
    return [k.strip() for k in keys_str.split("+") if k.strip()]


def _normalize_keys(keys: List[str]) -> List[str]:
    return [_KEY_ALIASES.get(k.lower(), k) for k in keys]


async def handle_scroll(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    processor: ContentProcessor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    direction = action.get("scroll_direction", "down")
    amount = action.get("scroll_amount", 1)

    amount = max(0, amount)

    scroll_offsets = {
        "down": (0, _SCROLL_STEP_PX * amount),
        "up": (0, -_SCROLL_STEP_PX * amount),
        "right": (_SCROLL_STEP_PX * amount, 0),
        "left": (-_SCROLL_STEP_PX * amount, 0),
    }
    delta_x, delta_y = scroll_offsets[direction]

    try:
        await page.mouse.wheel(delta_x, delta_y)
        await page.wait_for_timeout(_SETTLE_WAIT_MS)
    except Exception as error:
        log_fail("浏览器滚动", str(error))
        page_state = await get_page_state(page)
        return build_error_response(
            session_state=session_state(session_manager),
            page_state=page_state,
            error=make_action_failed_error(
                action_type="scroll",
                message="Scroll failed.",
                diagnostic_code="SCROLL_FAILED",
            ),
        )

    snapshot_manager.invalidate()
    page_state = await get_page_state(page)

    return build_success_response(
        session_state=session_state(session_manager, reused=True),
        page_state=page_state,
        action_result=ActionResult(
            type="scroll",
            status="completed",
            detail={"direction": direction, "amount": amount, "refs_invalidated": True},
        ),
    )


async def handle_key(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    processor: ContentProcessor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    keys_str = action.get("text", "")

    keys = _normalize_keys(_split_keys(keys_str))

    if not keys:
        return await action_error_response(
            session_manager,
            page,
            make_schema_error("key action requires a 'text' field with key combo."),
        )

    try:
        if len(keys) == 1:
            await page.keyboard.press(keys[0])
        else:
            pressed = []
            for key in keys:
                await page.keyboard.down(key)
                pressed.append(key)

            for key in reversed(pressed):
                await page.keyboard.up(key)

        await page.wait_for_timeout(_SETTLE_WAIT_MS)
    except Exception as error:
        if len(keys) > 1:
            for key in reversed(pressed):
                try:
                    await page.keyboard.up(key)
                except Exception:
                    pass

        log_fail("浏览器按键", str(error))
        page_state = await get_page_state(page)
        return build_error_response(
            session_state=session_state(session_manager),
            page_state=page_state,
            error=make_action_failed_error(
                action_type="key",
                message="Key action failed.",
                diagnostic_code="KEY_FAILED",
            ),
        )

    snapshot_manager.invalidate()
    page_state = await get_page_state(page)

    return build_success_response(
        session_state=session_state(session_manager, reused=True),
        page_state=page_state,
        action_result=ActionResult(
            type="key",
            status="completed",
            detail={"keys": "+".join(keys), "refs_invalidated": True},
        ),
    )


async def handle_wait(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    processor: ContentProcessor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    raw_duration = action.get("duration", 1.0)

    duration = max(0, min(raw_duration, _WAIT_DURATION_MAX_S))

    try:
        await page.wait_for_timeout(duration * 1000)
    except Exception as error:
        log_fail("浏览器等待", str(error))
        page_state = await get_page_state(page)
        return build_error_response(
            session_state=session_state(session_manager),
            page_state=page_state,
            error=make_action_failed_error(
                action_type="wait",
                message="Wait failed.",
                diagnostic_code="WAIT_FAILED",
            ),
        )

    intervention_error = await intervention.detect(page)
    if intervention_error:
        page_state = await get_page_state(page)
        return build_error_response(
            session_state=session_state(session_manager),
            page_state=page_state,
            error=make_user_intervention_error_from_signal(intervention_error),
        )

    page_state = await get_page_state(page)

    return build_success_response(
        session_state=session_state(session_manager, reused=True),
        page_state=page_state,
        action_result=ActionResult(
            type="wait",
            status="completed",
            detail={"duration_s": duration},
        ),
    )
