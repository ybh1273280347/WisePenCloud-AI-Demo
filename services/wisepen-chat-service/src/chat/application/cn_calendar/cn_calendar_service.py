from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

import chinese_calendar

from .models import CalendarStatusResult


class CnCalendarError(RuntimeError):
    pass


class CnCalendarService:
    def resolve(self, target_date: Optional[date], *, timezone: str) -> CalendarStatusResult:
        resolved_date = target_date or datetime.now(ZoneInfo(timezone)).date()

        try:
            is_workday = bool(chinese_calendar.is_workday(resolved_date))
            is_holiday = bool(chinese_calendar.is_holiday(resolved_date))
            is_in_lieu = bool(chinese_calendar.is_in_lieu(resolved_date))
            _, holiday_name_raw = chinese_calendar.get_holiday_detail(resolved_date)
        except NotImplementedError as e:
            raise CnCalendarError(str(e)) from e
        except Exception as e:
            raise CnCalendarError(f"Failed to resolve China calendar status: {e}") from e

        holiday_name = str(holiday_name_raw) if holiday_name_raw else None
        is_weekend = resolved_date.weekday() >= 5

        if is_workday and holiday_name:
            status = "make_up_workday"
        elif is_holiday and holiday_name and is_in_lieu:
            status = "in_lieu_holiday"
        elif is_holiday and holiday_name:
            status = "public_holiday"
        elif is_weekend:
            status = "weekend_rest_day"
        else:
            status = "regular_workday"

        return CalendarStatusResult(
            date=resolved_date,
            timezone=timezone,
            weekday=resolved_date.strftime("%A"),
            is_workday=is_workday,
            is_holiday=is_holiday,
            is_weekend=is_weekend,
            holiday_name=holiday_name,
            status=status,
        )
