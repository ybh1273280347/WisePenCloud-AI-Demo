from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

from chat.application.browser_interact.core.actions.control import WAIT_DURATION_MAX_S
from chat.application.browser_interact.core.controller import BrowserInteractController
from chat.domain.interfaces.tool import BaseTool

TOOL_DESCRIPTION = (
    "Agent-safe browser interaction tool based on snapshot+ref. "
    "Operates a local persistent browser with dedicated automation profile. "
    "If a fill_ref returns refs_invalidated=true, take a new snapshot before any ref action. "
    "Refs do not exist until you call snapshot. "
    "After snapshot, use only exact refs from snapshot output, such as e5. "
    "Never use role names or labels as ref values, such as searchbox, button, link, textbox, Search, or Sign in. "
    "Flow: 1) navigate, 2) snapshot, 3) use click_ref/fill_ref/select_ref/check_ref with exact ref ids. "
    "snapshot_id is optional for ref actions and defaults to the current snapshot. "
    "Always pass browser_session_id from the previous response. "
    "Use fill_ref only on refs with flags=fillable. "
    "Use click_ref for links and buttons. "
    "After filling a searchbox or combobox, press Enter with key or take a fresh snapshot before clicking suggestions/buttons. "
    "For finding a specific control on a complex page, prefer snapshot with mode='focused' and a goal such as 'search box' or 'sign in'. "
    "For login pages, click the sign-in link first if the current page only shows a sign-in link. "
    "On STALE_REF or SNAPSHOT_REQUIRED, take a new snapshot first. "
    "On USER_INTERVENTION_REQUIRED, prompt the user to act in the browser window. "
    "IMPORTANT: When you need to extract content from a page to answer the user's question, always call get_content first before answering. "
    "Do not try to answer based on just snapshot tree - get_content provides the actual page content and context you need."
)

TOOL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "browser_session_id": {
            "type": "string",
            "description": (
                "Session ID from the previous response. "
                "Required after the first successful action. "
                "Passing a wrong session_id returns SESSION_MISMATCH. "
                "An empty string is treated as missing."
            ),
        },
        "action": {
            "type": "object",
            "additionalProperties": False,
            "description": (
                "A single action to perform. Each action type has specific required fields:\n"
                "- navigate: requires url\n"
                "- click_ref: requires ref; snapshot_id defaults to current\n"
                "- fill_ref: requires ref and text; snapshot_id defaults to current\n"
                "- select_ref: requires ref and value or label; snapshot_id defaults to current\n"
                "- check_ref: requires ref; snapshot_id defaults to current; checked is optional\n"
                "- key: requires text\n"
                "- wait: duration is optional and must be a number\n"
                "- scroll: scroll_direction and scroll_amount are optional\n"
                "- snapshot: mode/goal/limit are optional; use mode='focused' with goal to find a specific control\n"
                "- status, screenshot, get_content, go_back, go_forward: only type is needed"
            ),
            "properties": {
                "type": {
                    "type": "string",
                    "enum": [
                        "navigate",
                        "go_back",
                        "go_forward",
                        "snapshot",
                        "click_ref",
                        "fill_ref",
                        "select_ref",
                        "check_ref",
                        "scroll",
                        "key",
                        "wait",
                        "screenshot",
                        "get_content",
                        "status",
                    ],
                    "description": "Action type.",
                },
                "url": {
                    "type": "string",
                    "description": "URL to navigate to. Required for navigate.",
                },
                "snapshot_id": {
                    "type": "string",
                    "description": (
                        "Snapshot ID from the last snapshot. Optional for ref actions; "
                        "if omitted, the current snapshot is used. If provided, it must match the current snapshot_id."
                    ),
                },
                "ref": {
                    "type": "string",
                    "pattern": "^e[1-9][0-9]*$",
                    "description": (
                        "Exact ref id from the latest snapshot, e.g. e5. "
                        "Must match ^e[1-9][0-9]*$. "
                        "Never use role names or labels as ref, such as searchbox, button, link, textbox, Search, or Sign in. "
                        "Required for click_ref, fill_ref, select_ref, and check_ref."
                    ),
                },
                "text": {
                    "type": "string",
                    "description": "Text to fill or key combo.",
                },
                "value": {
                    "type": "string",
                    "description": "Option value for select_ref.",
                },
                "label": {
                    "type": "string",
                    "description": "Visible option label for select_ref.",
                },
                "checked": {
                    "type": "boolean",
                    "description": "Desired checkbox/radio state for check_ref. Default: true.",
                },
                "scroll_direction": {
                    "type": "string",
                    "enum": ["up", "down", "left", "right"],
                },
                "scroll_amount": {
                    "type": "integer",
                    "description": "Number of scroll steps.",
                },
                "duration": {
                    "type": "number",
                    "description": (
                        f"Duration in seconds for wait action "
                        f"(max {WAIT_DURATION_MAX_S}s)."
                    ),
                },
                "wait_until": {
                    "type": "string",
                    "enum": ["domcontentloaded", "load", "networkidle"],
                    "description": "Navigation wait strategy. Default: domcontentloaded.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["full", "focused"],
                    "description": (
                        "Snapshot mode. Default: full. "
                        "Use focused when looking for a specific control or target on a complex page."
                    ),
                },
                "goal": {
                    "type": "string",
                    "description": (
                        "Focused snapshot ranking hint, such as 'search box', 'sign in', "
                        "'password field', 'submit button', or 'content area'."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Maximum elements returned by focused snapshot.",
                },
            },
            "required": ["type"],
        },
    },
    "required": ["action"],
}


class BrowseInteractTool(BaseTool):
    def __init__(
        self,
        automation_user_data_dir: Optional[str | Path] = None,
        browser_channel: Optional[str] = None,
        timeout: int = 30,
        disable_sandbox: bool = False,
        disable_dev_shm_usage: bool = False,
    ) -> None:
        self._controller = BrowserInteractController(
            automation_user_data_dir=automation_user_data_dir,
            browser_channel=browser_channel,
            timeout=timeout,
            disable_sandbox=disable_sandbox,
            disable_dev_shm_usage=disable_dev_shm_usage,
        )

    @property
    def name(self) -> str:
        return "browse_interact"

    @property
    def description(self) -> str:
        return TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return deepcopy(TOOL_SCHEMA)

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        return await self._controller.execute(
            context=context,
            request=kwargs,
        )

    async def close(self) -> None:
        await self._controller.cleanup()
