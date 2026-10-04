import json
from datetime import date

import pytest

from app import create_app, db
from app.blueprints.expenses import _build_month_payload, _compute_balances
from app.models import ExpenseEntry, RecurringExpense


def make_app():
    test_config = {
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite://',
        'HOMEHUB_CONFIG': {
            'admin_name': 'Administrator',
            'family_members': ['Alice', 'Bob', 'Carol'],
        },
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'test',
    }
    app = create_app(test_config)
    with app.app_context():
        db.create_all()
        db.session.execute(db.text("CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY, value TEXT)"))
        db.session.commit()
    return app


@pytest.fixture()
def client():
    app = make_app()
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['authed'] = True
    return c


def add_newspaper_rule(start=date(2026, 6, 1), end=date(2026, 6, 10)):
    rule = RecurringExpense(
        title='Newspaper',
        unit_price=5.0,
        default_quantity=1.0,
        frequency='daily',
        monthly_mode='day_of_month',
        start_date=start,
        end_date=end,
        effective_from=start,
        creator='Alice',
    )
    db.session.add(rule)
    db.session.commit()
    return rule.id


def entry_on(rid, d):
    return ExpenseEntry.query.filter_by(recurring_id=rid, date=d).first()


def edit_rule(client, rid, **overrides):
    data = {
        'user': 'Alice',
        'title': 'Newspaper',
        'category': '',
        'unit_price': '5',
        'default_quantity': '1',
        'frequency': 'daily',
        'monthly_mode': 'day_of_month',
        'start_date': '2026-06-01',
        'end_date': '2026-06-10',
        'edit_strategy': 'apply_from',
        'effective_from': '2026-06-01',
    }
    data.update(overrides)
    return client.post(f'/expenses/recurring/edit/{rid}', data=data)


def test_skip_and_restore_recurring_day(client):
    with client.application.app_context():
        rid = add_newspaper_rule()
    client.get('/api/expenses/month?year=2026&month=6')

    with client.application.app_context():
        eid = entry_on(rid, date(2026, 6, 3)).id
    client.post(f'/expenses/skip/{eid}', data={'user': 'Alice'})

    with client.application.app_context():
        assert entry_on(rid, date(2026, 6, 3)).skipped is True
        payload = _build_month_payload(2026, 6)
    assert payload['summary']['total_this_month'] == 45.0
    assert payload['by_date']['2026-06-03']['total'] == 0
    assert payload['by_date']['2026-06-03']['entries'][0]['skipped'] is True

    client.post(f'/expenses/skip/{eid}', data={'user': 'Alice'})
    with client.application.app_context():
        assert entry_on(rid, date(2026, 6, 3)).skipped is False
        assert _build_month_payload(2026, 6)['summary']['total_this_month'] == 50.0


def test_skip_requires_payer_or_admin(client):
    with client.application.app_context():
        rid = add_newspaper_rule()
    client.get('/api/expenses/month?year=2026&month=6')
    with client.application.app_context():
        eid = entry_on(rid, date(2026, 6, 3)).id
    client.post(f'/expenses/skip/{eid}', data={'user': 'Bob'})
    with client.application.app_context():
        assert not entry_on(rid, date(2026, 6, 3)).skipped


def test_skipped_days_survive_apply_from_edit(client):
    with client.application.app_context():
        rid = add_newspaper_rule()
    client.get('/api/expenses/month?year=2026&month=6')
    with client.application.app_context():
        eid = entry_on(rid, date(2026, 6, 5)).id
    client.post(f'/expenses/skip/{eid}', data={'user': 'Alice'})

    edit_rule(client, rid, unit_price='6', effective_from='2026-06-02')

    with client.application.app_context():
        skipped = entry_on(rid, date(2026, 6, 5))
        normal = entry_on(rid, date(2026, 6, 6))
        assert skipped.skipped is True
        assert skipped.unit_price == 6.0
        assert not normal.skipped


def test_skipped_days_survive_split_rule(client):
    with client.application.app_context():
        rid = add_newspaper_rule()
    client.get('/api/expenses/month?year=2026&month=6')
    with client.application.app_context():
        eid = entry_on(rid, date(2026, 6, 8)).id
    client.post(f'/expenses/skip/{eid}', data={'user': 'Alice'})

    edit_rule(client, rid, unit_price='7', edit_strategy='split_rule', effective_from='2026-06-06')

    with client.application.app_context():
        new_rule = RecurringExpense.query.filter(RecurringExpense.id != rid).one()
        moved = entry_on(new_rule.id, date(2026, 6, 8))
        assert moved.skipped is True
        assert moved.unit_price == 7.0


