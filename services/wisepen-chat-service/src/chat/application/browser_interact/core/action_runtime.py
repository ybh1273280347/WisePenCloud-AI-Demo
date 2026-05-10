from typing import Optional, Tuple

from playwright.async_api import Page

from .protocol import (
    ToolError,
    ToolErrorCode,
    build_error_response,
    get_page_state,
    get_session_state,
    make_browser_unavailable_error,
    make_profile_locked_error,
    make_profile_unavailable_error,
    make_invalid_browser_channel_error,
    make_schema_error,
    make_session_error,
)
from .session import BrowserSessionError, BrowserSessionManager
from .snapshot import ref_selector



def session_state(
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


async def action_error_response(
    session_manager: BrowserSessionManager,
    page: Optional[Page],
    error: ToolError,
) -> str:
    return build_error_response(
        session_state=session_state(session_manager),
        page_state=await get_page_state(page),
        error=error,
    )


async def get_existing_page_or_error(
    session_manager: BrowserSessionManager,
    browser_session_id: Optional[str],
) -> Tuple[Optional[Page], Optional[str]]:
    page, error_code = await session_manager.get_existing_page(browser_session_id)

    if error_code is None:
        return page, None

    return None, build_error_response(
        session_state=session_state(session_manager),
        page_state=await get_page_state(session_manager.page),
        error=make_session_error(error_code, session_manager.session_id),
    )


async def selector_or_error_response(
    session_manager: BrowserSessionManager,
    page: Page,
    ref: str,
) -> Tuple[Optional[str], Optional[str]]:
    try:
        return ref_selector(ref), None
    except ValueError:
        return None, await action_error_response(
            session_manager,
            page,
            make_schema_error(
                f"Invalid ref '{ref}'. Ref must be an exact id from the latest snapshot, such as 'e1', 'e2', 'e123'. "
                f"Do NOT use role names like 'searchbox', 'button', 'link', 'textbox' or labels like 'Search'."
            ),
        )


async def get_or_create_page_or_error(
    session_manager: BrowserSessionManager,
    browser_session_id: Optional[str],
) -> Tuple[Optional[Page], Optional[str]]:
    try:
        page, error_code = await session_manager.get_or_create_page(browser_session_id)
    except BrowserSessionError as error:
        return None, build_error_response(
            session_state=session_state(session_manager),
            page_state=await get_page_state(session_manager.page),
            error=browser_error_from_diagnostic(error.diagnostic_code),
        )

    if error_code is not None:
        return None, build_error_response(
            session_state=session_state(session_manager),
            page_state=await get_page_state(session_manager.page),
            error=browser_error_from_code(error_code, session_manager.session_id),
        )

    return page, None


def browser_error_from_diagnostic(diagnostic_code: str) -> ToolError:
    if diagnostic_code == "PROFILE_LOCKED":
        return make_profile_locked_error(
            message="Browser profile directory is locked by another process.",
            diagnostic_code=diagnostic_code,
        )
    if diagnostic_code == "PROFILE_UNAVAILABLE":
        return make_profile_unavailable_error(
            message="Browser profile directory is not usable.",
            diagnostic_code=diagnostic_code,
        )
    if diagnostic_code == "INVALID_BROWSER_CHANNEL":
        return make_invalid_browser_channel_error(
            message="Browser channel executable not found.",
            diagnostic_code=diagnostic_code,
        )
    if diagnostic_code == "NO_DISPLAY_SERVER":
        return make_browser_unavailable_error(
            message="No display server available for headed browser.",
            diagnostic_code=diagnostic_code,
        )
    if diagnostic_code == "BROWSER_CRASHED_ON_START":
        return make_browser_unavailable_error(
            message="Browser process crashed immediately after launch.",
            diagnostic_code=diagnostic_code,
        )
    if diagnostic_code == "LAUNCH_TIMEOUT":
        return make_browser_unavailable_error(
            message="Browser launch timed out.",
            diagnostic_code=diagnostic_code,
        )
    return make_browser_unavailable_error(
        message="Browser runtime is unavailable.",
        diagnostic_code=diagnostic_code,
    )


def browser_error_from_code(
    error_code: ToolErrorCode,
    current_session_id: Optional[str],
) -> ToolError:
    if error_code == ToolErrorCode.PROFILE_LOCKED:
        return make_profile_locked_error(
            message="Browser profile directory is locked by another process.",
        )
    if error_code == ToolErrorCode.PROFILE_UNAVAILABLE:
        return make_profile_unavailable_error(
            message="Browser profile directory is not usable.",
        )
    return make_session_error(error_code, current_session_id)