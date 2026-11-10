"""The shared 7-day trash: deletes keep a copy, the Trash page restores it, the same rules as each module."""
import json
import re
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from api_support import Home, trash_action
from app import db, migrations, trash, undo
from app.clock import utcnow
from app.models import (Chore, ExpenseEntry, ExpiryItem, Note, Recipe, RecurringChore, RecurringExpense,
                        ShoppingItem, UndoStash)

TODAY = date.today()


@pytest.fixture()
def home(tmp_path, monkeypatch):
    home = Home(tmp_path, monkeypatch)
    with home.ctx():
        db.session.execute(db.text("CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY, value TEXT)"))
        db.session.commit()
    return home


def flashes(home):
    with home.client.session_transaction() as sess:
        return [(category, message) for category, message in sess.pop('_flashes', [])]


def undo_offer(home):
    """The Undo toast offered by the last request."""
    offers = [json.loads(m) for c, m in flashes(home) if c == 'undo']
    assert offers, 'no Undo offered'
    return offers[-1]


def trash_rows(home):
    with home.ctx():
        return [(e.module, e.label, e.owner, e.deleted_by) for e in trash.entries()]


def entry_id(home, label):
    with home.ctx():
        return next(e.id for e in trash.entries() if e.label == label)


def count(home, model):
    with home.ctx():
        return model.query.count()


# --- Each module moves its deletes to the trash ---

def add_shopping(home, item='Milk', creator='Priya'):
    home.client.post('/shopping', data={'item': item, 'creator': creator, 'tags': '["Dairy"]'})
    with home.ctx():
        return ShoppingItem.query.filter_by(item=item).order_by(ShoppingItem.id.desc()).first().id


def test_shopping_delete_goes_to_the_trash(home):
    item_id = add_shopping(home)
    flashes(home)
    home.client.post(f'/shopping/delete/{item_id}', data={'user': 'Priya'})
    offer = undo_offer(home)
    assert offer['message'] == 'Milk moved to the trash.' and offer['seconds'] == 20
    assert count(home, ShoppingItem) == 0
    assert trash_rows(home) == [('shopping', 'Milk', 'Priya', 'Priya')]
    with home.ctx():
        entry = UndoStash.query.one()
        assert timedelta(days=6, hours=23) < entry.expires_at - utcnow() <= timedelta(days=7)
    # The toast's Undo restores it, tags and all
    assert home.client.post(f"/undo/{offer['token']}").get_json() == {'ok': True}
    with home.ctx():
        item = ShoppingItem.query.one()
        assert (item.id, item.item, item.creator, json.loads(item.tags)) == (item_id, 'Milk', 'Priya', ['Dairy'])
        assert UndoStash.query.count() == 0


def test_other_person_cannot_delete(home):
    item_id = add_shopping(home)
    home.client.post(f'/shopping/delete/{item_id}', data={'user': 'Dad'})
    assert count(home, ShoppingItem) == 1 and trash_rows(home) == []


@pytest.mark.parametrize('path, form, model, delete, module, label, message', [
    ('/notes', {'content': '<b>Gate code</b> is 1234', 'creator': 'Mom'}, Note, '/notes/delete/{id}', 'notes',
     'Gate code is 1234', 'Note moved to the trash.'),
    ('/recipes', {'title': 'Dal', 'ingredients': 'Lentils', 'instructions': 'Cook', 'creator': 'Mom'}, Recipe,
     '/recipes/delete/{id}', 'recipes', 'Dal', 'Dal moved to the trash.'),
    ('/expiry', {'name': 'Passport', 'expiry_date': '2030-01-01', 'creator': 'Mom'}, ExpiryItem,
     '/expiry/delete/{id}', 'expiry', 'Passport', 'Passport moved to the trash.'),
    ('/chores', {'description': 'Water plants', 'creator': 'Mom'}, Chore, '/chores/delete/{id}', 'chores',
     'Water plants', 'Water plants moved to the trash.'),
])
def test_each_module_moves_deletes_to_the_trash(home, path, form, model, delete, module, label, message):
    home.client.post(path, data=form)
    with home.ctx():
        row = model.query.one()
        row_id, before = row.id, undo.row_to_dict(row)
    flashes(home)
    home.client.post(delete.format(id=row_id), data={'user': 'Mom'})
    assert undo_offer(home)['message'] == message
    assert count(home, model) == 0
    assert trash_rows(home) == [(module, label, 'Mom', 'Mom')]
    resp = trash_action(home.client, entry_id(home, label), 'restore', {'user': 'Mom'}, follow_redirects=True)
    assert f'{label} restored.' in resp.get_data(as_text=True)
    with home.ctx():
        assert undo.row_to_dict(model.query.one()) == before
    assert trash_rows(home) == []


def test_recurring_chore_goes_to_the_trash_with_its_chores(home):
    home.client.post('/chores', data={'description': 'Bins', 'creator': 'Dad', 'is_recurring': '1', 'rec_interval': '1',
                                      'rec_unit': 'week', 'rec_start_date': TODAY.isoformat()})
    with home.ctx():
        rule = RecurringChore.query.one()
        rule_id, chores = rule.id, Chore.query.filter_by(recurring_id=rule.id).count()
    assert chores == 1
    flashes(home)
    home.client.post(f'/chores/recurring/delete/{rule_id}', data={'user': 'Dad'})
    assert undo_offer(home)['message'] == 'Recurring chore Bins moved to the trash.'
    assert count(home, RecurringChore) == 0 and count(home, Chore) == 0
    assert trash_rows(home) == [('chores', 'Bins', 'Dad', 'Dad')]
    trash_action(home.client, entry_id(home, 'Bins'), 'restore', {'user': 'Dad'})
    with home.ctx():
        assert RecurringChore.query.one().id == rule_id
        assert Chore.query.filter_by(recurring_id=rule_id).count() == 1


# --- The Trash page ---

def test_page_lists_newest_first_and_filters_by_module(home):
    for name in ('Milk', 'Eggs'):
        home.client.post(f'/shopping/delete/{add_shopping(home, name)}', data={'user': 'Priya'})
    home.client.post('/notes', data={'content': 'Call plumber', 'creator': 'Dad'})
    with home.ctx():
        note_id = Note.query.one().id
    home.client.post(f'/notes/delete/{note_id}', data={'user': 'Dad'})
    flashes(home)
    resp = home.client.get('/trash')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert body.index('Call plumber') < body.index('Eggs') < body.index('Milk')
    assert 'Deleted by Priya' in body and '7 days left' in body
    body = home.client.get('/trash?module=shopping').get_data(as_text=True)
    assert 'Milk' in body and 'Shopping List · ' not in body and 'Eggs' in body and 'Call plumber' not in body
    assert 'aria-current="page"' in body and 'Back to Shopping List' in body
    # An unknown module shows everything
    assert 'Call plumber' in home.client.get('/trash?module=nope').get_data(as_text=True)


