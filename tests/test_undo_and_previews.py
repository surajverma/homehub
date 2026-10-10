"""Dry-run previews, undo, and edits that keep the past as it was."""
from datetime import date, datetime, timedelta

import pytest

from app import create_app, db
from app.clock import utcnow
from app.models import Chore, ExpenseEntry, RecurringChore, RecurringExpense, RecurringReminder, UndoStash

TODAY = date.today()


def days_ago(n):
    return TODAY - timedelta(days=n)


@pytest.fixture()
def client():
    app = create_app({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite://',
        'HOMEHUB_CONFIG': {
            'admin_name': 'Administrator',
            'family_members': ['Alice', 'Bob'],
        },
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'test',
    })
    with app.app_context():
        db.create_all()
        db.session.execute(db.text("CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY, value TEXT)"))
        db.session.commit()
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['authed'] = True
    return c


def make_rule(client, frequency='daily', start=None, **extra):
    """A milk rule with its entries generated up to today; returns its id."""
    with client.application.app_context():
        rule = RecurringExpense(
            title='Milk', category='Food', unit_price=10.0, default_quantity=2.0, frequency=frequency,
            monthly_mode='day_of_month', start_date=start or days_ago(6), creator='Alice',
            effective_from=start or days_ago(6), **extra,
        )
        db.session.add(rule)
        db.session.commit()
        rid = rule.id
    client.get('/expenses/recurring')
    return rid


def edit_form(**overrides):
    form = {
        'user': 'Alice', 'title': 'Milk', 'category': 'Food', 'unit_price': '10', 'default_quantity': '2',
        'frequency': 'daily', 'monthly_mode': 'day_of_month', 'start_date': days_ago(6).isoformat(),
        'end_date': '', 'edit_strategy': 'apply_from', 'effective_from': TODAY.isoformat(),
    }
    form.update(overrides)
    return form


def entries(client, rid):
    with client.application.app_context():
        rows = ExpenseEntry.query.filter_by(recurring_id=rid).order_by(ExpenseEntry.date).all()
        return [(e.date, e.quantity, e.amount, e.title) for e in rows]


def last_token(client):
    with client.application.app_context():
        return UndoStash.query.order_by(UndoStash.id.desc()).first().token


# --- Phase 1: effective-from default ---------------------------------------------------------

def test_effective_from_defaults_to_today(client):
    rid = make_rule(client)
    html = client.get('/expenses/recurring').get_data(as_text=True)
    assert f'id="rule-{rid}-effective-from" type="date" name="effective_from" class="w-full border rounded p-2" value="{TODAY.isoformat()}"' in html


def test_effective_from_default_is_clamped_to_rule_window(client):
    ended = make_rule(client, start=days_ago(20), end_date=days_ago(5))
    upcoming = make_rule(client, start=TODAY + timedelta(days=3))
    html = client.get('/expenses/recurring').get_data(as_text=True)
    assert f'id="rule-{ended}-effective-from" type="date" name="effective_from" class="w-full border rounded p-2" value="{days_ago(5).isoformat()}"' in html
    assert f'id="rule-{upcoming}-effective-from" type="date" name="effective_from" class="w-full border rounded p-2" value="{(TODAY + timedelta(days=3)).isoformat()}"' in html


# --- Phase 3: dry run ------------------------------------------------------------------------

def test_preview_from_today_touches_nothing_past(client):
    rid = make_rule(client)
    before = entries(client, rid)
    data = client.post(f'/expenses/recurring/edit/{rid}/preview', data=edit_form(default_quantity='3')).get_json()
    assert data['ok'] is True
    assert (data['changed'], data['removed'], data['added'], data['past'], data['hand_edited']) == (1, 0, 0, 0, 0)
    assert data['first_date'] == TODAY.isoformat()
    assert data['summary']
    # A dry run changes nothing and leaves nothing to undo
    assert entries(client, rid) == before
    with client.application.app_context():
        assert UndoStash.query.count() == 0


