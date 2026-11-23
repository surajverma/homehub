"""Issue #87, for recurring expenses: a rule split from a date keeps the old rule's day.

Splitting a monthly rule on the 31st in a shorter month used to start the new rule on the 30th, and every
later month stayed on the 30th. A split now carries on the old schedule (with anchor_day, as recurring
reminders do), unless the edit gives the new rule a later start date of its own.
"""
from datetime import date

from app import db
from app.models import RecurringExpense

from test_undo_and_previews import client, edit_form, make_rule  # noqa: F401 (client is a fixture)


def billed(client, title):
    from app.models import ExpenseEntry
    with client.application.app_context():
        rows = ExpenseEntry.query.filter_by(title=title).order_by(ExpenseEntry.date).all()
        return [e.date.isoformat() for e in rows]


def split(client, rid, start, frequency='monthly', **overrides):
    form = edit_form(frequency=frequency, start_date=start.isoformat(), edit_strategy='split_rule',
                     title='Rent', **overrides)
    return client.post(f'/expenses/recurring/edit/{rid}', data=form)


def new_rule(client):
    with client.application.app_context():
        rule = RecurringExpense.query.filter_by(title='Rent').one()
        return rule.start_date, rule.anchor_day


def test_split_on_a_shortened_month_end_keeps_the_31st(client):
    rid = make_rule(client, 'monthly', start=date(2026, 5, 31))
    split(client, rid, date(2026, 5, 31), effective_from='2026-09-30')
    assert billed(client, 'Milk') == ['2026-05-31', '2026-06-30', '2026-07-31', '2026-08-31']
    assert billed(client, 'Rent')[:2] == ['2026-09-30', '2026-10-31']
    assert new_rule(client) == (date(2026, 9, 30), 31)


def test_split_mid_month_starts_on_the_rules_own_day(client):
    rid = make_rule(client, 'monthly', start=date(2026, 5, 31))
    split(client, rid, date(2026, 5, 31), effective_from='2026-09-15')
    assert billed(client, 'Milk')[-1] == '2026-08-31'
    assert billed(client, 'Rent')[:2] == ['2026-09-30', '2026-10-31']
    assert new_rule(client) == (date(2026, 9, 30), 31)


def test_split_of_a_split_rule_still_aims_for_the_31st(client):
    rid = make_rule(client, 'monthly', start=date(2026, 5, 31))
    split(client, rid, date(2026, 5, 31), effective_from='2026-06-30')
    first_split, _anchor = new_rule(client)
    with client.application.app_context():
        first_id = RecurringExpense.query.filter_by(title='Rent').one().id
    # The form shows the split rule's own start date, Jun 30
    split(client, first_id, first_split, effective_from='2026-09-10', unit_price='12')
    with client.application.app_context():
        rules = RecurringExpense.query.filter_by(title='Rent').order_by(RecurringExpense.id).all()
        assert [(r.start_date, r.end_date, r.anchor_day) for r in rules] == [
            (date(2026, 6, 30), date(2026, 9, 9), 31), (date(2026, 9, 30), None, 31)]
        from app.models import ExpenseEntry
        dates = [e.date.isoformat() for e in ExpenseEntry.query.filter_by(recurring_id=rules[1].id)
                 .order_by(ExpenseEntry.date)]
    assert billed(client, 'Rent')[:5] == ['2026-06-30', '2026-07-31', '2026-08-31', '2026-09-30', '2026-10-31']
    assert dates[:2] == ['2026-09-30', '2026-10-31']


def test_split_weekly_rule_keeps_its_weekday(client):
    monday = date(2026, 10, 5)
    rid = make_rule(client, 'weekly', start=monday)
    split(client, rid, monday, frequency='weekly', effective_from='2026-10-21')
    assert billed(client, 'Milk') == ['2026-10-05', '2026-10-12', '2026-10-19']
    assert billed(client, 'Rent')[:2] == ['2026-10-26', '2026-11-02']
    assert new_rule(client) == (date(2026, 10, 26), None)


def test_split_with_a_later_start_date_uses_that_day(client):
    rid = make_rule(client, 'monthly', start=date(2026, 5, 31))
    split(client, rid, date(2026, 9, 20), effective_from='2026-09-15')
    assert billed(client, 'Rent')[:2] == ['2026-09-20', '2026-10-20']
    assert new_rule(client) == (date(2026, 9, 20), None)


def test_split_preview_names_the_new_rules_first_date(client):
    rid = make_rule(client, 'monthly', start=date(2026, 5, 31))
    form = edit_form(frequency='monthly', start_date='2026-05-31', edit_strategy='split_rule', title='Rent',
                     effective_from='2026-09-15')
    preview = client.post(f'/expenses/recurring/edit/{rid}/preview', data=form).get_json()
    assert preview['new_start'] == '2026-09-30'
    assert any('2026-09-30' in line for line in preview['details'])


def test_new_start_date_on_apply_from_drops_the_kept_day(client):
    rid = make_rule(client, 'monthly', start=date(2026, 5, 31))
    split(client, rid, date(2026, 5, 31), effective_from='2026-09-30')
    with client.application.app_context():
        split_id = RecurringExpense.query.filter_by(title='Rent').one().id
    client.post(f'/expenses/recurring/edit/{split_id}', data=edit_form(
        frequency='monthly', start_date='2026-09-20', edit_strategy='apply_from', title='Rent',
        effective_from='2026-09-20'))
    with client.application.app_context():
        assert db.session.get(RecurringExpense, split_id).anchor_day is None
    assert billed(client, 'Rent')[:2] == ['2026-09-20', '2026-10-20']