def test_page_is_for_everyone_and_in_the_sidebar(home):
    body = home.client.get('/').get_data(as_text=True)
    assert 'href="/trash"' in body
    assert 'Nothing here' in home.client.get('/trash').get_data(as_text=True)


def test_trash_page_follows_a_user_switch():
    """Switching user in the top bar fires user-switched; the page re-checks who may restore without a reload."""
    import os
    from app import config as config_module
    with open(os.path.join(config_module.BASE_DIR, 'static', 'js', 'pages', 'trash.js'), encoding='utf-8') as f:
        script = f.read()
    assert "addEventListener('user-switched'" in script


@pytest.mark.parametrize('page, module', [('/shopping', 'shopping'), ('/notes', 'notes'), ('/recipes', 'recipes'),
                                          ('/expiry', 'expiry'), ('/chores', 'chores'), ('/expenses', 'expenses')])
def test_each_module_page_links_to_its_trash(home, page, module):
    body = home.client.get(page).get_data(as_text=True)
    assert f'href="/trash?module={module}"' in body


def test_only_the_owner_or_admin_may_restore_or_delete(home):
    home.client.post(f'/shopping/delete/{add_shopping(home)}', data={'user': 'Priya'})
    eid = entry_id(home, 'Milk')
    resp = trash_action(home.client, eid, 'restore', {'user': 'Dad'}, follow_redirects=True)
    assert 'Not allowed to restore Milk.' in resp.get_data(as_text=True)
    resp = trash_action(home.client, eid, 'delete', {'user': 'Dad'}, follow_redirects=True)
    assert 'Not allowed to delete Milk.' in resp.get_data(as_text=True)
    assert len(trash_rows(home)) == 1 and count(home, ShoppingItem) == 0
    # The page tells whose it is; the script hides the buttons for others
    assert 'data-owner="Priya"' in home.client.get('/trash').get_data(as_text=True)
    resp = trash_action(home.client, eid, 'restore', {'user': 'Administrator', 'module': 'shopping'})
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/trash?module=shopping')
    assert count(home, ShoppingItem) == 1


def test_delete_forever(home):
    home.client.post(f'/shopping/delete/{add_shopping(home)}', data={'user': 'Priya'})
    eid = entry_id(home, 'Milk')
    resp = trash_action(home.client, eid, 'delete', {'user': 'Priya'}, follow_redirects=True)
    assert 'Milk deleted for good.' in resp.get_data(as_text=True)
    assert trash_rows(home) == [] and count(home, ShoppingItem) == 0
    resp = trash_action(home.client, eid, 'restore', {'user': 'Priya'}, follow_redirects=True)
    assert 'That is no longer in the trash.' in resp.get_data(as_text=True)


def test_restore_from_another_site_is_blocked(home):
    home.client.post(f'/shopping/delete/{add_shopping(home)}', data={'user': 'Priya'})
    eid = entry_id(home, 'Milk')
    resp = trash_action(home.client, eid, 'restore', {'user': 'Priya'}, headers={'Sec-Fetch-Site': 'cross-site'})
    assert resp.status_code == 403 and count(home, ShoppingItem) == 0


def test_ids_in_the_trash_are_not_given_to_new_rows(home):
    first = add_shopping(home, 'Milk')
    home.client.post(f'/shopping/delete/{first}', data={'user': 'Priya'})
    # SQLite would hand the freed id to the next row; it stays Milk's while Milk is in the trash
    bread = add_shopping(home, 'Bread')
    assert bread == first + 1
    with home.ctx():
        # Several new rows in one go each get their own id
        db.session.add_all([ShoppingItem(item='Tea', creator='Priya'), ShoppingItem(item='Rice', creator='Priya')])
        db.session.commit()
        assert sorted(i.id for i in ShoppingItem.query.all()) == [bread, bread + 1, bread + 2]
    trash_action(home.client, entry_id(home, 'Milk'), 'restore', {'user': 'Priya'})
    with home.ctx():
        assert db.session.get(ShoppingItem, first).item == 'Milk'


def test_restore_when_the_id_was_taken_anyway(home):
    """An id can still be taken (an entry made before ids were kept, or a row added with its id): the restore
    then gives the record a new id instead of overwriting the other one."""
    first = add_shopping(home, 'Milk')
    home.client.post(f'/shopping/delete/{first}', data={'user': 'Priya'})
    with home.ctx():
        db.session.add(ShoppingItem(id=first, item='Bread', creator='Priya'))
        db.session.commit()
    trash_action(home.client, entry_id(home, 'Milk'), 'restore', {'user': 'Priya'})
    with home.ctx():
        items = {i.item: i.id for i in ShoppingItem.query.all()}
    assert items['Bread'] == first and items['Milk'] != first


def test_retrying_an_api_delete_never_trashes_a_newer_reminder(home):
    """The review's case: a reminder trashed through the API, a new one by the same owner, then the client
    retries the delete. Before, the new reminder could have the old id and be trashed by the retry."""
    from app.models import Reminder
    deleter = home.token('Deleter', act_as=True, allow_delete=True, reminders='write')
    old = home.post('/reminders', deleter, json={'title': 'Dentist', 'date': TODAY.isoformat(), 'by': 'Priya'}).get_json()['id']
    assert home.delete(f'/reminders/{old}', deleter, json={'by': 'Priya'}).status_code == 200
    new = home.post('/reminders', deleter, json={'title': 'Vet', 'date': TODAY.isoformat(), 'by': 'Priya'}).get_json()['id']
    assert new != old
    assert home.delete(f'/reminders/{old}', deleter, json={'by': 'Priya'}).status_code == 404
    with home.ctx():
        assert [r.title for r in Reminder.query.all()] == ['Vet']


def test_short_undo_stashes_are_not_in_the_trash(home):
    with home.ctx():
        undo.stash_undo('chore_done', {'chore_id': 1})
        assert trash.entries() == []
        stash = UndoStash.query.one()
        assert stash.expires_at - utcnow() < timedelta(minutes=1)