def test_rewrite_all_keeps_skipped_days(client):
    with client.application.app_context():
        rid = add_newspaper_rule()
    client.get('/api/expenses/month?year=2026&month=6')
    with client.application.app_context():
        eid = entry_on(rid, date(2026, 6, 4)).id
    client.post(f'/expenses/skip/{eid}', data={'user': 'Alice'})

    edit_rule(client, rid, unit_price='8', edit_strategy='rewrite_all')

    with client.application.app_context():
        assert entry_on(rid, date(2026, 6, 4)).skipped is True


def test_deleted_recurring_day_can_be_added_back(client):
    with client.application.app_context():
        rid = add_newspaper_rule()
    client.get('/api/expenses/month?year=2026&month=6')
    with client.application.app_context():
        eid = entry_on(rid, date(2026, 6, 4)).id
    client.post(f'/expenses/delete/{eid}', data={'user': 'Alice'})

    with client.application.app_context():
        payload = _build_month_payload(2026, 6)
    assert payload['missing_recurring'] == {'2026-06-04': [{'rule_id': rid, 'title': 'Newspaper', 'creator': 'Alice'}]}

    client.post(f'/expenses/recurring/{rid}/restore', data={'user': 'Alice', 'date': '2026-06-04'})
    with client.application.app_context():
        restored = entry_on(rid, date(2026, 6, 4))
        assert restored is not None and restored.amount == 5.0
        assert _build_month_payload(2026, 6)['missing_recurring'] == {}

    # A second restore must not duplicate the day
    client.post(f'/expenses/recurring/{rid}/restore', data={'user': 'Alice', 'date': '2026-06-04'})
    with client.application.app_context():
        assert ExpenseEntry.query.filter_by(recurring_id=rid, date=date(2026, 6, 4)).count() == 1


def test_restore_rejects_dates_outside_schedule(client):
    with client.application.app_context():
        rule = RecurringExpense(
            title='Cleaner', unit_price=100.0, default_quantity=1.0, frequency='weekly',
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 30), effective_from=date(2026, 6, 1), creator='Alice',
        )
        db.session.add(rule)
        db.session.commit()
        rid = rule.id
    client.get('/api/expenses/month?year=2026&month=6')
    client.post(f'/expenses/recurring/{rid}/restore', data={'user': 'Alice', 'date': '2026-06-02'})
    with client.application.app_context():
        assert entry_on(rid, date(2026, 6, 2)) is None
        assert ExpenseEntry.query.filter_by(recurring_id=rid).count() == 5


def test_balances_equal_split_and_settlement(client):
    client.post('/expenses', data={
        'form_type': 'single', 'title': 'Groceries', 'amount': '90', 'payer': 'Alice',
        'date': '2026-06-01', 'split_with': ['Alice', 'Bob', 'Carol'],
    })
    client.post('/expenses', data={
        'form_type': 'single', 'title': 'Pizza', 'amount': '20', 'payer': 'Bob',
        'date': '2026-06-02', 'split_with': ['Alice', 'Bob'],
    })
    # Personal expense: nothing ticked, so it doesn't affect balances
    client.post('/expenses', data={
        'form_type': 'single', 'title': 'Book', 'amount': '50', 'payer': 'Carol', 'date': '2026-06-02',
    })

    with client.application.app_context():
        b = _compute_balances(2)
    assert b['net'] == {'Alice': 50.0, 'Bob': -20.0, 'Carol': -30.0}
    assert sorted((t['from'], t['to'], t['amount']) for t in b['settlements']) == [
        ('Bob', 'Alice', 20.0), ('Carol', 'Alice', 30.0),
    ]

    client.post('/expenses/settle', data={'user': 'Carol', 'from_member': 'Carol', 'to_member': 'Alice', 'amount': '30'})
    with client.application.app_context():
        b = _compute_balances(2)
        payload = _build_month_payload(date.today().year, date.today().month)
    assert b['net'] == {'Alice': 20.0, 'Bob': -20.0}
    assert b['settlements'] == [{'from': 'Bob', 'to': 'Alice', 'amount': 20.0}]
    # Settlements are not spending
    assert payload['summary']['total_this_month'] == 0


