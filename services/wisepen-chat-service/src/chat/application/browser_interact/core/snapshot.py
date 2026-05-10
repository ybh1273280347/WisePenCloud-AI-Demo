import json
import re
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import Page

from .protocol import (
    SnapshotPayload,
    ToolError,
    make_snapshot_required_error,
    make_stale_ref_error,
)

SNAPSHOT_ID_BYTES = 8
SNAPSHOT_DEFAULT_MODE = "full"
SNAPSHOT_FOCUSED_DEFAULT_LIMIT = 30
SNAPSHOT_MAX_LIMIT = 200
SNAPSHOT_SCHEMA_VERSION = 1
REF_PATTERN = re.compile(r"e[1-9][0-9]*")
GOAL_SYNONYMS = {
    "find": "search",
    "query": "search",
    "lookup": "search",
    "look": "search",
    "搜索": "search",
    "查找": "search",
    "searchbox": "search",
    "field": "input",
    "box": "input",
    "textbox": "input",
    "输入": "input",
    "邮箱": "email",
    "邮件": "email",
    "mail": "email",
    "username": "user",
    "login": "signin",
    "log": "signin",
    "signin": "signin",
    "登录": "signin",
    "password": "password",
    "密码": "password",
    "submit": "submit",
    "button": "button",
}
FIELD_WEIGHTS = {
    "label": 10,
    "placeholder": 10,
    "ariaLabel": 10,
    "name": 8,
    "id": 7,
    "title": 6,
    "role": 5,
    "type": 5,
    "ancestorText": 4,
    "tag": 2,
    "className": 1,
}

SCRIPT_PATH = Path(__file__).parent.parent / "snapshot_script.js"
SNAPSHOT_SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")


def ref_selector(ref: str) -> str:
    if not isinstance(ref, str) or not REF_PATTERN.fullmatch(ref):
        raise ValueError("ref must match e1/e2/e123 format")
    return f"[data-agent-ref='{ref}']"


def parse_snapshot_raw(raw: str) -> List[Dict[str, Any]]:
    if not isinstance(raw, str):
        raise ValueError("snapshot script returned a non-string payload")

    payload = json.loads(raw)

    if not isinstance(payload, dict):
        raise ValueError("snapshot script returned a non-object payload")

    if payload.get("schemaVersion") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("unsupported snapshot schemaVersion")

    elements = payload.get("elements")
    if not isinstance(elements, list):
        raise ValueError("snapshot payload missing elements list")

    for element in elements:
        if not isinstance(element, dict):
            raise ValueError("snapshot element must be an object")
        ref = element.get("ref")
        if not isinstance(ref, str) or not REF_PATTERN.fullmatch(ref):
            raise ValueError("snapshot element has invalid ref")

    return elements


def format_field_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


def format_snapshot_tree(elements: List[Dict[str, Any]]) -> str:
    lines = []
    for element in elements:
        ref = element.get("ref", "")
        role = element.get("role") or element.get("tag") or "element"
        label = str(element.get("label") or "").strip()
        flags = element.get("flags") or []
        parts = [f"ref={ref}", f"role={role}"]

        if label:
            parts.append(f'label="{format_field_value(label)}"')

        if flags:
            parts.append("flags=" + ",".join(str(flag) for flag in flags))

        lines.append(" | ".join(parts))

    return "\n".join(lines)


def focused_elements(
    elements: List[Dict[str, Any]],
    goal: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:
    terms = normalize_goal_terms(goal)

    def score(element: Dict[str, Any]) -> int:
        value = 0
        phrase = normalize_text(goal or "")

        for field, weight in FIELD_WEIGHTS.items():
            text = normalize_text(str(element.get(field) or ""))
            if not text:
                continue

            if phrase and phrase in text:
                value += weight * 3

            for term in terms:
                if term in text:
                    value += weight

        role = normalize_text(str(element.get("role") or ""))
        element_type = normalize_text(str(element.get("type") or ""))

        if "search" in terms and role in ("searchbox", "search"):
            value += 12
        if "search" in terms and element_type == "search":
            value += 10
        if "email" in terms and element_type == "email":
            value += 14
        if "password" in terms and element_type == "password":
            value += 14
        if "input" in terms and element.get("fillable"):
            value += 8
        if "submit" in terms and role == "button":
            value += 8

        if element.get("inViewport"):
            value += 4
        if element.get("fillable"):
            value += 10
        if element.get("clickable"):
            value += 3

        if not terms and element.get("inViewport"):
            value += 2

        return value

    indexed = list(enumerate(elements))
    indexed.sort(key=lambda item: (-score(item[1]), item[0]))
    return [element for _, element in indexed[:limit]]


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def normalize_goal_terms(goal: Optional[str]) -> List[str]:
    normalized_goal = normalize_text(goal or "")
    raw_terms = [
        term
        for term in re.split(r"\W+", normalized_goal)
        if term
    ]
    terms = []

    for term in raw_terms:
        normalized = GOAL_SYNONYMS.get(term, term)
        terms.append(normalized)
        if normalized != term:
            terms.append(term)

    for alias, canonical in GOAL_SYNONYMS.items():
        if alias in normalized_goal:
            terms.append(canonical)
            terms.append(alias)

    return list(dict.fromkeys(terms))


class SnapshotManager:
    def __init__(self) -> None:
        self._current_snapshot_id: Optional[str] = None

    @property
    def current_snapshot_id(self) -> Optional[str]:
        return self._current_snapshot_id

    def invalidate(self) -> None:
        self._current_snapshot_id = None

    def resolve_current(self, snapshot_id: Optional[str]) -> Tuple[Optional[str], Optional[ToolError]]:
        if snapshot_id is None:
            if self._current_snapshot_id is None:
                return None, make_snapshot_required_error()
            return self._current_snapshot_id, None

        if snapshot_id != self._current_snapshot_id:
            return None, make_stale_ref_error(snapshot_id)

        return self._current_snapshot_id, None

    async def take(
        self,
        page: Page,
        *,
        mode: str = SNAPSHOT_DEFAULT_MODE,
        goal: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> SnapshotPayload:
        raw = await page.evaluate(SNAPSHOT_SCRIPT)
        elements = parse_snapshot_raw(raw)

        if mode == "full":
            pass
        elif mode == "focused":
            if limit is None:
                limit = SNAPSHOT_FOCUSED_DEFAULT_LIMIT
            else:
                limit = max(1, min(limit, SNAPSHOT_MAX_LIMIT))
            elements = focused_elements(elements, goal, limit)
        else:
            raise ValueError(f"unsupported snapshot mode: {mode}")

        tree = format_snapshot_tree(elements)

        snapshot_id = secrets.token_hex(SNAPSHOT_ID_BYTES)
        self._current_snapshot_id = snapshot_id

        return SnapshotPayload(
            snapshot_id=snapshot_id,
            tree=tree,
            mode=mode,
            goal=goal,
        )
