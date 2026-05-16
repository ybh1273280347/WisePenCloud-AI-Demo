from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True, slots=True)
class CalendarStatusResult:
    date: date
    timezone: str
    weekday: str
    is_workday: bool
    is_holiday: bool
    is_weekend: bool
    holiday_name: Optional[str]
    status: str