def test_preview_counts_past_and_hand_edited_entries(client):
    rid = make_rule(client)
    with client.application.app_context():
        edited = ExpenseEntry.query.filter_by(recurring_id=rid, date=days_ago(2)).first()
        edited.quantity, edited.amount = 5.0, 50.0
        db.session.commit()
    data = client.post(f'/expenses/recurring/edit/{rid}/preview',
                       data=edit_form(default_quantity='3', effective_from=days_ago(3).isoformat())).get_json()
    assert (data['changed'], data['past'], data['hand_edited']) == (4, 3, 1)
    assert data['first_date'] == days_ago(3).isoformat()
    assert '4' in data['summary'] and days_ago(3).isoformat() in data['summary']
    assert len(data['details']) == 1


def test_preview_reports_no_change_when_values_are_the_same(client):
    rid = make_rule(client)
    data = client.post(f'/expenses/recurring/edit/{rid}/preview',
                       data=edit_form(effective_from=days_ago(4).isoformat())).get_json()
    assert (data['changed'], data['removed'], data['added'], data['past']) == (0, 0, 0, 0)
    # The page still confirms the save, so there is always something to show
    assert days_ago(4).isoformat() in data['summary']


def test_preview_rewrite_all_counts_entries_outside_the_new_range(client):
    rid = make_rule(client)
    data = client.post(f'/expenses/recurring/edit/{rid}/preview',
                       data=edit_form(edit_strategy='rewrite_all', start_date=days_ago(3).isoformat(),
                                      default_quantity='4')).get_json()
    assert data['strategy'] == 'rewrite_all'
    assert (data['removed'], data['changed']) == (3, 4)


def test_preview_matches_what_the_edit_then_does(client):
    rid = make_rule(client)
    with client.application.app_context():
        # A day the user deleted comes back when the rule is rebuilt over it
        ExpenseEntry.query.filter_by(recurring_id=rid, date=days_ago(1)).delete()
        db.session.commit()
    form = edit_form(default_quantity='3', effective_from=days_ago(2).isoformat())
    data = client.post(f'/expenses/recurring/edit/{rid}/preview', data=form).get_json()
    assert (data['changed'], data['added']) == (2, 1)
    client.post(f'/expenses/recurring/edit/{rid}', data=form)
    rows = entries(client, rid)
    assert [q for d, q, _a, _t in rows if d >= days_ago(2)] == [3.0, 3.0, 3.0]
    assert [q for d, q, _a, _t in rows if d < days_ago(2)] == [2.0] * 4


def test_preview_requires_permission(client):
    rid = make_rule(client)
    resp = client.post(f'/expenses/recurring/edit/{rid}/preview', data=edit_form(user='Bob'))
    assert resp.status_code == 403
    resp = client.post(f'/expenses/recurring/delete/{rid}/preview', data={'user': 'Bob'})
    assert resp.status_code == 403


def test_delete_preview_counts_entries(client):
    rid = make_rule(client)
    keep = client.post(f'/expenses/recurring/delete/{rid}/preview', data={'user': 'Alice'}).get_json()
    assert (keep['entries'], keep['removed']) == (7, 0)
    drop = client.post(f'/expenses/recurring/delete/{rid}/preview',
                       data={'user': 'Alice', 'delete_entries': '1'}).get_json()
    assert (drop['entries'], drop['removed']) == (7, 7)
    assert days_ago(6).isoformat() in drop['summary']
    assert len(entries(client, rid)) == 7


