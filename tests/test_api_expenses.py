"""API: the expense tracker."""
from datetime import date, timedelta

import pytest

from api_support import Home
from app.models import ExpenseEntry, RecurringExpense, db

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    return Home(tmp_path, monkeypatch)


@pytest.fixture()
def agent(home):
    return home.all_tools('Hermes')


def add_rule(home, title='Milk', creator='Dad', price=60.0, qty=2.0, start=None, **kw):
    with home.ctx():
        rule = RecurringExpense(title=title, unit_price=price, default_quantity=qty, frequency='daily',
                                start_date=start or TODAY - timedelta(days=5), creator=creator, **kw)
        db.session.add(rule)
        db.session.commit()
        return rule.id


def entry(home, rule_id, d):
    with home.ctx():
        return ExpenseEntry.query.filter_by(recurring_id=rule_id, date=d).first()


def set_categories(home, text):
    with home.ctx():
        db.session.execute(db.text("CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY, value TEXT)"))
        db.session.execute(db.text("INSERT INTO app_setting(key, value) VALUES('categories', :v)"), {'v': text})
        db.session.commit()


def test_rules_and_entries(home, agent):
    rid = add_rule(home)
    add_rule(home, title='Old paper', start=TODAY - timedelta(days=30), end_date=TODAY - timedelta(days=10))
    data = home.get('/expenses/rules', agent).get_json()
    assert data['currency'] == '₹'
    assert [r['title'] for r in data['rules']] == ['Milk']
    assert data['rules'][0] == {'id': rid, 'title': 'Milk', 'unit_price': 60.0, 'usual_quantity': 2.0, 'frequency': 'daily',
                                'category': None, 'start_date': (TODAY - timedelta(days=5)).isoformat(), 'end_date': None,
                                'creator': 'Dad'}
    # Entries were generated up to today, as the expenses page does
    today = home.get('/expenses', agent).get_json()['entries']
    milk = [e for e in today if e['recurring_id'] == rid]
    assert len(milk) == 1 and milk[0]['amount'] == 120.0 and milk[0]['payer'] == 'Dad' and milk[0]['created_by'] == 'Dad'
    rng = home.get(f'/expenses?from={(TODAY - timedelta(days=5)).isoformat()}&to={TODAY.isoformat()}', agent).get_json()
    assert len([e for e in rng['entries'] if e['recurring_id'] == rid]) == 6
    assert home.get('/expenses?from=2026-01-01&to=2026-03-04', agent).status_code == 400
    assert home.get(f'/expenses?date={TODAY.isoformat()}&from=2026-01-01&to=2026-01-02', agent).status_code == 400


def test_skip_and_restore(home, agent):
    rid = add_rule(home)
    path = f'/expenses/rules/{rid}/days/{YESTERDAY.isoformat()}/skipped'
    resp = home.put(path, agent, json={'skipped': True, 'by': 'Dad'})
    assert resp.status_code == 200
    assert resp.get_json()['skipped'] is True and resp.get_json()['summary'].startswith('Milk skipped on')
    # Repeating it keeps it skipped (it never toggles back)
    assert home.put(path, agent, json={'skipped': True, 'by': 'Dad'}).get_json()['skipped'] is True
    assert entry(home, rid, YESTERDAY).skipped is True
    assert home.put(path, agent, json={'skipped': False, 'by': 'Dad'}).get_json()['skipped'] is False
    assert entry(home, rid, YESTERDAY).skipped is False


def test_ownership(home, agent):
    rid = add_rule(home, creator='Dad')
    path = f'/expenses/rules/{rid}/days/{YESTERDAY.isoformat()}'
    resp = home.put(path + '/skipped', agent, json={'skipped': True, 'by': 'Priya'})
    assert resp.status_code == 403 and 'Priya' in resp.get_json()['message']
    assert home.patch(path, agent, json={'quantity': 3, 'by': 'Priya'}).status_code == 403
    # Without "by" the token acts as itself, which owns nothing here
    assert home.patch(path, agent, json={'quantity': 3}).status_code == 403
    assert entry(home, rid, YESTERDAY).skipped is False