def test_settle_requires_involved_member(client):
    client.post('/expenses/settle', data={'user': 'Bob', 'from_member': 'Carol', 'to_member': 'Alice', 'amount': '30'})
    with client.application.app_context():
        assert ExpenseEntry.query.count() == 0


def test_recurring_split_copies_to_entries_and_skips_excluded(client):
    client.post('/expenses', data={
        'form_type': 'recurring', 'title': 'Milk', 'unit_price': '10', 'default_quantity': '1',
        'frequency': 'daily', 'start_date': '2026-06-01', 'end_date': '2026-06-03',
        'creator': 'Alice', 'split_with': ['Alice', 'Bob'],
    })
    client.get('/api/expenses/month?year=2026&month=6')
    with client.application.app_context():
        rid = RecurringExpense.query.one().id
        entries = ExpenseEntry.query.filter_by(recurring_id=rid).all()
        assert all(json.loads(e.split_with) == ['Alice', 'Bob'] for e in entries)
        eid = entry_on(rid, date(2026, 6, 2)).id
    client.post(f'/expenses/skip/{eid}', data={'user': 'Alice'})
    with client.application.app_context():
        assert _compute_balances(2)['net'] == {'Alice': 10.0, 'Bob': -10.0}


def test_blank_quantity_counts_as_one(client):
    client.post('/expenses', data={
        'form_type': 'single', 'title': 'Bread', 'amount': '', 'unit_price': '12', 'quantity': '',
        'payer': 'Alice', 'date': '2026-06-01',
    })
    with client.application.app_context():
        assert ExpenseEntry.query.one().amount == 12.0


def test_edit_entry_updates_split(client):
    client.post('/expenses', data={
        'form_type': 'single', 'title': 'Taxi', 'amount': '30', 'payer': 'Alice',
        'date': '2026-06-01', 'split_with': ['Alice', 'Bob', 'Carol'],
    })
    with client.application.app_context():
        eid = ExpenseEntry.query.one().id
    client.post(f'/expenses/edit/{eid}', data={'user': 'Alice', 'split_present': '1', 'split_with': ['Alice', 'Bob']})
    with client.application.app_context():
        assert json.loads(ExpenseEntry.query.get(eid).split_with) == ['Alice', 'Bob']
    client.post(f'/expenses/edit/{eid}', data={'user': 'Alice', 'split_present': '1'})
    with client.application.app_context():
        assert ExpenseEntry.query.get(eid).split_with is None


def test_uneven_split_by_shares(client):
    client.post('/expenses', data={
        'form_type': 'single', 'title': 'Rent', 'amount': '100', 'payer': 'Alice', 'date': '2026-06-01',
        'split_with': ['Alice', 'Bob', 'Carol'], 'split_mode': 'shares',
        'split_weight__Alice': '2', 'split_weight__Bob': '1', 'split_weight__Carol': '1',
    })
    with client.application.app_context():
        assert _compute_balances(2)['net'] == {'Alice': 50.0, 'Bob': -25.0, 'Carol': -25.0}
        payload = _build_month_payload(2026, 6)
    entry = payload['by_date']['2026-06-01']['entries'][0]
    assert entry['split_mode'] == 'shares'
    assert entry['shares'] == {'Alice': 50.0, 'Bob': 25.0, 'Carol': 25.0}
    assert payload['summary']['per_payer'] == {'Alice': 100.0}
    assert payload['summary']['per_share'] == {'Alice': 50.0, 'Bob': 25.0, 'Carol': 25.0}


def test_uneven_split_by_percent_and_amount(client):
    client.post('/expenses', data={
        'form_type': 'single', 'title': 'Trip', 'amount': '200', 'payer': 'Bob', 'date': '2026-06-01',
        'split_with': ['Alice', 'Bob'], 'split_mode': 'percent',
        'split_weight__Alice': '75', 'split_weight__Bob': '25',
    })
    with client.application.app_context():
        assert _compute_balances(2)['net'] == {'Alice': -150.0, 'Bob': 150.0}
    # A ticked member with no value takes no part in an uneven split
    client.post('/expenses', data={
        'form_type': 'single', 'title': 'Dinner', 'amount': '90', 'payer': 'Alice', 'date': '2026-06-02',
        'split_with': ['Alice', 'Bob', 'Carol'], 'split_mode': 'amount',
        'split_weight__Alice': '30', 'split_weight__Bob': '60', 'split_weight__Carol': '',
    })
    with client.application.app_context():
        assert _compute_balances(2)['net'] == {'Alice': -90.0, 'Bob': 90.0}
        stored = json.loads(ExpenseEntry.query.filter_by(title='Dinner').one().split_with)
    assert stored == {'mode': 'amount', 'weights': {'Alice': 30.0, 'Bob': 60.0}}


