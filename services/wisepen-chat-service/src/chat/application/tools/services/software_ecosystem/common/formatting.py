from __future__ import annotations

from typing import Any, Iterable, List, Optional


def compact_text(value: Any, *, max_chars: int = 500) -> Optional[str]:
    if value is None:
        return None

    text = " ".join(str(value).split())
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def format_scalar(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value) if value else "none"
    if isinstance(value, dict):
        return ", ".join(f"{key}: {val}" for key, val in value.items()) if value else "none"
    return str(value)


def append_key_values(lines: List[str], items: Iterable[tuple[str, Any]]) -> None:
    for key, value in items:
        lines.append(f"- {key}: {format_scalar(value)}")


def truncate_result(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    suffix = "\n\n[Tool Notice] Result truncated because it exceeded the tool output budget."
    return text[: max_chars - len(suffix)].rstrip() + suffix

