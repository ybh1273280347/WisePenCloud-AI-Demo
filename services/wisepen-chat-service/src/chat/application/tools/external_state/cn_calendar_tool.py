from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from chat.application.tools.services.cn_calendar import CnCalendarError, CnCalendarService, CalendarStatusResult
from chat.application.tools.config import DEFAULT_TOOL_TIMEZONE
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail, log_ok


_TOOL_DESCRIPTION = (
    "Checks the Mainland China calendar status for a specific date. "
    "Use this tool when the user asks whether a date is a workday, weekend, public holiday, "
    "make-up workday, adjusted rest day, or whether a weekend needs work in China.\n\n"
    "Call cn_calendar when the user asks: 今天是不是休息日, 某天是不是法定节假日, "
    "某天是不是调休, 某个周末是否要上班, 中国节假日/工作日/调休日判断, "
    "or whether a task should be scheduled by China workday rules.\n\n"
    "If target_date is omitted, the tool checks today's date in the given timezone. "
    "For Mainland China calendar checks, use timezone='Asia/Shanghai'."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "target_date": {
            "type": "string",
            "description": (
                "Optional date to check in YYYY-MM-DD format. "
                "Omit this field to check today's date in the given timezone."
            ),
            "pattern": r"^\d{4}-\d{2}-\d{2}$",
        },
        "timezone": {
            "type": "string",
            "default": "Asia/Shanghai",
            "description": (
                "IANA timezone used only when target_date is omitted."
            ),
        },
    },
    "additionalProperties": False,
}


class CnCalendarTool(BaseTool):
    def __init__(self, service: Optional[CnCalendarService] = None) -> None:
        self._service = service or CnCalendarService()

    @property
    def name(self) -> str:
        return "cn_calendar"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        raw_target_date = kwargs.get("target_date")

        try:
            timezone = _normalize_timezone(kwargs.get("timezone") or DEFAULT_TOOL_TIMEZONE)
            target_date = _parse_target_date(raw_target_date)
        except CnCalendarError as e:
            log_fail(
                "cn_calendar",
                e,
                target_date=raw_target_date,
                timezone=kwargs.get("timezone"),
            )
            return _format_tool_error(str(e))

        try:
            result = self._service.resolve(target_date, timezone=timezone)
        except CnCalendarError as e:
            log_fail(
                "cn_calendar",
                e,
                target_date=raw_target_date,
                timezone=timezone,
            )
            return _format_tool_error(str(e))
        except Exception as e:
            log_fail(
                "cn_calendar unexpected",
                e,
                target_date=raw_target_date,
                timezone=timezone,
            )
            return _format_tool_error(f"Unexpected China calendar status error: {e}")

        log_ok(
            "cn_calendar",
            target_date=str(result.date),
            timezone=result.timezone,
            status=result.status,
            is_workday=result.is_workday,
            is_holiday=result.is_holiday,
        )

        return _format_calendar_status(result)


def _normalize_timezone(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CnCalendarError("timezone must be a valid IANA timezone string.")
    timezone = value.strip()
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as e:
        raise CnCalendarError("timezone must be a valid IANA timezone string.") from e
    return timezone


def _parse_target_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise CnCalendarError("target_date must be in YYYY-MM-DD format.")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as e:
        raise CnCalendarError("target_date must be in YYYY-MM-DD format.") from e


def _format_calendar_status(result: CalendarStatusResult) -> str:
    holiday_name = result.holiday_name or "none"
    lines = [
        "[Tool Result] cn_calendar",
        "",
        f"Date: {result.date}",
        f"Timezone: {result.timezone}",
        f"Weekday: {result.weekday}",
        f"Status: {result.status}",
        f"Holiday name: {holiday_name}",
        f"Is workday: {_format_bool(result.is_workday)}",
        f"Is holiday: {_format_bool(result.is_holiday)}",
        f"Is weekend: {_format_bool(result.is_weekend)}",
        "",
        "Status meanings:",
        "- regular_workday: normal workday",
        "- weekend_rest_day: normal weekend rest day",
        "- public_holiday: statutory public holiday",
        "- in_lieu_holiday: adjusted holiday / rest day in lieu",
        "- make_up_workday: adjusted make-up workday",
        "",
        "Assistant instructions:",
        "- Use this result as the China calendar status for the requested date.",
        "- Use is_workday for business-day decisions.",
        "- If status is make_up_workday, explain that it may be a weekend but is officially adjusted as a workday.",
        "- If status is public_holiday or in_lieu_holiday, explain that it is not a regular workday.",
        "- Do not infer other countries' holiday status from this tool.",
    ]
    return "\n".join(lines)


def _format_tool_error(message: str) -> str:
    return (
        "[Tool Error] cn_calendar failed: "
        f"{message}\n\n"
        "Assistant fallback instructions:\n"
        "- Do not guess China holiday adjustment status if the tool failed.\n"
        "- If the failure is caused by an unsupported date range, say that the current China calendar data cannot determine that date.\n"
        "- The assistant may still answer general weekday/weekend information only if it can compute it reliably."
    )


def _format_bool(value: bool) -> str:
    return "true" if value else "false"