def test_weekly_rule_keeps_its_weekday_after_an_edit_from_today(client):
    start = days_ago(17)
    rid = make_rule(client, frequency='weekly', start=start)
    assert [d for d, *_ in entries(client, rid)] == [start, start + timedelta(days=7), start + timedelta(days=14)]
    client.post(f'/expenses/recurring/edit/{rid}',
                data=edit_form(frequency='weekly', start_date=start.isoformat(), default_quantity='3'))
    with client.application.app_context():
        rule = db.session.get(RecurringExpense, rid)
        # Generation resumes from the last scheduled day, so the next one is a week after it
        assert rule.last_generated_date == start + timedelta(days=14)
    assert [d for d, *_ in entries(client, rid)] == [start, start + timedelta(days=7), start + timedelta(days=14)]


# --- Phase 4: undo ---------------------------------------------------------------------------

def test_undo_restores_rule_and_entries_after_an_edit(client):
    rid = make_rule(client)
    before = entries(client, rid)
    resp = client.post(f'/expenses/recurring/edit/{rid}',
                       data=edit_form(title='Curd', default_quantity='3', effective_from=days_ago(3).isoformat()),
                       follow_redirects=True)
    assert 'id="undoOffers"' in resp.get_data(as_text=True)
    assert entries(client, rid) != before
    token = last_token(client)
    assert client.post(f'/undo/{token}').get_json() == {'ok': True}
    assert entries(client, rid) == before
    with client.application.app_context():
        rule = db.session.get(RecurringExpense, rid)
        assert (rule.title, rule.default_quantity, rule.effective_from) == ('Milk', 2.0, days_ago(6))
        assert UndoStash.query.count() == 0
    # A token works once
    assert client.post(f'/undo/{token}').status_code == 410


def test_undo_after_split_removes_the_new_rule(client):
    rid = make_rule(client)
    before = entries(client, rid)
    client.post(f'/expenses/recurring/edit/{rid}',
                data=edit_form(edit_strategy='split_rule', default_quantity='3', effective_from=days_ago(2).isoformat()))
    with client.application.app_context():
        assert RecurringExpense.query.count() == 2
    assert client.post(f'/undo/{last_token(client)}').get_json()['ok'] is True
    with client.application.app_context():
        assert RecurringExpense.query.count() == 1
        assert db.session.get(RecurringExpense, rid).end_date is None
    assert entries(client, rid) == before


def test_undo_after_rewrite_all_brings_back_removed_entries(client):
    rid = make_rule(client)
    before = entries(client, rid)
    client.post(f'/expenses/recurring/edit/{rid}',
                data=edit_form(edit_strategy='rewrite_all', start_date=days_ago(2).isoformat(), default_quantity='9'))
    assert len(entries(client, rid)) == 3
    client.post(f'/undo/{last_token(client)}')
    assert entries(client, rid) == before


@pytest.mark.parametrize('delete_entries', ['1', ''])
def test_undo_restores_a_deleted_rule(client, delete_entries):
    rid = make_rule(client)
    before = entries(client, rid)
    client.post(f'/expenses/recurring/delete/{rid}', data={'user': 'Alice', 'delete_entries': delete_entries})
    with client.application.app_context():
        assert db.session.get(RecurringExpense, rid) is None
    assert client.post(f'/undo/{last_token(client)}').get_json()['ok'] is True
    with client.application.app_context():
        assert db.session.get(RecurringExpense, rid).title == 'Milk'
    assert entries(client, rid) == before


def test_undo_expires_and_is_purged(client):
    rid = make_rule(client)
    client.post(f'/expenses/recurring/edit/{rid}', data=edit_form(default_quantity='3'))
    token = last_token(client)
    with client.application.app_context():
        UndoStash.query.update({UndoStash.expires_at: utcnow() - timedelta(seconds=1)})
        db.session.commit()
    resp = client.post(f'/undo/{token}')
    assert resp.status_code == 410 and resp.get_json()['ok'] is False
    with client.application.app_context():
        assert UndoStash.query.count() == 0
    assert entries(client, rid)[-1][1] == 3.0


def test_unknown_undo_token_is_rejected(client):
    assert client.post('/undo/nope').status_code == 410


