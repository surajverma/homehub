"""Undo when SQLite has reused a deleted rule's id, and end dates of reminder rules that already ended."""
from datetime import date, timedelta

import pytest

from app import create_app, db
from app.models import Chore, ExpenseEntry, RecurringChore, RecurringExpense, RecurringReminder, UndoStash

TODAY = date.today()


def days_ago(n):
    return TODAY - timedelta(days=n)


@pytest.fixture()
def client():
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite://', 'SECRET_KEY': 'test'})
    with app.app_context():
        db.create_all()
        db.session.execute(db.text("CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY, value TEXT)"))
        db.session.commit()
    return app.test_client()


def last_token(client):
    with client.application.app_context():
        return UndoStash.query.order_by(UndoStash.id.desc()).first().token


# --- Undo after the id was reused --------------------------------------------------------------

def add_expense_rule(client, title, creator, price):
    client.post('/expenses', data={'form_type': 'recurring', 'title': title, 'unit_price': str(price),
                                   'default_quantity': '1', 'frequency': 'daily', 'creator': creator,
                                   'start_date': days_ago(2).isoformat()})
    client.get('/expenses')  # generates the entries up to today
    with client.application.app_context():
        return RecurringExpense.query.filter_by(title=title).one().id


def expense_rules(client):
    """title -> (creator, sorted amounts of its entries)"""
    with client.application.app_context():
        return {
            r.title: (r.creator, sorted(e.amount for e in ExpenseEntry.query.filter_by(recurring_id=r.id)))
            for r in RecurringExpense.query.all()
        }


@pytest.mark.parametrize('delete_entries', ['1', ''])
def test_undo_of_a_deleted_expense_rule_leaves_a_rule_that_took_its_id_alone(client, delete_entries):
    milk = add_expense_rule(client, 'Milk', 'Mom', 10)
    client.post(f'/expenses/recurring/delete/{milk}', data={'user': 'Mom', 'delete_entries': delete_entries})
    token = last_token(client)
    # Someone else adds a rule inside the undo window, and SQLite hands it the id that just became free
    paper = add_expense_rule(client, 'Paper', 'Dad', 7)
    assert paper == milk
    if delete_entries:
        assert expense_rules(client) == {'Paper': ('Dad', [7.0, 7.0, 7.0])}

    assert client.post(f'/undo/{token}').get_json()['ok'] is True

    rules = expense_rules(client)
    assert rules == {'Paper': ('Dad', [7.0, 7.0, 7.0]), 'Milk': ('Mom', [10.0, 10.0, 10.0])}
    with client.application.app_context():
        assert RecurringExpense.query.filter_by(title='Paper').one().id == paper
        assert RecurringExpense.query.filter_by(title='Milk').one().id != paper
        assert ExpenseEntry.query.count() == 6


def test_undo_of_an_expense_rule_edit_still_restores_in_place(client):
    milk = add_expense_rule(client, 'Milk', 'Mom', 10)
    client.post(f'/expenses/recurring/edit/{milk}', data={
        'user': 'Mom', 'title': 'Curd', 'unit_price': '12', 'default_quantity': '1', 'frequency': 'daily',
        'edit_strategy': 'split_rule', 'effective_from': days_ago(1).isoformat()})
    assert client.post(f'/undo/{last_token(client)}').get_json()['ok'] is True
    assert expense_rules(client) == {'Milk': ('Mom', [10.0, 10.0, 10.0])}
    with client.application.app_context():
        assert RecurringExpense.query.one().id == milk


def test_undo_of_a_split_never_deletes_a_rule_that_took_the_new_rules_id(client):
    milk = add_expense_rule(client, 'Milk', 'Mom', 10)
    client.post(f'/expenses/recurring/edit/{milk}', data={
        'user': 'Mom', 'title': 'Curd', 'unit_price': '12', 'default_quantity': '1', 'frequency': 'daily',
        'edit_strategy': 'split_rule', 'effective_from': days_ago(1).isoformat()})
    token = last_token(client)
    with client.application.app_context():
        curd = RecurringExpense.query.filter_by(title='Curd').one().id
    client.post(f'/expenses/recurring/delete/{curd}', data={'user': 'Mom', 'delete_entries': '1'})
    paper = add_expense_rule(client, 'Paper', 'Dad', 7)
    assert paper == curd

    client.post(f'/undo/{token}')

    rules = expense_rules(client)
    assert rules['Paper'] == ('Dad', [7.0, 7.0, 7.0])
    assert rules['Milk'] == ('Mom', [10.0, 10.0, 10.0])


