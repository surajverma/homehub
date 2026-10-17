"""Shared date math for recurring chores and reminders.

Both kinds of rule repeat every ``interval`` days, weeks, months or years.
Month and year steps clamp to the last valid day of the target month, so a
rule that falls on the 31st lands on the 30th (or Feb 28/29) in shorter months.
Every date is counted from the rule's start date rather than from the previous
date, so a clamped month doesn't carry over and long months return to the 31st.
"""
import functools
import threading
from datetime import date, timedelta

UNITS = ('day', 'week', 'month', 'year')

# Older reminder rules only stored a frequency string.
_LEGACY_FREQUENCY_UNITS = {'daily': 'day', 'weekly': 'week'}


def add_months(dt: date, months: int, day: int | None = None) -> date:
    """``dt`` moved by ``months``, on ``day`` (default: ``dt``'s own day) or the month's last day."""
    y = dt.year + (dt.month - 1 + months) // 12
    m = (dt.month - 1 + months) % 12 + 1
    last = (date(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(day or dt.day, last))


def add_years(dt: date, years: int) -> date:
    try:
        return date(dt.year + years, dt.month, dt.day)
    except ValueError:
        # Feb 29 in a non-leap year
        return date(dt.year + years, 2, 28)


def step(d: date, interval: int, unit: str) -> date:
    """Return the date one ``interval`` of ``unit`` after ``d``."""
    interval = max(1, int(interval or 1))
    unit = (unit or 'day').lower()
    if unit == 'week':
        return d + timedelta(weeks=interval)
    if unit == 'month':
        return add_months(d, interval)
    if unit == 'year':
        return add_years(d, interval)
    return d + timedelta(days=interval)


def rule_interval_unit(rule) -> tuple[int, str]:
    """Read (interval, unit) from a chore or reminder rule.

    Reminder rules without a unit fall back to their legacy ``frequency``: daily,
    weekly, and anything else as monthly, always with an interval of 1. Chore
    rules have no frequency and default to daily.
    """
    unit = (getattr(rule, 'unit', None) or '').lower()
    if unit:
        return max(1, int(getattr(rule, 'interval', None) or 1)), unit
    if not hasattr(rule, 'frequency'):
        return max(1, int(getattr(rule, 'interval', None) or 1)), 'day'
    frequency = (rule.frequency or '').lower()
    return 1, _LEGACY_FREQUENCY_UNITS.get(frequency, 'month')


def _anchor_day(rule, origin: date) -> int | None:
    """The day a month or year rule aims for when its start date is a shortened month end."""
    day = getattr(rule, 'anchor_day', None)
    return day if day and day > origin.day else None


def _nth_date(origin: date, n: int, interval: int, unit: str, anchor_day: int | None = None) -> date:
    """The ``n``-th scheduled date counted from ``origin`` (the 0th is ``origin`` itself)."""
    if n == 0:
        return origin
    if unit == 'month':
        return add_months(origin, n * interval, anchor_day)
    if unit == 'year':
        if anchor_day:
            return add_months(origin, 12 * n * interval, anchor_day)
        return add_years(origin, n * interval)
    if unit == 'week':
        return origin + timedelta(weeks=n * interval)
    return origin + timedelta(days=n * interval)


def _first_on_or_after_from(origin: date, target: date, interval: int, unit: str,
                            anchor_day: int | None = None) -> date:
    """First date of the series starting at ``origin`` that is on or after ``target``."""
    if target <= origin:
        return origin
    if unit in ('month', 'year'):
        months = interval * (12 if unit == 'year' else 1)
        # Start from a count that can't overshoot, then walk forward
        n = max(0, ((target.year - origin.year) * 12 + target.month - origin.month) // months - 1)
    else:
        days = interval * (7 if unit == 'week' else 1)
        n = (target - origin).days // days
    d = _nth_date(origin, n, interval, unit, anchor_day)
    while d < target:
        n += 1
        d = _nth_date(origin, n, interval, unit, anchor_day)
    return d


def next_occurrence(rule, d: date) -> date:
    """The rule's first scheduled date after ``d``, counted from its start date."""
    interval, unit = rule_interval_unit(rule)
    unit = unit if unit in UNITS else 'day'
    origin = getattr(rule, 'start_date', None) or d
    return _first_on_or_after_from(origin, d + timedelta(days=1), interval, unit, _anchor_day(rule, origin))


def first_on_or_after(rule, target: date) -> date | None:
    """First scheduled date of ``rule`` on or after ``target``, or None once it has ended."""
    interval, unit = rule_interval_unit(rule)
    unit = unit if unit in UNITS else 'day'
    origin = rule.start_date or target
    d = _first_on_or_after_from(origin, target, interval, unit, _anchor_day(rule, origin))
    end = getattr(rule, 'end_date', None)
    if end and d > end:
        return None
    return d


def occurrences_between(rule, start: date, end: date):
    """Yield every scheduled date of ``rule`` in [start, end], respecting its end date."""
    d = first_on_or_after(rule, start)
    rule_end = getattr(rule, 'end_date', None)
    while d is not None and d <= end and (not rule_end or d <= rule_end):
        yield d
        d = next_occurrence(rule, d)


# Generating due chores and recurring expenses is check-then-insert. Gunicorn runs one
# worker with several threads, so a process-wide lock is enough to stop two requests
# from both creating the same occurrence.
_generation_lock = threading.RLock()


def serialized(fn):
    """Run ``fn`` (including its commit) while holding the shared generation lock."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _generation_lock:
            return fn(*args, **kwargs)
    return wrapper
