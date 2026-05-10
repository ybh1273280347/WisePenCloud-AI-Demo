from typing import Dict, Optional

from chat.application.web_fetch.content_processor import ContentProcessor

from ..protocol import (
    ActionResult,
    build_error_response,
    build_success_response,
    get_page_state,
    make_session_error,
)
from ..intervention import UserInterventionDetector
from ..session import BrowserSessionManager
from ..snapshot import SnapshotManager
from ..action_runtime import session_state

async def handle_status(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    processor: ContentProcessor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    if browser_session_id is not None:
        error_code = await session_manager.validate_session(browser_session_id)
        if error_code is not None:
            return build_error_response(
                session_state=session_state(session_manager),
                page_state=await get_page_state(session_manager.page),
                error=make_session_error(error_code, session_manager.session_id),
            )

    page_state = await get_page_state(session_manager.page)

    return build_success_response(
        session_state=session_state(
            session_manager,
            reused=session_manager.is_session_alive,
        ),
        page_state=page_state,
        action_result=ActionResult(
            type="status",
            status="completed",
            detail={
                "has_session": session_manager.has_session,
                "is_session_alive": session_manager.is_session_alive,
            },
        ),
    )