def test_quantity_recalculates_amount(home, agent):
    rid = add_rule(home, price=60.0, qty=2.0)
    path = f'/expenses/rules/{rid}/days/{YESTERDAY.isoformat()}'
    resp = home.patch(path, agent, json={'quantity': 2.5, 'by': 'Dad'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['quantity'] == 2.5 and data['amount'] == 150.0
    assert data['summary'].endswith(': quantity 2.5, ₹150')
    with home.ctx():
        e = ExpenseEntry.query.filter_by(recurring_id=rid, date=YESTERDAY).one()
        assert (e.quantity, e.amount) == (2.5, 150.0)


@pytest.mark.parametrize('quantity', [0, -1, 'two', None, True, 1e12])
def test_invalid_quantity(home, agent, quantity):
    rid = add_rule(home)
    home.get('/expenses', agent)  # generate the recurring days
    resp = home.patch(f'/expenses/rules/{rid}/days/{YESTERDAY.isoformat()}', agent, json={'quantity': quantity, 'by': 'Dad'})
    assert resp.status_code == 400
    assert entry(home, rid, YESTERDAY).quantity == 2.0


def test_nan_is_rejected(home, agent):
    rid = add_rule(home)
    resp = home.call('PATCH', f'/expenses/rules/{rid}/days/{YESTERDAY.isoformat()}', agent,
                     data='{"quantity": NaN, "by": "Dad"}', headers={'Content-Type': 'application/json'})
    assert resp.status_code == 400


def test_future_days_are_rejected(home, agent):
    rid = add_rule(home)
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    assert home.put(f'/expenses/rules/{rid}/days/{tomorrow}/skipped', agent, json={'skipped': True, 'by': 'Dad'}).status_code == 400
    assert home.post('/expenses', agent, json={'title': 'X', 'amount': 5, 'date': tomorrow, 'by': 'Dad'}).status_code == 400


def test_deleted_day_is_a_conflict(home, agent):
    rid = add_rule(home)
    home.get('/expenses', agent)  # generate
    with home.ctx():
        db.session.delete(ExpenseEntry.query.filter_by(recurring_id=rid, date=YESTERDAY).one())
        db.session.commit()
    resp = home.put(f'/expenses/rules/{rid}/days/{YESTERDAY.isoformat()}/skipped', agent, json={'skipped': True, 'by': 'Dad'})
    assert resp.status_code == 409 and resp.get_json()['error'] == 'conflict'
    assert 'expenses page' in resp.get_json()['message']
    # Still deleted: the API never brings it back by itself
    assert entry(home, rid, YESTERDAY) is None


def test_day_outside_the_rule(home, agent):
    rid = add_rule(home, start=YESTERDAY)
    before = (YESTERDAY - timedelta(days=3)).isoformat()
    assert home.put(f'/expenses/rules/{rid}/days/{before}/skipped', agent, json={'skipped': True, 'by': 'Dad'}).status_code == 404
    assert home.put(f'/expenses/rules/999/days/{YESTERDAY.isoformat()}/skipped', agent, json={'skipped': True}).status_code == 404


def test_settlements_cannot_be_skipped(home, agent):
    # A settlement never belongs to a rule; even one that somehow did is refused, as on the page
    rid = add_rule(home)
    home.get('/expenses', agent)
    with home.ctx():
        e = ExpenseEntry.query.filter_by(recurring_id=rid, date=YESTERDAY).one()
        e.is_settlement = True
        db.session.commit()
    resp = home.put(f'/expenses/rules/{rid}/days/{YESTERDAY.isoformat()}/skipped', agent, json={'skipped': True, 'by': 'Dad'})
    assert resp.status_code == 400 and 'Only recurring days' in resp.get_json()['message']


def test_add_one_off(home, agent):
    set_categories(home, 'Groceries, Transport')
    resp = home.post('/expenses', agent, json={'title': 'Vegetables', 'unit_price': 40, 'quantity': 2.5,
                                               'category': 'groceries', 'by': 'Priya'})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data['amount'] == 100.0 and data['payer'] == 'Priya' and data['created_by'] == 'Priya'
    assert data['category'] == 'Groceries' and data['date'] == TODAY.isoformat() and data['recurring_id'] is None
    resp = home.post('/expenses', agent, json={'title': 'Taxi', 'amount': 250, 'payer': 'dad', 'date': YESTERDAY.isoformat(), 'by': 'Priya'})
    assert resp.get_json()['payer'] == 'Dad' and resp.get_json()['created_by'] == 'Priya'
    # It shows on the expenses page's own month data
    month = home.client.get(f'/api/expenses/month?year={TODAY.year}&month={TODAY.month}').get_json()
    titles = [e['title'] for day in month['by_date'].values() for e in day['entries']]
    assert 'Vegetables' in titles


@pytest.mark.parametrize('body', [
    {'title': 'X', 'by': 'Priya'},
    {'title': 'X', 'amount': 5, 'unit_price': 5, 'quantity': 1, 'by': 'Priya'},
    {'title': 'X', 'unit_price': 5, 'by': 'Priya'},
    {'title': 'X', 'amount': 0, 'by': 'Priya'},
    {'title': 'X', 'amount': -5, 'by': 'Priya'},
    {'title': '', 'amount': 5, 'by': 'Priya'},
    {'title': 'X', 'amount': 5, 'payer': 'Stranger', 'by': 'Priya'},
    {'title': 'X', 'amount': 5, 'category': 'Unknown', 'by': 'Priya'},
    {'title': 'X', 'amount': 5, 'split_with': ['Dad'], 'by': 'Priya'},
    {'title': 'X', 'amount': 5},
])
def test_add_rejects(home, agent, body):
    assert home.post('/expenses', agent, json=body).status_code == 400


def test_token_without_act_as_must_name_the_payer(home):
    secret = home.token('Script', expenses='write')
    assert home.post('/expenses', secret, json={'title': 'X', 'amount': 5}).status_code == 400
    resp = home.post('/expenses', secret, json={'title': 'X', 'amount': 5, 'payer': 'Mom'})
    assert resp.status_code == 201 and resp.get_json()['created_by'] == 'Script'


def test_no_deletes_rule_edits_or_settlements(home, agent):
    rid = add_rule(home)
    assert home.delete(f'/expenses/rules/{rid}', agent).status_code in (404, 405)
    assert home.put(f'/expenses/rules/{rid}', agent, json={'unit_price': 1}).status_code in (404, 405)
    assert home.post('/expenses/settle', agent, json={}).status_code in (404, 405)
    with home.ctx():
        assert db.session.get(RecurringExpense, rid).unit_price == 60.0


def test_switched_off(home, agent):
    home.toggle('expense_tracker', False)
    assert home.get('/expenses/rules', agent).status_code == 404
    home.toggle('expense_tracker', True)
    assert home.get('/expenses/rules', agent).status_code == 200
