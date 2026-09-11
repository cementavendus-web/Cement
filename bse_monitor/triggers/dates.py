"""Calendar arithmetic for statutory deadlines.

Deliberately dependency-free. The obvious shortcut — ``timedelta(days=182)`` for
"six months" — is wrong for a statutory deadline and is never used here: SEBI
lock-ins run in calendar months, so six months from 31 August is the last day of
February, not 1 March.

``add_months`` is the most failure-prone function in the calendar layer, so it is
isolated here with its own tests rather than inlined into the rule engine.
"""

from __future__ import annotations

import calendar as _stdlib_calendar
import datetime as dt

__all__ = ["add_days", "add_months", "add_years", "add_hours", "days_between"]


def add_days(day: dt.date, n: int) -> dt.date:
    return day + dt.timedelta(days=n)


def add_months(day: dt.date, n: int) -> dt.date:
    """Add ``n`` calendar months, clamping to the target month's last day.

    ``31 Aug + 6m`` -> ``28 Feb`` (or ``29 Feb`` in a leap year), ``31 Mar + 1m``
    -> ``30 Apr``, ``29 Feb + 12m`` -> ``28 Feb``.
    """
    total = (day.year * 12 + (day.month - 1)) + n
    year, month = divmod(total, 12)
    month += 1
    last_day = _stdlib_calendar.monthrange(year, month)[1]
    return dt.date(year, month, min(day.day, last_day))


def add_years(day: dt.date, n: int) -> dt.date:
    return add_months(day, 12 * n)


def add_hours(moment: dt.datetime, n: int) -> dt.datetime:
    return moment + dt.timedelta(hours=n)


def days_between(start: dt.date, end: dt.date) -> int:
    return (end - start).days