def test_expired_entries_are_purged(home):
    home.client.post(f'/shopping/delete/{add_shopping(home)}', data={'user': 'Priya'})
    with home.ctx():
        UndoStash.query.update({UndoStash.expires_at: utcnow() - timedelta(seconds=1)})
        db.session.commit()
    assert 'Nothing here' in home.client.get('/trash').get_data(as_text=True)
    with home.ctx():
        assert UndoStash.query.count() == 0


def test_long_labels_are_cut(home):
    with home.ctx():
        assert undo.trash_label('word ' * 40) == ('word ' * 16).strip()[:79].rstrip() + '…'
        assert undo.trash_label('<p>Hi &amp; <b>bye</b></p>') == 'Hi & bye'


# --- Expenses ---

def add_expense(home, title='Groceries', amount='120', user='Priya', day=None):
    home.client.post('/expenses', data={'date': (day or TODAY).isoformat(), 'title': title, 'amount': amount,
                                        'payer': user, 'user': user, 'category': ''})
    with home.ctx():
        return ExpenseEntry.query.filter_by(title=title).order_by(ExpenseEntry.id.desc()).first().id


def make_rule(home, user='Priya', start=None):
    """A daily milk rule with its days generated up to today; returns its id."""
    start = start or TODAY - timedelta(days=3)
    with home.ctx():
        rule = RecurringExpense(title='Milk', unit_price=30.0, default_quantity=2.0, frequency='daily',
                                monthly_mode='day_of_month', start_date=start, effective_from=start, creator=user)
        db.session.add(rule)
        db.session.commit()
        rid = rule.id
    home.client.get('/expenses/recurring')
    return rid


def test_expense_entry_goes_to_the_trash(home):
    eid = add_expense(home)
    flashes(home)
    home.client.post(f'/expenses/delete/{eid}', data={'user': 'Priya'})
    assert undo_offer(home)['message'] == 'Groceries moved to the trash.'
    assert trash_rows(home) == [('expenses', f'Groceries ({TODAY.isoformat()})', 'Priya', 'Priya')]
    trash_action(home.client, entry_id(home, f'Groceries ({TODAY.isoformat()})'), 'restore', {'user': 'Priya'})
    with home.ctx():
        entry = db.session.get(ExpenseEntry, eid)
        assert (entry.title, entry.amount) == ('Groceries', 120.0)


def test_bulk_delete_offers_one_undo_and_lists_each(home):
    ids = [add_expense(home, f'Item {i}') for i in range(3)]
    flashes(home)
    home.client.post('/expenses/bulk-delete', data={'user': 'Priya', 'ids': [str(i) for i in ids]})
    offer = undo_offer(home)
    assert offer['message'] == '3 expenses moved to the trash.'
    assert len(trash_rows(home)) == 3 and count(home, ExpenseEntry) == 0
    assert home.client.post(f"/undo/{offer['token']}").get_json() == {'ok': True}
    assert count(home, ExpenseEntry) == 3 and trash_rows(home) == []


def test_recurring_rule_goes_to_the_trash(home):
    rid = make_rule(home)
    with home.ctx():
        days = ExpenseEntry.query.filter_by(recurring_id=rid).count()
    assert days >= 3
    flashes(home)
    home.client.post(f'/expenses/recurring/delete/{rid}', data={'user': 'Priya', 'delete_entries': '1'})
    assert undo_offer(home)['message'] == 'Recurring rule moved to the trash with its generated entries.'
    assert count(home, RecurringExpense) == 0 and count(home, ExpenseEntry) == 0
    assert trash_rows(home) == [('expenses', 'Milk', 'Priya', 'Priya')]
    trash_action(home.client, entry_id(home, 'Milk'), 'restore', {'user': 'Priya'})
    with home.ctx():
        assert ExpenseEntry.query.filter_by(recurring_id=rid).count() == days


def _delete_day(home, rid, day):
    with home.ctx():
        entry = ExpenseEntry.query.filter_by(recurring_id=rid, date=day).one()
        eid = entry.id
    home.client.post(f'/expenses/delete/{eid}', data={'user': 'Priya'})
    return f'Milk ({day.isoformat()})'


def test_day_whose_rule_is_in_the_trash_brings_the_rule_back(home):
    rid = make_rule(home)
    day = TODAY - timedelta(days=2)
    label = _delete_day(home, rid, day)
    home.client.post(f'/expenses/recurring/delete/{rid}', data={'user': 'Priya', 'delete_entries': '1'})
    resp = trash_action(home.client, entry_id(home, label), 'restore', {'user': 'Priya'}, follow_redirects=True)
    assert 'Its recurring rule Milk was restored too.' in resp.get_data(as_text=True)
    with home.ctx():
        rule = RecurringExpense.query.one()
        assert ExpenseEntry.query.filter_by(recurring_id=rule.id, date=day).count() == 1
    assert trash_rows(home) == []


def test_day_whose_rule_is_gone_for_good_comes_back_as_a_one_off(home):
    rid = make_rule(home)
    day = TODAY - timedelta(days=2)
    label = _delete_day(home, rid, day)
    home.client.post(f'/expenses/recurring/delete/{rid}', data={'user': 'Priya', 'delete_entries': '1'})
    trash_action(home.client, entry_id(home, 'Milk'), 'delete', {'user': 'Priya'})
    resp = trash_action(home.client, entry_id(home, label), 'restore', {'user': 'Priya'}, follow_redirects=True)
    assert 'no longer exists, so it was restored as a one-off expense' in resp.get_data(as_text=True)
    with home.ctx():
        entry = ExpenseEntry.query.one()
        assert (entry.date, entry.recurring_id) == (day, None)


def test_day_added_back_since_is_a_conflict(home):
    rid = make_rule(home)
    day = TODAY - timedelta(days=2)
    label = _delete_day(home, rid, day)
    home.client.post(f'/expenses/recurring/{rid}/restore', data={'user': 'Priya', 'date': day.isoformat()})
    resp = trash_action(home.client, entry_id(home, label), 'restore', {'user': 'Priya'}, follow_redirects=True)
    assert 'was added back since it was deleted' in resp.get_data(as_text=True)
    with home.ctx():
        assert ExpenseEntry.query.filter_by(recurring_id=rid, date=day).count() == 1
    assert len(trash_rows(home)) == 1


# --- flask trash ---