def test_admin_can_share_an_expense(client):
    # The admin is not a family member but can still pay and take a share
    cfg = client.application.config['HOMEHUB_CONFIG']
    admin = cfg['admin_name']
    assert admin not in cfg['family_members']
    client.post('/expenses', data={
        'form_type': 'single', 'title': 'Gift', 'amount': '5000', 'payer': admin, 'date': '2026-06-01',
        'split_with': ['Alice', 'Bob', 'Carol', admin],
    })
    with client.application.app_context():
        assert _compute_balances(2)['net'] == {admin: 3750.0, 'Alice': -1250.0, 'Bob': -1250.0, 'Carol': -1250.0}
    html = client.get('/expenses').get_data(as_text=True)
    assert f'name="split_with" value="{admin}"' in html
    assert f'<option value="{admin}">' in html


def test_recurring_rule_keeps_uneven_split(client):
    # The live config.yml is loaded on the first request, so pick members after one
    client.get('/expenses')
    first, second = client.application.config['HOMEHUB_CONFIG']['family_members'][:2]
    client.post('/expenses', data={
        'form_type': 'recurring', 'title': 'Milk', 'unit_price': '30', 'default_quantity': '1',
        'frequency': 'daily', 'start_date': '2026-06-01', 'end_date': '2026-06-02', 'creator': first,
        'split_with': [first, second], 'split_mode': 'shares',
        f'split_weight__{first}': '1', f'split_weight__{second}': '2',
    })
    client.get('/api/expenses/month?year=2026&month=6')
    with client.application.app_context():
        rule = RecurringExpense.query.one()
        assert rule.split_mode == 'shares' and rule.split_weights == {first: 1.0, second: 2.0}
        assert _compute_balances(2)['net'] == {first: 40.0, second: -40.0}
    html = client.get('/expenses/recurring').get_data(as_text=True)
    assert 'by shares' in html
    assert f'name="split_weight__{second}" value="2.0"' in html


def test_settling_rounded_shares_leaves_no_residue(client):
    # 5000 / 3 does not divide evenly; paying the displayed amounts must clear everything
    client.post('/expenses', data={
        'form_type': 'single', 'title': 'Gift', 'amount': '5000', 'payer': 'Alice', 'date': '2026-06-01',
        'split_with': ['Alice', 'Bob', 'Carol'],
    })
    client.post('/expenses', data={
        'form_type': 'single', 'title': 'Cake', 'amount': '1000', 'payer': 'Alice', 'date': '2026-06-01',
        'split_with': ['Alice', 'Bob', 'Carol'], 'split_mode': 'shares',
        'split_weight__Alice': '3', 'split_weight__Bob': '1', 'split_weight__Carol': '2',
    })
    with client.application.app_context():
        b = _compute_balances(2)
        shares = _build_month_payload(2026, 6)['by_date']['2026-06-01']['entries'][0]['shares']
    assert shares == {'Alice': 1666.66, 'Bob': 1666.67, 'Carol': 1666.67}
    for t in b['settlements']:
        client.post('/expenses/settle', data={'user': t['from'], 'from_member': t['from'], 'to_member': t['to'], 'amount': str(t['amount'])})
    with client.application.app_context():
        assert _compute_balances(2) == {'net': {}, 'settlements': []}


def test_skip_rejects_entries_without_a_rule(client):
    with client.application.app_context():
        db.session.add(ExpenseEntry(date=date(2026, 6, 1), title='Lunch', amount=40.0, payer='Alice'))
        db.session.commit()
        eid = ExpenseEntry.query.filter_by(title='Lunch').one().id
    client.post(f'/expenses/skip/{eid}', data={'user': 'Alice'})
    with client.application.app_context():
        assert not db.session.get(ExpenseEntry, eid).skipped


@pytest.mark.parametrize('amount', ['nan', 'inf', '-5', '0'])
def test_settle_rejects_non_positive_or_non_finite_amounts(client, amount):
    client.post('/expenses/settle', data={'user': 'Bob', 'from_member': 'Bob', 'to_member': 'Alice', 'amount': amount})
    with client.application.app_context():
        assert ExpenseEntry.query.count() == 0


