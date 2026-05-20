from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE_NAME = "Asia/Shanghai"

_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def get_current_datetime() -> datetime:
    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE_NAME))


def build_current_date_context() -> str:
    now = get_current_datetime()
    weekday = _WEEKDAYS[now.weekday()]
    current_date = now.date().isoformat()
    return (
        f"Current date: {current_date} ({weekday}), timezone baseline: "
        f"{DEFAULT_TIMEZONE_NAME}.\n"
        "Use this date as the anchor for relative date expressions such as "
        '"recently", "today", "yesterday", "this week", "this year", '
        '"最近", "今天", "昨天", "本周", and "今年".\n'
        "For current clock time, cross-timezone current time, or precise time "
        "conversion, call the resolve_time tool."
    )