def test_cli_list_and_restore(home):
    runner = home.app.test_cli_runner()
    assert 'The trash is empty.' in runner.invoke(args=['trash', 'list']).output
    home.client.post(f'/shopping/delete/{add_shopping(home)}', data={'user': 'Priya'})
    eid = entry_id(home, 'Milk')
    output = runner.invoke(args=['trash', 'list']).output
    assert re.search(rf'^{eid}  \[shopping\]  Milk  - deleted by Priya on .* UTC; 7 days left$', output, re.M)
    assert 'Milk' not in runner.invoke(args=['trash', 'list', '--module', 'notes']).output
    result = runner.invoke(args=['trash', 'restore', str(eid)])
    assert result.exit_code == 0 and 'Restored Milk (shopping).' in result.output
    assert count(home, ShoppingItem) == 1
    assert runner.invoke(args=['trash', 'restore', str(eid)]).exit_code != 0


# --- Startup ---

def test_old_undo_table_gets_the_trash_columns(tmp_path):
    path = str(tmp_path / 'app.db')
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE undo_stash (id INTEGER PRIMARY KEY, token VARCHAR(32) NOT NULL UNIQUE, kind VARCHAR(32) NOT NULL, "
                 "payload TEXT NOT NULL, expires_at DATETIME NOT NULL)")
    conn.execute("INSERT INTO undo_stash (token, kind, payload, expires_at) VALUES ('a', 'x', '{}', '2000-01-01 00:00:00.000000')")
    conn.execute("INSERT INTO undo_stash (token, kind, payload, expires_at) VALUES ('b', 'x', '{}', '2999-01-01 00:00:00.000000')")
    conn.commit()
    conn.close()
    migrations.run(path)
    conn = sqlite3.connect(path)
    cols = {row[1] for row in conn.execute('PRAGMA table_info(undo_stash)')}
    assert {'module', 'label', 'owner', 'deleted_by', 'deleted_at'} <= cols
    # Expired stashes are purged at startup
    assert [r[0] for r in conn.execute('SELECT token FROM undo_stash')] == ['b']
    conn.close()


def test_trash_entries_from_before_record_ids_get_them(tmp_path):
    conn = sqlite3.connect(str(tmp_path / 'x.db'))
    conn.execute("CREATE TABLE undo_stash (id INTEGER PRIMARY KEY, kind TEXT, payload TEXT, module TEXT, record_id INTEGER)")
    conn.execute("""INSERT INTO undo_stash (kind, payload, module) VALUES ('note', '{"row": {"id": 7}}', 'notes')""")
    conn.execute("""INSERT INTO undo_stash (kind, payload, module) VALUES ('chore_rule', '{"rule": {"id": 3}, "chores": []}', 'chores')""")
    conn.execute("""INSERT INTO undo_stash (kind, payload, module) VALUES ('chore_done', '{"chore_id": 9}', NULL)""")
    assert migrations.fill_trash_record_ids(conn.cursor()) == 2
    assert [r[0] for r in conn.execute('SELECT record_id FROM undo_stash ORDER BY id')] == [7, 3, None]


def test_purge_expired_stashes(tmp_path):
    conn = sqlite3.connect(str(tmp_path / 'x.db'))
    conn.execute("CREATE TABLE undo_stash (id INTEGER PRIMARY KEY, token TEXT, kind TEXT, payload TEXT, expires_at DATETIME)")
    conn.execute("INSERT INTO undo_stash (token, expires_at) VALUES ('old', '2026-10-01 10:00:00.000000')")
    conn.execute("INSERT INTO undo_stash (token, expires_at) VALUES ('new', '2026-10-09 10:00:00.000000')")
    assert migrations.purge_expired_stashes(conn.cursor(), now=datetime(2026, 10, 5)) == 1
    assert [r[0] for r in conn.execute('SELECT token FROM undo_stash')] == ['new']


# --- Reminders ---

def add_reminder(home, title='Dentist', creator='Priya', day=None):
    day = (day or TODAY).isoformat()
    resp = home.client.post('/api/reminders', json={'title': title, 'creator': creator, 'start_date': day, 'date': day})
    return resp.get_json()['reminder']['id']


def calendar_titles(home):
    data = home.client.get(f'/api/reminders?scope=month&date={TODAY.isoformat()}').get_json()
    return sorted(r['title'] for r in data['reminders'])


def test_calendar_delete_goes_to_the_trash(home):
    from app.models import Reminder
    rid = add_reminder(home)
    flashes(home)
    home.client.post(f'/calendar/delete/{rid}', data={'user': 'Priya'})
    assert undo_offer(home)['message'] == 'Dentist moved to the trash.'
    assert count(home, Reminder) == 0
    assert trash_rows(home) == [('reminders', f'Dentist ({TODAY.isoformat()})', 'Priya', 'Priya')]
    trash_action(home.client, entry_id(home, f'Dentist ({TODAY.isoformat()})'), 'restore', {'user': 'Priya'})
    assert calendar_titles(home) == ['Dentist']


def test_calendar_list_delete_offers_the_servers_undo(home):
    one = add_reminder(home, 'One')
    two = add_reminder(home, 'Two')
    three = add_reminder(home, 'Three', creator='Dad')
    data = home.client.delete('/api/reminders', json={'ids': [one], 'creator': 'Priya'}).get_json()
    assert data['ok'] and data['deleted'] == 1 and data['undo_token']
    assert home.client.post(f"/undo/{data['undo_token']}").get_json() == {'ok': True}
    assert calendar_titles(home) == ['One', 'Three', 'Two']
    # Several at once: one Undo for all; someone else's reminder is left alone
    data = home.client.delete('/api/reminders', json={'ids': [one, two, three], 'creator': 'Priya'}).get_json()
    assert data['deleted'] == 2 and calendar_titles(home) == ['Three']
    assert len(trash_rows(home)) == 2
    assert home.client.post(f"/undo/{data['undo_token']}").get_json() == {'ok': True}
    assert calendar_titles(home) == ['One', 'Three', 'Two'] and trash_rows(home) == []


def test_bulk_form_delete(home):
    ids = [add_reminder(home, f'R{i}') for i in range(2)]
    flashes(home)
    home.client.post('/calendar/delete_bulk', data={'ids': ','.join(map(str, ids)), 'user': 'Priya'})
    assert undo_offer(home)['message'] == 'Moved 2 reminders to trash.'
    assert len(trash_rows(home)) == 2


