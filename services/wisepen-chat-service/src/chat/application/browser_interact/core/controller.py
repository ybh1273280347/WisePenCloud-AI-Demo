import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from chat.application.web_fetch.content_processor import ContentProcessor
from common.logger import log_error, log_event

from .actions import (
    handle_check_ref,
    handle_click_ref,
    handle_fill_ref,
    handle_get_content,
    handle_go_back,
    handle_go_forward,
    handle_key,
    handle_navigate,
    handle_screenshot,
    handle_scroll,
    handle_select_ref,
    handle_snapshot,
    handle_status,
    handle_wait,
)
from .intervention import UserInterventionDetector
from .protocol import (
    build_error_response,
    get_page_state,
    get_session_state,
    make_internal_error,
    make_no_action_error,
    make_schema_error,
    make_unknown_action_error,
)
from .session import BrowserSessionManager
from .snapshot import SnapshotManager

ActionHandler = Callable[
    [
        BrowserSessionManager,
        SnapshotManager,
        UserInterventionDetector,
        ContentProcessor,
        Optional[str],
        Dict[str, Any],
    ],
    Awaitable[str],
]


ACTION_HANDLERS: Dict[str, ActionHandler] = {
    "status": handle_status,
    "navigate": handle_navigate,
    "go_back": handle_go_back,
    "go_forward": handle_go_forward,
    "snapshot": handle_snapshot,
    "screenshot": handle_screenshot,
    "click_ref": handle_click_ref,
    "fill_ref": handle_fill_ref,
    "select_ref": handle_select_ref,
    "check_ref": handle_check_ref,
    "scroll": handle_scroll,
    "key": handle_key,
    "wait": handle_wait,
    "get_content": handle_get_content,
}


_NEW_SESSION_LOCK_KEY = "__new_browser_session__"


@dataclass(slots=True)
class _SessionLockEntry:
    lock: asyncio.Lock
    ref_count: int = 0


class BrowserInteractController:
    def __init__(
        self,
        automation_user_data_dir=None,
        browser_channel=None,
        timeout: int = 30,
        disable_sandbox: bool = False,
        disable_dev_shm_usage: bool = False,
    ) -> None:
        self._session_manager = BrowserSessionManager(
            automation_user_data_dir=automation_user_data_dir,
            browser_channel=browser_channel,
            timeout=timeout,
            disable_sandbox=disable_sandbox,
            disable_dev_shm_usage=disable_dev_shm_usage,
        )
        self._snapshot_manager = SnapshotManager()
        self._intervention = UserInterventionDetector()
        self._processor = ContentProcessor()
        self._session_locks: Dict[str, _SessionLockEntry] = {}
        self._session_locks_guard = asyncio.Lock()

    async def cleanup(self) -> None:
        await self._session_manager.cleanup()
        async with self._session_locks_guard:
            self._session_locks.clear()
        log_event("browse_interact controller 已关闭")

    async def execute(
        self,
        request: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        lock_key = self._session_lock_key(request)
        lock_entry = await self._borrow_session_lock(lock_key)
        started = time.monotonic()
        action_type = _action_type(request)
        result = ""
        success = False

        try:
            async with lock_entry.lock:
                result = await self._execute_inner(
                    request=request,
                    context=context,
                )
                success = '"success": true' in result[:80]
                return result
        finally:
            await self._release_session_lock(lock_key)
            log_event(
                "tool_perf",
                tool_name="browse_interact",
                stage=action_type,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                success=success,
                cache_hit=False,
                fallback_used=False,
                worker_count=len(self._session_locks),
            )

    async def _execute_inner(
        self,
        request: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        raw_browser_session_id = request.get("browser_session_id")
        browser_session_id = raw_browser_session_id or None

        action = request.get("action")

        if action is None:
            return await self._no_action()

        if not isinstance(action, dict):
            return await self._invalid_schema("Action must be an object.")

        act_type = action.get("type")

        if not act_type:
            return await self._invalid_schema("Action must have a string 'type' field.")

        handler = ACTION_HANDLERS.get(act_type)
        if not handler:
            return await self._unknown_action(act_type)

        try:
            return await handler(
                self._session_manager,
                self._snapshot_manager,
                self._intervention,
                self._processor,
                browser_session_id,
                action,
            )
        except Exception as error:
            log_error(
                "browse_interact 执行",
                str(error),
                action_type=act_type,
            )
            return build_error_response(
                session_state=get_session_state(
                    self._session_manager.session_id,
                    valid=self._session_manager.is_session_alive,
                ),
                page_state=await get_page_state(self._session_manager.page),
                error=make_internal_error(
                    message="Browse interaction failed unexpectedly.",
                    diagnostic_code="UNHANDLED_CONTROLLER_ERROR",
                ),
            )

    async def _no_action(self) -> str:
        return build_error_response(
            session_state=get_session_state(
                self._session_manager.session_id,
                valid=self._session_manager.is_session_alive,
            ),
            page_state=await get_page_state(self._session_manager.page),
            error=make_no_action_error(),
        )

    async def _invalid_schema(self, message: str) -> str:
        return build_error_response(
            session_state=get_session_state(
                self._session_manager.session_id,
                valid=self._session_manager.is_session_alive,
            ),
            page_state=await get_page_state(self._session_manager.page),
            error=make_schema_error(message),
        )

    async def _unknown_action(self, act_type: str) -> str:
        return build_error_response(
            session_state=get_session_state(
                self._session_manager.session_id,
                valid=self._session_manager.is_session_alive,
            ),
            page_state=await get_page_state(self._session_manager.page),
            error=make_unknown_action_error(act_type),
        )

    def _session_lock_key(self, request: Dict[str, Any]) -> str:
        raw_browser_session_id = request.get("browser_session_id")
        if isinstance(raw_browser_session_id, str) and raw_browser_session_id.strip():
            return raw_browser_session_id.strip()

        current_session_id = self._session_manager.session_id
        if current_session_id:
            return current_session_id

        return _NEW_SESSION_LOCK_KEY

    async def _borrow_session_lock(self, key: str) -> _SessionLockEntry:
        async with self._session_locks_guard:
            entry = self._session_locks.get(key)
            if entry is None:
                entry = _SessionLockEntry(lock=asyncio.Lock())
                self._session_locks[key] = entry
            entry.ref_count += 1
            return entry

    async def _release_session_lock(self, key: str) -> None:
        async with self._session_locks_guard:
            entry = self._session_locks.get(key)
            if entry is None:
                return
            entry.ref_count = max(0, entry.ref_count - 1)
            if entry.ref_count == 0 and not entry.lock.locked():
                self._session_locks.pop(key, None)


def _action_type(request: Dict[str, Any]) -> str:
    action = request.get("action")
    if not isinstance(action, dict):
        return "invalid_action"
    act_type = action.get("type")
    return act_type if isinstance(act_type, str) and act_type else "unknown_action"
