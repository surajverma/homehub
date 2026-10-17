from datetime import date
from types import SimpleNamespace

from app.recurrence import (
    add_months,
    add_years,
    first_on_or_after,
    next_occurrence,
    occurrences_between,
    rule_interval_unit,
    step,
)


def chore_rule(**kw):
    base = dict(interval=1, unit='day', start_date=None, end_date=None)
    base.update(kw)
    return SimpleNamespace(**base)


def reminder_rule(**kw):
    base = dict(interval=None, unit=None, frequency='daily', start_date=None, end_date=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_add_months_clamps_to_month_end():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 11, 30), 1, day=31) == date(2026, 12, 31)
    assert add_months(date(2026, 11, 30), 3, day=31) == date(2027, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)
    assert add_months(date(2026, 3, 31), 13) == date(2027, 4, 30)


def test_add_years_handles_leap_day():
    assert add_years(date(2028, 2, 29), 1) == date(2029, 2, 28)
    assert add_years(date(2028, 2, 29), 4) == date(2032, 2, 29)
    assert add_years(date(2026, 7, 4), 2) == date(2028, 7, 4)


def test_step_units_and_bad_interval():
    d = date(2026, 10, 4)
    assert step(d, 3, 'day') == date(2026, 10, 7)
    assert step(d, 2, 'week') == date(2026, 10, 18)
    assert step(d, 1, 'month') == date(2026, 11, 4)
    assert step(d, 1, 'year') == date(2027, 10, 4)
    assert step(d, 0, 'day') == date(2026, 10, 5)
    assert step(d, 1, 'fortnight') == date(2026, 10, 5)


def test_rule_interval_unit_for_chores_and_reminders():
    assert rule_interval_unit(chore_rule(interval=2, unit='Week')) == (2, 'week')
    assert rule_interval_unit(chore_rule(interval=None, unit=None)) == (1, 'day')
    assert rule_interval_unit(reminder_rule(interval=3, unit='month')) == (3, 'month')
    assert rule_interval_unit(reminder_rule(frequency='daily')) == (1, 'day')
    assert rule_interval_unit(reminder_rule(frequency='weekly', interval=5)) == (1, 'week')
    assert rule_interval_unit(reminder_rule(frequency='monthly')) == (1, 'month')
    assert rule_interval_unit(reminder_rule(frequency=None)) == (1, 'month')


def test_next_occurrence_counts_from_start_date():
    rule = chore_rule(unit='month', start_date=date(2026, 1, 31))
    d = next_occurrence(rule, date(2026, 1, 31))
    assert d == date(2026, 2, 28)
    # The clamped February date doesn't carry over into March
    assert next_occurrence(rule, d) == date(2026, 3, 31)
    assert next_occurrence(rule, date(2026, 3, 31)) == date(2026, 4, 30)
    # A due date that drifted under the old behaviour moves on to the next real date
    assert next_occurrence(rule, date(2026, 3, 28)) == date(2026, 3, 31)


def test_next_occurrence_without_start_date_steps_from_given_date():
    rule = chore_rule(unit='month')
    assert next_occurrence(rule, date(2026, 1, 31)) == date(2026, 2, 28)
    assert next_occurrence(chore_rule(interval=3), date(2026, 10, 4)) == date(2026, 10, 7)


def test_month_end_rules_return_to_the_31st():
    reminder = reminder_rule(unit='month', interval=1, start_date=date(2026, 10, 31))
    got = list(occurrences_between(reminder, date(2026, 10, 1), date(2027, 4, 30)))
    assert got == [date(2026, 10, 31), date(2026, 11, 30), date(2026, 12, 31), date(2027, 1, 31),
                   date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30)]
    chore = chore_rule(unit='month', start_date=date(2026, 8, 31))
    assert first_on_or_after(chore, date(2026, 10, 1)) == date(2026, 10, 31)
    every_two = chore_rule(unit='month', interval=2, start_date=date(2026, 12, 31))
    got = list(occurrences_between(every_two, date(2026, 12, 1), date(2027, 8, 31)))
    assert got == [date(2026, 12, 31), date(2027, 2, 28), date(2027, 4, 30), date(2027, 6, 30), date(2027, 8, 31)]