def test_whole_series_goes_to_the_trash_but_ending_it_does_not(home):
    from app.models import RecurringReminder
    future = (TODAY + timedelta(days=3)).isoformat()
    rule = home.client.post('/api/reminders', json={'title': 'Pills', 'creator': 'Mom', 'start_date': future, 'date': future,
                                                    'recurring': {'interval': 1, 'unit': 'day'}}).get_json()['recurring_id']
    data = home.client.delete(f'/api/recurring_rules/{rule}', json={'creator': 'Mom'}).get_json()
    assert data['ok'] and data['ended'] is False
    assert count(home, RecurringReminder) == 0
    assert trash_rows(home) == [('reminders', 'Pills', 'Mom', 'Mom')]
    trash_action(home.client, entry_id(home, 'Pills'), 'restore', {'user': 'Mom'})
    assert count(home, RecurringReminder) == 1
    # A series that has started is only ended from today: nothing is removed, so nothing goes to the trash
    with home.ctx():
        r = RecurringReminder.query.one()
        r.start_date = TODAY - timedelta(days=5)
        db.session.commit()
        rule = r.id
    data = home.client.delete(f'/api/recurring_rules/{rule}', json={'creator': 'Mom'}).get_json()
    assert data['ended'] is True and trash_rows(home) == []
    assert data['undo_token']


def test_calendar_trash_button_opens_the_trash_page(home):
    body = home.client.get('/calendar').get_data(as_text=True)
    assert 'href="/trash?module=reminders"' in body and 'trashPanel' not in body
    body = home.client.get('/trash?module=reminders').get_data(as_text=True)
    assert 'Back to Calendar' in body


def test_reminders_trashed_before_the_upgrade_can_be_restored(tmp_path, monkeypatch):
    """The upgrade moves the calendar's old trash into the shared one; the Trash page restores from it."""
    from app import create_app
    from app.models import Reminder
    path = tmp_path / 'app.db'
    uri = 'sqlite:///' + path.as_posix()
    home = Home(tmp_path, monkeypatch)
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': uri, 'SECRET_KEY': 'test'})
    with app.app_context():
        db.create_all()
        app.config['HOMEHUB_CONFIG'] = home.app.config['HOMEHUB_CONFIG']
        deleted = utcnow() - timedelta(days=2)
        db.session.add(Reminder(date=TODAY, start_date=TODAY, start_time='09:30', time='09:30', title='Vet', creator='Dad',
                                category='health', deleted_at=deleted))
        db.session.add(Reminder(date=TODAY, start_date=TODAY, title='Kept', creator='Dad'))
        db.session.commit()
        rid = Reminder.query.filter_by(title='Vet').one().id
        db.engine.dispose()
    assert migrations.run(str(path)) is True
    client = app.test_client()
    with app.app_context():
        assert [r.title for r in Reminder.query.all()] == ['Kept']
        entry = trash.entries('reminders')[0]
        assert (entry.label, entry.owner, entry.deleted_by) == (f'Vet ({TODAY.isoformat()})', 'Dad', '')
        assert abs((entry.deleted_at - deleted).total_seconds()) < 1
        assert abs((entry.expires_at - (deleted + timedelta(days=7))).total_seconds()) < 1
        entry_id = entry.id
    body = client.get('/trash').get_data(as_text=True)
    assert 'Deleted at ' in body and '5 days left' in body
    trash_action(client, entry_id, 'restore', {'user': 'Dad'})
    with app.app_context():
        r = db.session.get(Reminder, rid)
        assert (r.title, r.start_time, r.category, r.creator, r.deleted_at) == ('Vet', '09:30', 'health', 'Dad', None)


def test_blank_user_cannot_restore_or_delete_an_ownerless_entry(home):
    """An entry without an owner (an old record) and a form without a user must not count as a match."""
    with home.ctx():
        db.session.add(ShoppingItem(item='Old item', creator=''))
        db.session.commit()
        undo.trash_row('shopping_item', ShoppingItem.query.one(), undo.trash_info('shopping', 'Old item', '', 'Administrator'))
    eid = entry_id(home, 'Old item')
    for action in ('restore', 'delete'):
        for form in ({}, {'user': ''}, {'user': '  '}):
            resp = trash_action(home.client, eid, action, form, follow_redirects=True)
            assert 'Not allowed to' in resp.get_data(as_text=True)
    assert len(trash_rows(home)) == 1 and count(home, ShoppingItem) == 0
    # Owned entries need a user too
    home.client.post(f'/shopping/delete/{add_shopping(home)}', data={'user': 'Priya'})
    assert 'Not allowed to restore Milk.' in trash_action(home.client, entry_id(home, 'Milk'), 'restore', {}, follow_redirects=True).get_data(as_text=True)
    # The admin may restore the ownerless one
    trash_action(home.client, eid, 'restore', {'user': 'Administrator'})
    assert count(home, ShoppingItem) == 1


def test_a_page_left_open_never_acts_on_a_newer_entry(home):
    """Restore the only entry in one tab, trash something else: it can get the same entry id. The old tab's
    Delete forever must not remove the new entry."""
    home.client.post(f'/shopping/delete/{add_shopping(home, "Milk")}', data={'user': 'Priya'})
    old = entry_id(home, 'Milk')
    with home.ctx():
        old_stamp = trash.entry_stamp(db.session.get(UndoStash, old))
    trash_action(home.client, old, 'restore', {'user': 'Priya'})
    # The trash no longer hands out an id twice; rewind its counter, as in a database whose upgrade failed
    with home.ctx():
        db.session.execute(db.text("UPDATE sqlite_sequence SET seq = :seq WHERE name = 'undo_stash'"), {'seq': old - 1})
        db.session.commit()
    home.client.post(f'/shopping/delete/{add_shopping(home, "Bread")}', data={'user': 'Priya'})
    assert entry_id(home, 'Bread') == old  # SQLite gave the free id to the new entry
    for action in ('delete', 'restore'):
        resp = home.client.post(f'/trash/{old}/{action}', data={'user': 'Priya', 'stamp': old_stamp}, follow_redirects=True)
        assert 'That is no longer in the trash.' in resp.get_data(as_text=True)
    assert [r[1] for r in trash_rows(home)] == ['Bread']
    # The page sends each entry's own stamp
    body = home.client.get('/trash').get_data(as_text=True)
    with home.ctx():
        assert f'name="stamp" value="{trash.entry_stamp(db.session.get(UndoStash, old))}"' in body


