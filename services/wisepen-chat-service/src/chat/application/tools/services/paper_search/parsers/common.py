from __future__ import annotations

import re
from typing import Mapping, Optional


def first_string(value: object) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return " ".join(value.split()).strip()
    if isinstance(value, list):
        for item in value:
            text = first_string(item)
            if text:
                return text
    return None


def date_parts_to_iso(value: object) -> tuple[Optional[str], Optional[int]]:
    parts = _date_parts(value)
    if not parts:
        return None, None

    year = parts[0]
    try:
        year_int = int(year)
    except (TypeError, ValueError):
        return None, None

    if len(parts) >= 3:
        try:
            return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}", year_int
        except (TypeError, ValueError):
            return str(year_int), year_int
    if len(parts) >= 2:
        try:
            return f"{int(parts[0]):04d}-{int(parts[1]):02d}-01", year_int
        except (TypeError, ValueError):
            return str(year_int), year_int
    return str(year_int), year_int


def strip_markup(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = re.sub(r"<[^>]+>", " ", value)
    text = " ".join(text.split()).strip()
    return text or None


def _date_parts(value: object) -> list[object]:
    if isinstance(value, Mapping):
        date_parts = value.get("date-parts")
        if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list):
            return date_parts[0]
    return []