@pytest.mark.parametrize('mode, weights', [
    ('percent', {'Alice': '60', 'Bob': '60'}),
    ('amount', {'Alice': '30', 'Bob': '30'}),
    ('shares', {'Alice': '', 'Bob': ''}),
    ('shares', {'Alice': 'nan', 'Bob': '1'}),
])
def test_invalid_uneven_split_is_rejected(client, mode, weights):
    data = {
        'form_type': 'single', 'title': 'Trip', 'amount': '90', 'payer': 'Alice', 'date': '2026-06-01',
        'split_with': ['Alice', 'Bob'], 'split_mode': mode,
    }
    data.update({f'split_weight__{k}': v for k, v in weights.items()})
    client.post('/expenses', data=data)
    with client.application.app_context():
        assert ExpenseEntry.query.count() == 0


def test_invalid_split_leaves_edited_entry_unchanged(client):
    with client.application.app_context():
        db.session.add(ExpenseEntry(date=date(2026, 6, 1), title='Trip', amount=90.0, payer='Alice', split_with=json.dumps(['Alice', 'Bob'])))
        db.session.commit()
        eid = ExpenseEntry.query.one().id
    client.post(f'/expenses/edit/{eid}', data={
        'user': 'Alice', 'title': 'Changed', 'amount': '90', 'split_present': '1',
        'split_with': ['Alice', 'Bob'], 'split_mode': 'percent',
        'split_weight__Alice': '10', 'split_weight__Bob': '10',
    })
    with client.application.app_context():
        e = db.session.get(ExpenseEntry, eid)
        assert e.title == 'Trip'
        assert json.loads(e.split_with) == ['Alice', 'Bob']


def test_rule_edit_form_keeps_former_members(client):
    # Dave left family_members but is still part of the stored split
    with client.application.app_context():
        rid = add_newspaper_rule()
        rule = db.session.get(RecurringExpense, rid)
        rule.split_with = json.dumps({'mode': 'shares', 'weights': {'Alice': 1, 'Dave': 2}})
        db.session.commit()
    html = client.get('/expenses/recurring').get_data(as_text=True)
    assert 'name="split_weight__Dave" value="2' in html
    assert 'value="Dave" class="rounded split-member" checked' in html


@pytest.mark.parametrize('field', ['amount', 'unit_price', 'quantity'])
def test_non_finite_expense_is_rejected(client, field):
    data = {'form_type': 'single', 'title': 'Bad', 'amount': '10', 'payer': 'Alice', 'date': '2026-06-01'}
    data[field] = 'nan' if field != 'quantity' else 'inf'
    if field != 'amount':
        data['unit_price'] = data.get('unit_price', '5')
    client.post('/expenses', data=data)
    with client.application.app_context():
        assert ExpenseEntry.query.count() == 0


def test_non_finite_recurring_rule_is_rejected(client):
    client.post('/expenses', data={
        'form_type': 'recurring', 'title': 'Milk', 'unit_price': 'inf', 'default_quantity': '1',
        'frequency': 'daily', 'start_date': '2026-06-01', 'end_date': '2026-06-03', 'creator': 'Alice',
    })
    with client.application.app_context():
        assert RecurringExpense.query.count() == 0


def test_non_finite_edit_leaves_entry_unchanged(client):
    with client.application.app_context():
        db.session.add(ExpenseEntry(date=date(2026, 6, 1), title='Trip', amount=90.0, payer='Alice'))
        db.session.commit()
        eid = ExpenseEntry.query.one().id
    client.post(f'/expenses/edit/{eid}', data={'user': 'Alice', 'title': 'Changed', 'amount': 'nan'})
    with client.application.app_context():
        e = db.session.get(ExpenseEntry, eid)
        assert e.title == 'Trip' and e.amount == 90.0


def test_restore_rejects_dates_before_last_edit(client):
    with client.application.app_context():
        rid = add_newspaper_rule()
    client.get('/api/expenses/month?year=2026&month=6')
    with client.application.app_context():
        eid = entry_on(rid, date(2026, 6, 2)).id
    client.post(f'/expenses/delete/{eid}', data={'user': 'Alice'})
    edit_rule(client, rid, unit_price='8', effective_from='2026-06-05')

    client.post(f'/expenses/recurring/{rid}/restore', data={'user': 'Alice', 'date': '2026-06-02'})
    with client.application.app_context():
        assert entry_on(rid, date(2026, 6, 2)) is None
