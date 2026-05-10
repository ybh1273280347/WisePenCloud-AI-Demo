import asyncio
from typing import Any, Awaitable, Callable, Dict, Optional

from chat.application.web_fetch.content_processor import ContentProcessor
from common.logger import log_error

from .actions import (
    handle_click_ref,
    handle_check_ref,
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
from .protocol import (
    build_error_response,
    get_page_state,
    get_session_state,
    make_internal_error,
    make_no_action_error,
    make_schema_error,
    make_unknown_action_error,
)
from .intervention import UserInterventionDetector
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
        self._execute_lock = asyncio.Lock()

    async def cleanup(self) -> None:
        await self._session_manager.cleanup()

    async def execute(
        self,
        request: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        async with self._execute_lock:
            return await self._execute_inner(
                request=request,
                context=context,
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
                "browse_interact 执行异常",
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