def make_recurring_chore(client, start, unit='week', end=None):
    client.post('/chores', data={
        'description': 'Water plants', 'creator': 'Alice', 'tags': '[]', 'is_recurring': 'on',
        'rec_interval': '1', 'rec_unit': unit, 'rec_start_date': start.isoformat(),
        'rec_end_date': end.isoformat() if end else '',
    })
    with client.application.app_context():
        return Chore.query.first().id


def chore_state(client):
    with client.application.app_context():
        chore = Chore.query.first()
        return chore.due_date, bool(chore.done)


def test_recurring_chore_done_survives_the_next_page_load(client):
    cid = make_recurring_chore(client, TODAY)
    client.post(f'/chores/toggle/{cid}')
    client.get('/chores')
    assert chore_state(client) == (TODAY + timedelta(days=7), False)


def test_recurring_chore_done_can_be_undone(client):
    cid = make_recurring_chore(client, TODAY)
    resp = client.post(f'/chores/toggle/{cid}', follow_redirects=True)
    assert 'id="undoOffers"' in resp.get_data(as_text=True)
    assert client.post(f'/undo/{last_token(client)}').get_json()['ok'] is True
    client.get('/chores')
    assert chore_state(client) == (TODAY, False)
    with client.application.app_context():
        assert RecurringChore.query.first().last_generated_date == TODAY


def test_last_date_of_a_recurring_chore_stays_done_and_can_be_undone(client):
    cid = make_recurring_chore(client, TODAY, end=TODAY + timedelta(days=3))
    client.post(f'/chores/toggle/{cid}')
    client.get('/chores')
    assert chore_state(client) == (TODAY, True)
    client.post(f'/undo/{last_token(client)}')
    client.get('/chores')
    assert chore_state(client) == (TODAY, False)


def test_plain_chore_toggle_offers_no_undo(client):
    client.post('/chores', data={'description': 'Dust', 'creator': 'Alice', 'tags': '[]'})
    with client.application.app_context():
        cid = Chore.query.first().id
    client.post(f'/chores/toggle/{cid}')
    with client.application.app_context():
        assert UndoStash.query.count() == 0


# --- Phase 5: recurring reminders keep their past --------------------------------------------

def make_reminder_rule(client, start, **recurring):
    resp = client.post('/api/reminders', json={
        'date': start.isoformat(), 'title': 'Pay rent', 'description': '', 'creator': 'Alice',
        'recurring': dict({'interval': 1, 'unit': 'day'}, **recurring),
    })
    return resp.get_json()['recurring_id']


def occurrences(client, day):
    data = client.get(f'/api/reminders?scope=day&date={day.isoformat()}').get_json()
    return [r['title'] for r in data['reminders']]


def test_editing_a_started_reminder_rule_applies_from_today(client):
    rid = make_reminder_rule(client, days_ago(10))
    resp = client.patch(f'/api/recurring_rules/{rid}', json={'creator': 'Alice', 'title': 'Pay landlord'}).get_json()
    assert resp['ok'] is True and resp['split'] is True
    assert resp['rule']['id'] != rid and resp['rule']['start_date'] == TODAY.isoformat()
    assert occurrences(client, days_ago(10)) == ['Pay rent']
    assert occurrences(client, days_ago(1)) == ['Pay rent']
    assert occurrences(client, TODAY) == ['Pay landlord']
    assert occurrences(client, TODAY + timedelta(days=5)) == ['Pay landlord']
    with client.application.app_context():
        assert db.session.get(RecurringReminder, rid).end_date == days_ago(1)


def test_edited_weekly_reminder_keeps_its_weekday(client):
    start = days_ago(10)
    rid = make_reminder_rule(client, start, unit='week')
    resp = client.patch(f'/api/recurring_rules/{rid}', json={
        'creator': 'Alice', 'title': 'Pay landlord', 'start_date': start.isoformat(), 'interval': 1, 'unit': 'week',
    }).get_json()
    assert resp['rule']['start_date'] == (start + timedelta(days=14)).isoformat()
    assert occurrences(client, start + timedelta(days=7)) == ['Pay rent']
    assert occurrences(client, start + timedelta(days=14)) == ['Pay landlord']


