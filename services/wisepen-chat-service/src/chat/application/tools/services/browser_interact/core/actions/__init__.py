from chat.application.tools.services.browser_interact.core.actions.control import (
    handle_key,
    handle_scroll,
    handle_wait,
)
from chat.application.tools.services.browser_interact.core.actions.navigation import (
    handle_go_back,
    handle_go_forward,
    handle_navigate,
)
from chat.application.tools.services.browser_interact.core.actions.observation import (
    handle_get_content,
    handle_screenshot,
    handle_snapshot,
)
from chat.application.tools.services.browser_interact.core.actions.ref import (
    handle_check_ref,
    handle_click_ref,
    handle_fill_ref,
    handle_select_ref,
)
from chat.application.tools.services.browser_interact.core.actions.status import handle_status

__all__ = [
    "handle_navigate",
    "handle_go_back",
    "handle_go_forward",
    "handle_click_ref",
    "handle_fill_ref",
    "handle_select_ref",
    "handle_check_ref",
    "handle_snapshot",
    "handle_screenshot",
    "handle_get_content",
    "handle_scroll",
    "handle_key",
    "handle_wait",
    "handle_status",
]
