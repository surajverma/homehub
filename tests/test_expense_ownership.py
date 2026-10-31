"""Only the person who entered an expense, or the admin, may change it; the payer does not get that right.
Rows from before created_by was kept fall back to the payer."""
import io
import json
from datetime import date

import pytest

from app import create_app, db
from app.blueprints.expenses import _generate_recurring_entries_until
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
def client(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    return make_app().test_client()


def add_entry(client, created_by='Alice', payer='Bob', **extra):
    """An expense Alice entered, paid by Bob (or as given). created_by=None is an older row."""
    values = dict(date=date(2026, 10, 2), title='Milk', amount=60.0, payer=payer, created_by=created_by)
    values.update(extra)
    with client.application.app_context():
        entry = ExpenseEntry(**values)
        db.session.add(entry)
        db.session.commit()
        return entry.id


def get(client, entry_id):
    with client.application.app_context():
        return db.session.get(ExpenseEntry, entry_id)


def edit(client, entry_id, user):
    client.post(f'/expenses/edit/{entry_id}', data={'user': user, 'title': f'edited by {user}', 'amount': '60'})
    entry = get(client, entry_id)
    return entry is not None and entry.title == f'edited by {user}'


def delete(client, entry_id, user):
    client.post(f'/expenses/delete/{entry_id}', data={'user': user})
    return get(client, entry_id) is None


def bulk_delete(client, entry_id, user):
    client.post('/expenses/bulk-delete', data={'user': user, 'ids': [str(entry_id)]})
    return get(client, entry_id) is None


def skip(client, entry_id, user):
    client.post(f'/expenses/skip/{entry_id}', data={'user': user})
    return bool(get(client, entry_id).skipped)


ACTIONS = {'edit': edit, 'delete': delete, 'bulk_delete': bulk_delete, 'skip': skip}


@pytest.mark.parametrize('action', list(ACTIONS))
@pytest.mark.parametrize('user,allowed', [
    ('Alice', True),           # entered it
    ('Bob', False),            # paid, but someone else entered it
    ('Carol', False),          # unrelated
    ('Administrator', True),   # admin (no admin password set)
])
def test_creator_or_admin_can_change(client, action, user, allowed):
    entry_id = add_entry(client, recurring_id=7 if action == 'skip' else None)
    assert ACTIONS[action](client, entry_id, user) is allowed


@pytest.mark.parametrize('action', list(ACTIONS))
@pytest.mark.parametrize('user,allowed', [('Bob', True), ('Alice', False), ('Administrator', True)])
def test_older_rows_fall_back_to_payer(client, action, user, allowed):
    entry_id = add_entry(client, created_by=None, recurring_id=7 if action == 'skip' else None)
    assert ACTIONS[action](client, entry_id, user) is allowed


def test_locked_admin_name_is_not_enough(client):
    from app import admin
    with client.application.app_context():
        admin.set_admin_password('4321')
    entry_id = add_entry(client)
    assert not delete(client, entry_id, 'Administrator')
    client.post('/admin/unlock', json={'password': '4321'})
    assert delete(client, entry_id, 'Administrator')


def test_add_expense_records_who_entered_it(client):
    client.post('/expenses', data={'form_type': 'single', 'user': 'Alice', 'payer': 'Bob', 'title': 'Bread',
                                   'amount': '40', 'date': '2026-10-03'})
    with client.application.app_context():
        entry = ExpenseEntry.query.filter_by(title='Bread').one()
        assert (entry.payer, entry.created_by) == ('Bob', 'Alice')
    # The payer picker stays open, and the payer cannot edit what Alice entered
    assert not edit(client, entry.id, 'Bob')
    assert edit(client, entry.id, 'Alice')


def test_settlement_records_who_entered_it(client):
    client.post('/expenses/settle', data={'user': 'Bob', 'from_member': 'Bob', 'to_member': 'Alice', 'amount': '25'})
    with client.application.app_context():
        entry = ExpenseEntry.query.filter_by(is_settlement=True).one()
        assert entry.created_by == 'Bob'


def test_recurring_entries_belong_to_rule_creator(client):
    with client.application.app_context():
        db.session.add(RecurringExpense(title='Paper', unit_price=8, default_quantity=1, frequency='daily',
                                        start_date=date(2026, 10, 1), end_date=date(2026, 10, 2), creator='Carol',
                                        effective_from=date(2026, 10, 1)))
        db.session.commit()
        _generate_recurring_entries_until(date(2026, 10, 2))
        assert {e.created_by for e in ExpenseEntry.query.all()} == {'Carol'}


def test_month_payload_says_who_owns_each_entry(client):
    add_entry(client)
    add_entry(client, created_by=None, title='Old')
    data = client.get('/api/expenses/month?year=2026&month=10').get_json()
    entries = {e['title']: e for e in data['by_date']['2026-10-02']['entries']}
    assert (entries['Milk']['created_by'], entries['Milk']['owner']) == ('Alice', 'Alice')
    assert (entries['Old']['created_by'], entries['Old']['owner']) == ('', 'Bob')


def test_export_has_added_by_and_import_ignores_it(client):
    add_entry(client)
    text = client.get('/expenses/export.csv?month=2026-10').get_data(as_text=True).lstrip('﻿')
    header, row = text.splitlines()[:2]
    assert header.endswith(',added_by')
    assert row.endswith(',Alice')

    # Carol imports a file claiming Alice added the rows: Carol owns them
    other = make_app().test_client()
    other.post('/expenses/import', data={'file': (io.BytesIO(text.encode()), 'x.csv')}, content_type='multipart/form-data')
    other.post('/expenses/import/confirm', data={'user': 'Carol', 'rows': ['1']})
    with other.application.app_context():
        entry = ExpenseEntry.query.one()
        assert (entry.payer, entry.created_by) == ('Bob', 'Carol')


def test_import_needs_to_know_who_is_importing(client):
    client.post('/expenses/import', data={'file': (io.BytesIO(b'date,title,amount,payer\n2026-10-01,Milk,5,Bob\n'), 'x.csv')},
                content_type='multipart/form-data')
    resp = client.post('/expenses/import/confirm', data={'rows': ['1']}, follow_redirects=True)
    assert 'Choose who you are' in resp.get_data(as_text=True)
    with client.application.app_context():
        assert ExpenseEntry.query.count() == 0


def test_migration_adds_created_by_without_touching_rows(tmp_path):
    import sqlite3
    from app import migrations
    path = tmp_path / 'old.db'
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE expense_entry (id INTEGER PRIMARY KEY, date DATE, title TEXT, category TEXT, unit_price REAL, "
                 "quantity REAL, amount REAL, payer TEXT, recurring_id INTEGER, timestamp TIMESTAMP)")
    conn.execute("INSERT INTO expense_entry (date, title, amount, payer) VALUES ('2026-10-01', 'Milk', 5, 'Bob')")
    conn.commit()
    conn.close()
    migrations.run(str(path))
    conn = sqlite3.connect(path)
    row = conn.execute('SELECT title, amount, payer, created_by FROM expense_entry').fetchone()
    conn.close()
    assert row == ('Milk', 5, 'Bob', None)
