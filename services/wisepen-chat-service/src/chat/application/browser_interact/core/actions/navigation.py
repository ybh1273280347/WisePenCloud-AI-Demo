from typing import Dict, Optional
from urllib.parse import urlparse

from chat.application.web_fetch.content_processor import ContentProcessor
from common.logger import log_fail, log_ok

from ..action_runtime import (
    action_error_response,
    get_existing_page_or_error,
    get_or_create_page_or_error,
    session_state,
)
from ..intervention import UserInterventionDetector
from ..protocol import (
    ActionResult,
    build_error_response,
    build_success_response,
    get_page_state,
    make_action_failed_error,
    make_schema_error,
    make_user_intervention_error_from_signal,
)
from ..session import BrowserSessionManager
from ..snapshot import SnapshotManager
from .ref import _SETTLE_WAIT_MS

_NAVIGATION_TIMEOUT_MS = 30000


def _redact_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        if parsed.hostname:
            return f"{parsed.scheme}://{parsed.hostname}/***"
        return url
    except Exception:
        return url


async def handle_navigate(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    processor: ContentProcessor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    url = action.get("url", "")
    if not url:
        return await action_error_response(
            session_manager,
            session_manager.page,
            make_schema_error("navigate action requires a 'url' field."),
        )

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    was_alive = session_manager.is_session_alive

    page, session_error_response = await get_or_create_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    wait_until = action.get("wait_until", "domcontentloaded")

    snapshot_manager.invalidate()

    try:
        await page.goto(url, wait_until=wait_until, timeout=_NAVIGATION_TIMEOUT_MS)
        await page.wait_for_timeout(_SETTLE_WAIT_MS)
    except Exception as error:
        error_message = str(error)
        is_timeout = "Timeout" in error_message or "timeout" in error_message
        diagnostic_code = "NAVIGATION_TIMEOUT" if is_timeout else "NAVIGATION_FAILED"
        log_fail("浏览器导航", error_message, url=_redact_url(url))
        page_state = await get_page_state(page)
        return build_error_response(
            session_state=session_state(session_manager),
            page_state=page_state,
            error=make_action_failed_error(
                action_type="navigate",
                message="Navigation failed.",
                diagnostic_code=diagnostic_code,
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
    log_ok("浏览器导航", url=_redact_url(url))

    created = not was_alive
    reused = was_alive

    return build_success_response(
        session_state=session_state(
            session_manager,
            created=created,
            reused=reused,
        ),
        page_state=page_state,
        action_result=ActionResult(
            type="navigate",
            status="completed",
            detail={
                "url": page.url,
                "title": page_state.title if page_state else "",
                "refs_invalidated": True,
            },
        ),
    )


async def handle_go_back(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    processor: ContentProcessor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    return await handle_navigation_direction(
        session_manager=session_manager,
        snapshot_manager=snapshot_manager,
        intervention=intervention,
        browser_session_id=browser_session_id,
        act_type="go_back",
    )


async def handle_go_forward(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    processor: ContentProcessor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    return await handle_navigation_direction(
        session_manager=session_manager,
        snapshot_manager=snapshot_manager,
        intervention=intervention,
        browser_session_id=browser_session_id,
        act_type="go_forward",
    )


async def handle_navigation_direction(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    browser_session_id: Optional[str],
    act_type: str,
) -> str:
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    snapshot_manager.invalidate()

    try:
        if act_type == "go_back":
            await page.go_back()
        else:
            await page.go_forward()

        await page.wait_for_timeout(_SETTLE_WAIT_MS)
    except Exception as error:
        log_fail("浏览器导航", str(error))
        page_state = await get_page_state(page)
        return build_error_response(
            session_state=session_state(session_manager),
            page_state=page_state,
            error=make_action_failed_error(
                action_type=act_type,
                message=f"{act_type} failed.",
                diagnostic_code="NAVIGATION_FAILED",
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
            type=act_type,
            status="completed",
            detail={"url": page.url, "refs_invalidated": True},
        ),
    )