def test_restoring_a_rule_keeps_later_edits_to_its_history(home):
    """Kept as history, the entries stay editable: a restore must relink them as they are now,
    not bring back the copies from the time of the delete."""
    rid = make_rule(home)
    with home.ctx():
        days = {e.date: e.id for e in ExpenseEntry.query.filter_by(recurring_id=rid).all()}
    assert len(days) >= 3
    first, second, *rest = sorted(days)
    home.client.post(f'/expenses/recurring/delete/{rid}', data={'user': 'Priya'})
    with home.ctx():
        assert ExpenseEntry.query.filter(ExpenseEntry.recurring_id.isnot(None)).count() == 0
        # Edited afterwards...
        db.session.get(ExpenseEntry, days[first]).amount = 99.0
        db.session.commit()
    # ...and one deleted for good
    home.client.post(f'/expenses/delete/{days[second]}', data={'user': 'Priya'})
    trash_action(home.client, entry_id(home, f'Milk ({second.isoformat()})'), 'delete', {'user': 'Priya'})
    trash_action(home.client, entry_id(home, 'Milk'), 'restore', {'user': 'Priya'})
    with home.ctx():
        rule = RecurringExpense.query.one()
        linked = {e.date: e for e in ExpenseEntry.query.filter_by(recurring_id=rule.id).all()}
        assert linked[first].amount == 99.0
        assert second not in linked and db.session.get(ExpenseEntry, days[second]) is None
        assert all(d in linked for d in rest)
        assert ExpenseEntry.query.filter(ExpenseEntry.recurring_id.is_(None)).count() == 0


def test_day_finds_its_rule_after_the_rule_came_back_under_a_new_id(home):
    """The rule is restored first but another rule had taken its id (possible for entries made before ids
    were kept), so it comes back with a new id. Its deleted day must still join it, not become a one-off."""
    rid = make_rule(home)
    day = TODAY - timedelta(days=2)
    label = _delete_day(home, rid, day)
    home.client.post(f'/expenses/recurring/delete/{rid}', data={'user': 'Priya', 'delete_entries': '1'})
    with home.ctx():
        UndoStash.query.update({UndoStash.record_id: None})
        db.session.add(RecurringExpense(id=rid, title='Paper', unit_price=7.0, default_quantity=1.0, frequency='daily',
                                        start_date=TODAY, effective_from=TODAY, creator='Dad'))
        db.session.commit()
    trash_action(home.client, entry_id(home, 'Milk'), 'restore', {'user': 'Priya'})
    resp = trash_action(home.client, entry_id(home, label), 'restore', {'user': 'Priya'}, follow_redirects=True)
    assert 'one-off' not in resp.get_data(as_text=True)
    with home.ctx():
        milk = RecurringExpense.query.filter_by(title='Milk').one()
        assert milk.id != rid
        restored = ExpenseEntry.query.filter_by(date=day, title='Milk').one()
        assert restored.recurring_id == milk.id
        assert ExpenseEntry.query.filter_by(recurring_id=rid).count() == 0  # nothing joined Paper


def test_a_failing_restore_shows_a_generic_message(home, monkeypatch):
    """What went wrong inside a restore is logged, never sent to the browser (code scanning)."""
    item = add_shopping(home)
    flashes(home)
    home.client.post(f'/shopping/delete/{item}', data={'user': 'Priya'})
    token = undo_offer(home)['token']

    def broken(payload):
        raise RuntimeError('database file /app/data/app.db is locked')
    monkeypatch.setitem(undo._restorers, 'shopping_item', broken)
    resp = home.client.post(f'/undo/{token}')
    assert resp.status_code == 500 and resp.get_json() == {'ok': False, 'error': 'Could not undo that change.'}
    body = trash_action(home.client, entry_id(home, 'Milk'), 'restore', {'user': 'Priya'},
                        follow_redirects=True).get_data(as_text=True)
    assert 'Could not restore Milk.' in body and 'app.db is locked' not in body
    # A conflict shows only the message written for people
    def conflict(payload):
        raise undo.RestoreConflict('Milk was added back since it was deleted.')
    monkeypatch.setitem(undo._restorers, 'shopping_item', conflict)
    resp = home.client.post(f'/undo/{token}')
    assert resp.status_code == 409 and resp.get_json()['error'] == 'Milk was added back since it was deleted.'
    assert count(home, ShoppingItem) == 0 and len(trash_rows(home)) == 1


def test_long_owner_names_are_kept_whole(home):
    """Who may restore depends on the owner's name, so it must not be shortened: a 64-character prefix
    would lock the owner out and let a member whose name is that prefix in."""
    long_name = 'Priya ' + 'x' * 70
    prefix = long_name[:64]
    item = add_shopping(home, 'Milk', creator=long_name)
    home.client.post(f'/shopping/delete/{item}', data={'user': long_name})
    with home.ctx():
        assert trash.entries()[0].owner == long_name
    eid = entry_id(home, 'Milk')
    assert 'Not allowed to restore Milk.' in trash_action(home.client, eid, 'restore', {'user': prefix},
                                                          follow_redirects=True).get_data(as_text=True)
    trash_action(home.client, eid, 'restore', {'user': long_name})
    assert count(home, ShoppingItem) == 1


def test_day_finds_its_rule_after_a_rename_and_a_new_id(home):
    """The rule is renamed after its day is trashed, then deleted and restored under a new id: the day must
    still join it (by the rule's uid), not come back as a one-off."""
    rid = make_rule(home)
    day = TODAY - timedelta(days=2)
    label = _delete_day(home, rid, day)
    with home.ctx():
        rule = db.session.get(RecurringExpense, rid)
        rule.title = 'Cow milk'
        uid = rule.uid
        db.session.commit()
    assert uid
    home.client.post(f'/expenses/recurring/delete/{rid}', data={'user': 'Priya', 'delete_entries': '1'})
    with home.ctx():
        db.session.add(RecurringExpense(id=rid, title='Paper', unit_price=7.0, default_quantity=1.0, frequency='daily',
                                        start_date=TODAY, effective_from=TODAY, creator='Dad'))
        db.session.commit()
    trash_action(home.client, entry_id(home, 'Cow milk'), 'restore', {'user': 'Priya'})
    resp = trash_action(home.client, entry_id(home, label), 'restore', {'user': 'Priya'}, follow_redirects=True)
    assert 'one-off' not in resp.get_data(as_text=True)
    with home.ctx():
        rule = RecurringExpense.query.filter_by(uid=uid).one()
        assert (rule.title, rule.id != rid) == ('Cow milk', True)
        assert ExpenseEntry.query.filter_by(date=day, title='Milk').one().recurring_id == rule.id