def add_recurring_chore(client, description, creator):
    client.post('/chores', data={'description': description, 'creator': creator, 'tags': '[]', 'is_recurring': 'on',
                                 'rec_interval': '1', 'rec_unit': 'week', 'rec_start_date': TODAY.isoformat(),
                                 'rec_end_date': ''})
    with client.application.app_context():
        return RecurringChore.query.filter_by(description=description).one().id


def chore_rules(client):
    """description -> (creator, descriptions of its chores)"""
    with client.application.app_context():
        return {
            r.description: (r.creator, [c.description for c in Chore.query.filter_by(recurring_id=r.id)])
            for r in RecurringChore.query.all()
        }


def test_undo_of_a_deleted_recurring_chore_leaves_one_that_took_its_id_alone(client):
    plants = add_recurring_chore(client, 'Water plants', 'Mom')
    client.post(f'/chores/recurring/delete/{plants}', data={'user': 'Mom'})
    token = last_token(client)
    bins = add_recurring_chore(client, 'Take out bins', 'Dad')
    assert bins == plants

    assert client.post(f'/undo/{token}').get_json()['ok'] is True

    assert chore_rules(client) == {'Take out bins': ('Dad', ['Take out bins']),
                                   'Water plants': ('Mom', ['Water plants'])}
    with client.application.app_context():
        assert RecurringChore.query.filter_by(description='Take out bins').one().id == bins


def test_undo_of_a_chore_tick_ignores_a_chore_that_took_its_id(client):
    plants = add_recurring_chore(client, 'Water plants', 'Mom')
    with client.application.app_context():
        chore_id = Chore.query.one().id
    client.post(f'/chores/toggle/{chore_id}')
    token = last_token(client)
    client.post(f'/chores/recurring/delete/{plants}', data={'user': 'Mom'})
    add_recurring_chore(client, 'Take out bins', 'Dad')
    with client.application.app_context():
        other = Chore.query.one()
        assert other.id == chore_id
        other.due_date = TODAY + timedelta(days=7)
        db.session.commit()

    client.post(f'/undo/{token}')

    with client.application.app_context():
        assert Chore.query.one().due_date == TODAY + timedelta(days=7)


def add_reminder_rule(client, title, creator, start):
    return client.post('/api/reminders', json={'date': start.isoformat(), 'title': title, 'description': '',
                                               'creator': creator, 'recurring': {'interval': 1, 'unit': 'day'}}
                       ).get_json()['recurring_id']


def reminder_rules(client):
    with client.application.app_context():
        return {r.title: (r.creator, r.end_date) for r in RecurringReminder.query.all()}


def test_undo_of_a_deleted_reminder_rule_leaves_one_that_took_its_id_alone(client):
    rent = add_reminder_rule(client, 'Pay rent', 'Mom', days_ago(5))
    token = client.delete(f'/api/recurring_rules/{rent}', json={'creator': 'Mom', 'scope': 'all'}).get_json()['undo_token']
    gym = add_reminder_rule(client, 'Gym', 'Dad', TODAY)
    assert gym == rent

    assert client.post(f'/undo/{token}').get_json()['ok'] is True

    assert reminder_rules(client) == {'Gym': ('Dad', None), 'Pay rent': ('Mom', None)}
    with client.application.app_context():
        assert RecurringReminder.query.filter_by(title='Gym').one().id == gym