def test_saving_a_reminder_rule_unchanged_does_not_split_it(client):
    start = days_ago(10)
    rid = make_reminder_rule(client, start)
    resp = client.patch(f'/api/recurring_rules/{rid}', json={
        'creator': 'Alice', 'title': 'Pay rent', 'description': '', 'start_date': start.isoformat(),
        'interval': 1, 'unit': 'day',
    }).get_json()
    assert resp['split'] is False
    with client.application.app_context():
        assert RecurringReminder.query.count() == 1


def test_reminder_rule_that_has_not_started_is_edited_in_place(client):
    rid = make_reminder_rule(client, TODAY + timedelta(days=2))
    resp = client.patch(f'/api/recurring_rules/{rid}', json={'creator': 'Alice', 'title': 'Pay landlord'}).get_json()
    assert resp['split'] is False and resp['rule']['id'] == rid
    with client.application.app_context():
        assert RecurringReminder.query.count() == 1


def test_changing_only_the_end_date_is_done_in_place(client):
    rid = make_reminder_rule(client, days_ago(10))
    resp = client.patch(f'/api/recurring_rules/{rid}', json={'creator': 'Alice', 'end_date': days_ago(6).isoformat()}).get_json()
    assert resp['split'] is False and resp['rule']['end_date'] == days_ago(6).isoformat()
    assert occurrences(client, days_ago(7)) == ['Pay rent']
    assert occurrences(client, TODAY) == []


def test_deleting_a_started_reminder_rule_keeps_past_dates(client):
    rid = make_reminder_rule(client, days_ago(10))
    resp = client.delete(f'/api/recurring_rules/{rid}', json={'creator': 'Alice'}).get_json()
    assert (resp['ok'], resp['ended']) == (True, True)
    assert occurrences(client, days_ago(10)) == ['Pay rent']
    assert occurrences(client, days_ago(1)) == ['Pay rent']
    assert occurrences(client, TODAY) == []
    assert occurrences(client, TODAY + timedelta(days=3)) == []


def test_deleting_a_reminder_rule_that_has_not_started_removes_it(client):
    rid = make_reminder_rule(client, TODAY)
    resp = client.delete(f'/api/recurring_rules/{rid}', json={'creator': 'Alice'}).get_json()
    assert (resp['ok'], resp['ended']) == (True, False)
    with client.application.app_context():
        assert RecurringReminder.query.count() == 0


def test_scope_all_removes_a_reminder_rule_with_its_past(client):
    rid = make_reminder_rule(client, days_ago(10))
    client.delete(f'/api/recurring_rules/{rid}', json={'creator': 'Alice', 'scope': 'all'})
    assert occurrences(client, days_ago(3)) == []


def test_reminder_rule_changes_require_permission(client):
    rid = make_reminder_rule(client, days_ago(10))
    assert client.patch(f'/api/recurring_rules/{rid}', json={'creator': 'Bob', 'title': 'x'}).status_code == 403
    assert client.delete(f'/api/recurring_rules/{rid}', json={'creator': 'Bob'}).status_code == 403
    assert occurrences(client, TODAY) == ['Pay rent']


def test_reminder_rule_edit_can_be_undone(client):
    rid = make_reminder_rule(client, days_ago(10))
    resp = client.patch(f'/api/recurring_rules/{rid}', json={'creator': 'Alice', 'title': 'Pay landlord'}).get_json()
    assert client.post(f"/undo/{resp['undo_token']}").get_json()['ok'] is True
    assert occurrences(client, TODAY) == ['Pay rent']
    with client.application.app_context():
        assert RecurringReminder.query.count() == 1
        assert db.session.get(RecurringReminder, rid).end_date is None