def test_leap_day_rule_returns_to_feb_29():
    rule = reminder_rule(unit='year', interval=1, start_date=date(2028, 2, 29))
    got = list(occurrences_between(rule, date(2028, 1, 1), date(2032, 12, 31)))
    assert got == [date(2028, 2, 29), date(2029, 2, 28), date(2030, 2, 28), date(2031, 2, 28), date(2032, 2, 29)]


def test_anchor_day_returns_a_shortened_start_to_its_day():
    # A rule continued from an edit that starts on Nov 30 but belongs to the 31st
    rule = reminder_rule(unit='month', interval=1, start_date=date(2026, 11, 30), anchor_day=31)
    got = list(occurrences_between(rule, date(2026, 11, 1), date(2027, 3, 31)))
    assert got == [date(2026, 11, 30), date(2026, 12, 31), date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31)]
    assert next_occurrence(rule, date(2026, 11, 30)) == date(2026, 12, 31)
    leap = reminder_rule(unit='year', interval=1, start_date=date(2029, 2, 28), anchor_day=29)
    assert first_on_or_after(leap, date(2031, 3, 1)) == date(2032, 2, 29)
    # Ignored for day and week rules, and when it isn't past the start date's own day
    weekly = chore_rule(unit='week', start_date=date(2026, 11, 30), anchor_day=31)
    assert next_occurrence(weekly, date(2026, 11, 30)) == date(2026, 12, 7)
    smaller = reminder_rule(unit='month', interval=1, start_date=date(2026, 11, 30), anchor_day=15)
    assert next_occurrence(smaller, date(2026, 11, 30)) == date(2026, 12, 30)


def test_first_on_or_after_long_running_rules():
    daily = chore_rule(start_date=date(2016, 1, 1))
    assert first_on_or_after(daily, date(2026, 10, 17)) == date(2026, 10, 17)
    weekly = chore_rule(unit='week', interval=2, start_date=date(2026, 1, 1))
    assert first_on_or_after(weekly, date(2026, 10, 17)) == date(2026, 10, 22)
    monthly = chore_rule(unit='month', interval=3, start_date=date(2020, 1, 31))
    assert first_on_or_after(monthly, date(2026, 10, 17)) == date(2026, 10, 31)
    assert first_on_or_after(monthly, date(2026, 11, 1)) == date(2027, 1, 31)
    yearly = chore_rule(unit='year', interval=1, start_date=date(2020, 3, 15))
    assert first_on_or_after(yearly, date(2026, 3, 15)) == date(2026, 3, 15)
    assert first_on_or_after(yearly, date(2026, 3, 16)) == date(2027, 3, 15)


def test_first_on_or_after():
    rule = chore_rule(interval=3, start_date=date(2026, 10, 1))
    assert first_on_or_after(rule, date(2026, 9, 1)) == date(2026, 10, 1)
    assert first_on_or_after(rule, date(2026, 10, 5)) == date(2026, 10, 7)
    assert first_on_or_after(rule, date(2026, 10, 7)) == date(2026, 10, 7)
    ended = chore_rule(interval=3, start_date=date(2026, 10, 1), end_date=date(2026, 10, 6))
    assert first_on_or_after(ended, date(2026, 10, 5)) is None
    no_start = chore_rule()
    assert first_on_or_after(no_start, date(2026, 10, 4)) == date(2026, 10, 4)


def test_occurrences_between_respects_window_and_end_date():
    rule = reminder_rule(unit='week', interval=1, start_date=date(2026, 9, 1), end_date=date(2026, 10, 20))
    got = list(occurrences_between(rule, date(2026, 10, 1), date(2026, 10, 31)))
    assert got == [date(2026, 10, 6), date(2026, 10, 13), date(2026, 10, 20)]
    later = reminder_rule(frequency='weekly', start_date=date(2026, 11, 1))
    assert list(occurrences_between(later, date(2026, 10, 1), date(2026, 10, 31))) == []