def test_delete_forever_removes_only_the_entry_that_was_checked(home):
    """Between checking an entry and deleting it, another request can restore it and a new entry can take
    its id. The delete must then remove nothing, not the newer entry (possibly another member's)."""
    home.client.post(f'/shopping/delete/{add_shopping(home, "Milk")}', data={'user': 'Priya'})
    with home.ctx():
        checked = trash.get_entry(entry_id(home, 'Milk'))
        checked_id = checked.id
        db.session.expunge(checked)
        # Meanwhile: Milk is restored, and Dad's Bread lands in the trash under the same id
        db.session.execute(db.text('DELETE FROM undo_stash WHERE id = :id'), {'id': checked_id})
        db.session.commit()
        db.session.add(ShoppingItem(item='Bread', creator='Dad'))
        db.session.commit()
        bread = ShoppingItem.query.filter_by(item='Bread').one()
        undo.trash_row('shopping_item', bread, undo.trash_info('shopping', 'Bread', 'Dad', 'Dad'))
        replacement = trash.entries()[0]
        db.session.execute(db.text('UPDATE undo_stash SET id = :id WHERE id = :old'), {'id': checked_id, 'old': replacement.id})
        db.session.commit()
        db.session.expire_all()
        assert trash.delete_for_good(checked) is False
        with pytest.raises(undo.RestoreConflict):
            trash.restore(checked)
        assert [e.label for e in trash.entries()] == ['Bread']


def test_ids_stay_retired_after_delete_forever(home):
    """The review's case: a reminder trashed through the API, deleted for good, then a new reminder by the
    same owner. A retried DELETE of the old id must not reach the new reminder."""
    from app.models import Reminder
    deleter = home.token('Deleter', act_as=True, allow_delete=True, reminders='write')
    old = home.post('/reminders', deleter, json={'title': 'Dentist', 'date': TODAY.isoformat(), 'by': 'Priya'}).get_json()['id']
    assert home.delete(f'/reminders/{old}', deleter, json={'by': 'Priya'}).status_code == 200
    trash_action(home.client, entry_id(home, f'Dentist ({TODAY.isoformat()})'), 'delete', {'user': 'Priya'})
    assert trash_rows(home) == []
    new = home.post('/reminders', deleter, json={'title': 'Vet', 'date': TODAY.isoformat(), 'by': 'Priya'}).get_json()['id']
    assert new > old
    assert home.delete(f'/reminders/{old}', deleter, json={'by': 'Priya'}).status_code == 404
    with home.ctx():
        assert [r.title for r in Reminder.query.all()] == ['Vet']


def test_tables_with_a_trash_never_reuse_ids(home):
    """AUTOINCREMENT: SQLite hands out each id once, atomically, whatever is deleted later."""
    from app.migrations import AUTOINCREMENT_TABLES
    with home.ctx():
        for table in AUTOINCREMENT_TABLES:
            sql = db.session.execute(db.text("SELECT sql FROM sqlite_master WHERE name = :t"), {'t': table}).scalar()
            assert 'AUTOINCREMENT' in sql.upper(), table


def test_restoring_a_day_never_brings_back_someone_elses_rule(home):
    """A legacy expense day belongs to its payer (no created_by) while its rule is Priya's. Dad may restore
    the day, but not the rule with it: that would restart Priya's recurring charges without her."""
    rid = make_rule(home, user='Priya')
    day = TODAY - timedelta(days=2)
    with home.ctx():
        entry = ExpenseEntry.query.filter_by(recurring_id=rid, date=day).one()
        entry.created_by, entry.payer = None, 'Dad'
        db.session.commit()
        eid = entry.id
    home.client.post(f'/expenses/delete/{eid}', data={'user': 'Dad'})
    home.client.post(f'/expenses/recurring/delete/{rid}', data={'user': 'Priya', 'delete_entries': '1'})
    label = f'Milk ({day.isoformat()})'
    resp = trash_action(home.client, entry_id(home, label), 'restore', {'user': 'Dad'}, follow_redirects=True)
    assert 'Its recurring rule Milk is in the trash too, and only Priya or the admin can restore it.' in resp.get_data(as_text=True)
    with home.ctx():
        assert RecurringExpense.query.count() == 0 and ExpenseEntry.query.count() == 0
    assert sorted(r[1] for r in trash_rows(home)) == sorted([label, 'Milk'])
    # Once Priya has restored her rule, Dad's day joins it
    trash_action(home.client, entry_id(home, 'Milk'), 'restore', {'user': 'Priya'})
    trash_action(home.client, entry_id(home, label), 'restore', {'user': 'Dad'})
    with home.ctx():
        assert db.session.get(ExpenseEntry, eid).recurring_id == RecurringExpense.query.one().id


def test_the_admin_may_restore_a_day_with_its_rule(home):
    rid = make_rule(home, user='Priya')
    day = TODAY - timedelta(days=2)
    label = _delete_day(home, rid, day)
    home.client.post(f'/expenses/recurring/delete/{rid}', data={'user': 'Priya', 'delete_entries': '1'})
    resp = trash_action(home.client, entry_id(home, label), 'restore', {'user': 'Administrator'}, follow_redirects=True)
    assert 'Its recurring rule Milk was restored too.' in resp.get_data(as_text=True)


# --- Concurrency and transactions ---

def test_restore_takes_the_generation_lock_before_writing(home, monkeypatch):
    """Recurring generation takes its lock and then writes; a restore must take them in the same order
    (lock first), or two overlapping requests each wait for the other until the database gives up."""
    from app import recurrence
    seen = []
    real_claim = undo.claim_stash

    def claim(stash):
        seen.append(recurrence.generation_lock._is_owned())
        return real_claim(stash)
    monkeypatch.setattr(undo, 'claim_stash', claim)
    home.client.post(f'/shopping/delete/{add_shopping(home)}', data={'user': 'Priya'})
    trash_action(home.client, entry_id(home, 'Milk'), 'restore', {'user': 'Priya'})
    assert seen == [True] and count(home, ShoppingItem) == 1


