"""Analysis windows.

One rule, applied everywhere: **every window ends on the last complete day.**

Both platforms are still writing to today's numbers while today is happening,
and a partial day drags CAC upward every morning. Reporting through yesterday
means the number Sean sees at 8am is the same number he saw at 8pm, and the UI
states the end date rather than saying a vague "today".
"""

from __future__ import annotations

import calendar
import datetime as dt
from typing import Dict, List, NamedTuple, Tuple


class Window(NamedTuple):
    key: str
    label: str
    start: dt.date
    end: dt.date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> "Window":
        """The equal-length window immediately before this one."""
        length = self.days
        end = self.start - dt.timedelta(days=1)
        return Window(self.key + "_prev", "Prior " + str(length) + " days", end - dt.timedelta(days=length - 1), end)


RANGES: Dict[str, str] = {
    "7d": "Last 7 days",
    "28d": "Last 28 days",
    "90d": "Last 90 days",
    "mtd": "Month to date",
}

DEFAULT_RANGE = "28d"


def last_complete_day(today: dt.date) -> dt.date:
    return today - dt.timedelta(days=1)


def resolve(key: str, today: dt.date) -> Window:
    if key not in RANGES:
        key = DEFAULT_RANGE
    end = last_complete_day(today)
    if key == "mtd":
        start = dt.date(end.year, end.month, 1)
    else:
        length = int(key.rstrip("d"))
        start = end - dt.timedelta(days=length - 1)
    return Window(key, RANGES[key], start, end)


def all_windows(today: dt.date) -> List[Window]:
    return [resolve(key, today) for key in RANGES]


def month_bounds(day: dt.date) -> Tuple[dt.date, dt.date]:
    return dt.date(day.year, day.month, 1), dt.date(
        day.year, day.month, calendar.monthrange(day.year, day.month)[1]
    )