def test_undo_of_a_reminder_edit_never_deletes_a_rule_that_took_the_successors_id(client):
    rent = add_reminder_rule(client, 'Pay rent', 'Mom', days_ago(5))
    resp = client.patch(f'/api/recurring_rules/{rent}', json={'creator': 'Mom', 'title': 'Pay landlord'}).get_json()
    successor = resp['rule']['id']
    client.delete(f'/api/recurring_rules/{successor}', json={'creator': 'Mom', 'scope': 'all'})
    gym = add_reminder_rule(client, 'Gym', 'Dad', TODAY)
    assert gym == successor

    client.post(f"/undo/{resp['undo_token']}")

    assert reminder_rules(client) == {'Gym': ('Dad', None), 'Pay rent': ('Mom', None)}


# --- End date of a reminder rule that already ended --------------------------------------------

def occurrences(client, day):
    data = client.get(f'/api/reminders?scope=day&date={day.isoformat()}').get_json()
    return [r['title'] for r in data['reminders']]


def ended_rule(client, ended_days_ago=5):
    rid = add_reminder_rule(client, 'Pay rent', 'Mom', days_ago(20))
    with client.application.app_context():
        db.session.get(RecurringReminder, rid).end_date = days_ago(ended_days_ago)
        db.session.commit()
    return rid


def end_date(client, rid):
    with client.application.app_context():
        return db.session.get(RecurringReminder, rid).end_date


def test_moving_an_ended_rules_end_earlier_never_moves_it_later(client):
    rid = ended_rule(client)
    resp = client.patch(f'/api/recurring_rules/{rid}', json={'creator': 'Mom', 'end_date': days_ago(7).isoformat()})
    assert resp.status_code == 200
    # It ended 5 days ago; asking for 7 days ago must not turn that into yesterday
    assert end_date(client, rid) == days_ago(7)
    assert occurrences(client, days_ago(4)) == [] and occurrences(client, days_ago(1)) == []


def test_an_ended_rule_cannot_get_more_past_dates(client):
    rid = ended_rule(client)
    resp = client.patch(f'/api/recurring_rules/{rid}', json={'creator': 'Mom', 'end_date': days_ago(2).isoformat()})
    assert resp.status_code == 400
    assert end_date(client, rid) == days_ago(5)
    assert occurrences(client, days_ago(3)) == []


@pytest.mark.parametrize('new_end', [None, TODAY + timedelta(days=10)])
def test_continuing_an_ended_rule_starts_again_from_today(client, new_end):
    rid = ended_rule(client)
    resp = client.patch(f'/api/recurring_rules/{rid}',
                        json={'creator': 'Mom', 'end_date': new_end.isoformat() if new_end else None}).get_json()
    assert resp['ok'] is True and resp['split'] is True
    # The gap between the old end and today stays empty
    assert end_date(client, rid) == days_ago(5)
    assert occurrences(client, days_ago(3)) == [] and occurrences(client, days_ago(1)) == []
    assert occurrences(client, TODAY) == ['Pay rent']
    assert occurrences(client, days_ago(6)) == ['Pay rent']


def test_other_edits_to_an_ended_rule_add_no_past_dates(client):
    rid = ended_rule(client)
    for change in ({'interval': 2, 'unit': 'day'}, {'start_date': days_ago(30).isoformat()}, {'title': 'Rent'}):
        resp = client.patch(f'/api/recurring_rules/{rid}', json=dict(change, creator='Mom'))
        assert resp.status_code == 400, change
    with client.application.app_context():
        assert RecurringReminder.query.count() == 1
    assert end_date(client, rid) == days_ago(5)
    assert occurrences(client, days_ago(3)) == []
    assert occurrences(client, days_ago(15)) == ['Pay rent']


def test_active_rule_end_date_in_the_past_is_taken_as_asked(client):
    rid = add_reminder_rule(client, 'Pay rent', 'Mom', days_ago(20))
    client.patch(f'/api/recurring_rules/{rid}', json={'creator': 'Mom', 'end_date': days_ago(6).isoformat()})
    assert end_date(client, rid) == days_ago(6)
    client.patch(f'/api/recurring_rules/{rid}', json={'creator': 'Mom', 'end_date': (TODAY + timedelta(days=3)).isoformat()})
    # Now ended 6 days ago, so a later end continues it from today instead of filling the gap
    assert end_date(client, rid) == days_ago(6)
    assert occurrences(client, days_ago(2)) == [] and occurrences(client, TODAY) == ['Pay rent']