def test_reminder_rule_edit_in_place_can_be_undone(client):
    rid = make_reminder_rule(client, TODAY + timedelta(days=2))
    resp = client.patch(f'/api/recurring_rules/{rid}', json={'creator': 'Alice', 'title': 'Pay landlord'}).get_json()
    client.post(f"/undo/{resp['undo_token']}")
    assert occurrences(client, TODAY + timedelta(days=2)) == ['Pay rent']


@pytest.mark.parametrize('scope', [None, 'all'])
def test_reminder_rule_delete_can_be_undone(client, scope):
    rid = make_reminder_rule(client, days_ago(10))
    resp = client.delete(f'/api/recurring_rules/{rid}', json={'creator': 'Alice', 'scope': scope}).get_json()
    assert occurrences(client, TODAY) == []
    assert client.post(f"/undo/{resp['undo_token']}").get_json()['ok'] is True
    assert occurrences(client, days_ago(3)) == ['Pay rent']
    assert occurrences(client, TODAY) == ['Pay rent']


def test_unchanged_reminder_rule_offers_no_undo(client):
    rid = make_reminder_rule(client, days_ago(10))
    resp = client.patch(f'/api/recurring_rules/{rid}', json={'creator': 'Alice', 'title': 'Pay rent'}).get_json()
    assert 'undo_token' not in resp


def test_recurring_chore_edit_can_be_undone(client):
    cid = make_recurring_chore(client, TODAY)
    with client.application.app_context():
        rule_id = RecurringChore.query.first().id
    resp = client.post('/chores', data={
        'description': 'Water the garden', 'creator': 'Alice', 'user': 'Alice', 'tags': '[]', 'is_recurring': 'on',
        'rec_interval': '2', 'rec_unit': 'day', 'rec_start_date': (TODAY + timedelta(days=1)).isoformat(),
        'rec_end_date': '', 'chore_id': str(cid), 'recurring_rule_id': str(rule_id),
    }, follow_redirects=True)
    assert 'id="undoOffers"' in resp.get_data(as_text=True)
    assert chore_state(client) == (TODAY + timedelta(days=1), False)
    assert client.post(f'/undo/{last_token(client)}').get_json()['ok'] is True
    with client.application.app_context():
        rule = db.session.get(RecurringChore, rule_id)
        assert (rule.description, rule.interval, rule.unit, rule.start_date) == ('Water plants', 1, 'week', TODAY)
        assert Chore.query.first().description == 'Water plants'
    assert chore_state(client) == (TODAY, False)


@pytest.mark.parametrize('route', ['/chores/recurring/delete/{rule}', '/chores/delete/{chore}'])
def test_recurring_chore_delete_can_be_undone(client, route):
    cid = make_recurring_chore(client, TODAY)
    with client.application.app_context():
        rule_id = RecurringChore.query.first().id
    client.post(route.format(rule=rule_id, chore=cid), data={'user': 'Alice'})
    with client.application.app_context():
        assert RecurringChore.query.count() == 0 and Chore.query.count() == 0
    assert client.post(f'/undo/{last_token(client)}').get_json()['ok'] is True
    with client.application.app_context():
        assert db.session.get(RecurringChore, rule_id).description == 'Water plants'
    assert chore_state(client) == (TODAY, False)


# --- Phase 6: sidebar ------------------------------------------------------------------------

def test_sidebar_is_one_flat_list_in_its_usual_order(client):
    html = client.get('/chores').get_data(as_text=True)
    nav = html.split('id="sidebarNav">')[1].split('</nav>')[0]
    hrefs = [part.split('"')[0] for part in nav.split('<a href="')[1:]]
    # The example config leaves the URL shortener off, so its link is not there
    assert hrefs == ['/', '/notes', '/upload', '/shopping', '/calendar', '/chores', '/recipes', '/expiry',
                     '/media', '/pdfs', '/qr', '/expenses']
    assert 'nav-group' not in nav