def test_two_deletes_of_one_record_keep_one_copy(home):
    """Two requests delete the same item at once: only the one that removed the row keeps a trash copy,
    or the item could be restored twice (or survive Delete forever on one copy)."""
    item_id = add_shopping(home)
    with home.ctx():
        item = db.session.get(ShoppingItem, item_id)
        item.item  # this request has read the row...
        # ...and another request deletes it first (in this transaction, as if committed just before)
        db.session.execute(db.text('DELETE FROM shopping_item WHERE id = :id'), {'id': item_id})
        assert undo.trash_row('shopping_item', item, undo.trash_info('shopping', 'Milk', 'Priya', 'Priya')) is None
    assert trash_rows(home) == []
    # The web page says so instead of offering an Undo
    item_id = add_shopping(home, 'Bread')
    with home.ctx():
        db.session.execute(db.text('DELETE FROM shopping_item WHERE id = :id'), {'id': item_id})
        db.session.commit()
    assert home.client.post(f'/shopping/delete/{item_id}', data={'user': 'Priya'}).status_code == 404


def test_bulk_delete_with_an_impossible_id_still_deletes_all_and_offers_undo(home):
    """An id too big for SQLite used to stop the bulk delete half way: the first reminder was already
    gone, and the answer was a 500 with no Undo."""
    one, two = add_reminder(home, 'One'), add_reminder(home, 'Two')
    resp = home.client.delete('/api/reminders', json={'ids': [one, 2 ** 70, two, True, -1], 'creator': 'Priya'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['deleted'] == 2 and data['undo_token']
    assert home.client.post(f"/undo/{data['undo_token']}").get_json() == {'ok': True}
    assert calendar_titles(home) == ['One', 'Two']
    # The same for expenses
    ids = [add_expense(home, f'Item {i}') for i in range(2)]
    flashes(home)
    resp = home.client.post('/expenses/bulk-delete', data={'user': 'Priya', 'ids': [str(ids[0]), '9' * 30, str(ids[1])]})
    assert resp.status_code == 302
    assert undo_offer(home)['message'] == '2 expenses moved to the trash.'
    assert count(home, ExpenseEntry) == 0


def test_flask_trash_restore_never_reaches_a_later_entry(home):
    """An id from an old flask trash list must not restore a different entry made since (as the admin)."""
    runner = home.app.test_cli_runner()
    home.client.post(f'/shopping/delete/{add_shopping(home, "Milk")}', data={'user': 'Priya'})
    listed = entry_id(home, 'Milk')
    trash_action(home.client, listed, 'delete', {'user': 'Priya'})
    home.client.post(f'/shopping/delete/{add_shopping(home, "Bread", creator="Dad")}', data={'user': 'Dad'})
    assert entry_id(home, 'Bread') != listed
    result = runner.invoke(args=['trash', 'restore', str(listed)])
    assert result.exit_code != 0 and 'Nothing in the trash has ID' in result.output
    assert [r[1] for r in trash_rows(home)] == ['Bread']


def test_undo_restores_a_rule_for_an_owner_with_a_long_name(home):
    """The quick Undo restores for whoever deleted; with their name cut short, they were refused their own rule."""
    name = 'Priya ' + 'x' * 140
    rid = make_rule(home, user=name)
    day = TODAY - timedelta(days=2)
    with home.ctx():
        entry = ExpenseEntry.query.filter_by(recurring_id=rid, date=day).one()
        entry.created_by = name
        db.session.commit()
        eid = entry.id
    flashes(home)
    home.client.post(f'/expenses/delete/{eid}', data={'user': name})
    token = undo_offer(home)['token']
    home.client.post(f'/expenses/recurring/delete/{rid}', data={'user': name, 'delete_entries': '1'})
    with home.ctx():
        assert UndoStash.query.filter_by(token=token).one().deleted_by == name
    assert home.client.post(f'/undo/{token}').get_json() == {'ok': True}
    with home.ctx():
        assert RecurringExpense.query.one().creator == name
        assert db.session.get(ExpenseEntry, eid).recurring_id == RecurringExpense.query.one().id



@pytest.mark.parametrize('kind', ['chore', 'expense'])
def test_deleting_a_recurring_rule_holds_the_generation_lock(home, monkeypatch, kind):
    """The rule's snapshot and its delete happen under the generation lock: a day or chore generated in
    between would otherwise be deleted without a copy in the trash."""
    from app import recurrence
    from app.blueprints import chores as chores_web
    from app.blueprints import expenses as expenses_web
    seen = []
    module = chores_web if kind == 'chore' else expenses_web
    real = module.delete_exactly

    def watched(row):
        seen.append(recurrence.generation_lock._is_owned())
        return real(row)
    monkeypatch.setattr(module, 'delete_exactly', watched)
    if kind == 'chore':
        home.client.post('/chores', data={'description': 'Bins', 'creator': 'Dad', 'is_recurring': '1', 'rec_interval': '1',
                                          'rec_unit': 'week', 'rec_start_date': TODAY.isoformat()})
        with home.ctx():
            rid = RecurringChore.query.one().id
        home.client.post(f'/chores/recurring/delete/{rid}', data={'user': 'Dad'})
    else:
        rid = make_rule(home)
        home.client.post(f'/expenses/recurring/delete/{rid}', data={'user': 'Priya', 'delete_entries': '1'})
    assert seen == [True] and len(trash_rows(home)) == 1


def test_a_day_is_not_restored_beside_its_replacement_after_a_split(home):
    """The rule was split after the day was deleted: the new rule generated a replacement for that day.
    Restoring the old day as well would count the expense twice."""
    rid = make_rule(home)
    day = TODAY - timedelta(days=1)
    label = _delete_day(home, rid, day)
    home.client.post(f'/expenses/recurring/edit/{rid}', data={
        'user': 'Priya', 'title': 'Milk', 'category': '', 'unit_price': '32', 'default_quantity': '2', 'frequency': 'daily',
        'monthly_mode': 'day_of_month', 'start_date': (TODAY - timedelta(days=3)).isoformat(), 'end_date': '',
        'edit_strategy': 'split_rule', 'effective_from': (TODAY - timedelta(days=2)).isoformat()})
    home.client.get('/expenses/recurring')
    with home.ctx():
        assert RecurringExpense.query.count() == 2
        on_day = ExpenseEntry.query.filter_by(date=day).all()
        assert len(on_day) == 1 and on_day[0].recurring_id != rid  # the replacement
    resp = trash_action(home.client, entry_id(home, label), 'restore', {'user': 'Priya'}, follow_redirects=True)
    assert 'no longer part of its recurring rule' in resp.get_data(as_text=True)
    with home.ctx():
        assert ExpenseEntry.query.filter_by(date=day).count() == 1
    assert len(trash_rows(home)) == 1
