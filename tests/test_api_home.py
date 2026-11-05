"""API: who is home, personal status, the notice board and reminders; the acting person ("by")."""
from datetime import date, datetime, timedelta

import pytest

from api_support import Home
from app.models import ApiActivity, HomeStatus, MemberStatus, Notice, RecurringReminder, Reminder, db


@pytest.fixture()
def home(tmp_path, monkeypatch):
    return Home(tmp_path, monkeypatch)


@pytest.fixture()
def agent(home):
    return home.all_tools('Hermes')


def add_reminder(home, title='Dentist', d=None, creator='Priya', **kw):
    with home.ctx():
        r = Reminder(date=d or date.today(), start_date=d or date.today(), title=title, creator=creator, **kw)
        db.session.add(r)
        db.session.commit()
        return r.id


# --- Permissions ---

def test_no_access_read_only_and_write(home):
    reader = home.token('Reader', presence='read')
    nothing = home.token('Nothing', notice='read')
    assert home.get('/presence', reader).status_code == 200
    resp = home.put('/presence/Priya', reader, json={'status': 'home'})
    assert resp.status_code == 403 and resp.get_json()['error'] == 'forbidden'
    assert 'read' in resp.get_json()['message']
    assert home.get('/presence', nothing).status_code == 403
    writer = home.token('Writer', act_as=True, presence='write')
    assert home.get('/presence', writer).status_code == 200  # write includes read
    assert home.put('/presence/Priya', writer, json={'status': 'home'}).status_code == 200


def test_switched_off_tool_answers_404_and_comes_back(home, agent):
    home.toggle('who_is_home', False)
    resp = home.get('/presence', agent)
    assert resp.status_code == 404 and resp.get_json()['error'] == 'not_found'
    assert home.put('/presence/Priya', agent, json={'status': 'home'}).status_code == 404
    home.toggle('who_is_home', True)
    assert home.get('/presence', agent).status_code == 200


def test_unknown_fields_are_rejected(home, agent):
    resp = home.put('/presence/Priya', agent, json={'status': 'home', 'colour': 'red'})
    assert resp.status_code == 400 and 'colour' in resp.get_json()['message']
    assert home.get('/presence?extra=1', agent).status_code == 400
    assert home.get('/reminders?scope=today&scope=week', agent).status_code == 400


# --- Who is home ---

def test_presence(home, agent):
    resp = home.put('/presence/priya', agent, json={'status': 'HOME', 'by': 'dad'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['name'] == 'Priya' and data['status'] == 'home' and data['summary'] == 'Priya is home.'
    with home.ctx():
        assert HomeStatus.query.filter_by(name='Priya').one().status == 'Home'
        row = ApiActivity.query.one()
        assert (row.token_name, row.acting_as, row.action) == ('Hermes', 'Dad', 'presence_set')
    # Repeating it changes nothing
    assert home.put('/presence/Priya', agent, json={'status': 'home'}).get_json()['status'] == 'home'
    people = home.get('/presence', agent).get_json()['people']
    assert people == [
        {'name': 'Priya', 'status': 'home', 'label': 'Home'},
        {'name': 'Dad', 'status': None, 'label': None},
        {'name': 'Mom', 'status': None, 'label': None},
    ]
    # The Welcome page shows it like a change made there
    assert 'Home' in home.client.get('/').get_data(as_text=True)


def test_presence_rejects_unknown_people_and_statuses(home, agent):
    assert home.put('/presence/Stranger', agent, json={'status': 'home'}).status_code == 404
    assert home.put('/presence/Administrator', agent, json={'status': 'home'}).status_code == 404
    assert home.put('/presence/Priya', agent, json={'status': 'sleeping'}).status_code == 400
    assert home.put('/presence/Priya', agent, json={}).status_code == 400


def test_web_who_is_home_unchanged(home):
    resp = home.client.post('/whoishome', data={'name': 'Mom', 'status': 'Traveling'}, headers={'X-Requested-With': 'fetch'})
    assert resp.get_json() == {'ok': True, 'who_statuses': {'Mom': 'Traveling'}, 'member_statuses': {}, 'result': 'updated'}


# --- Personal status ---

def test_status_set_and_clear(home, agent):
    resp = home.put('/statuses/me', agent, json={'text': 'Working from home', 'by': 'Mom'})
    assert resp.status_code == 200
    assert resp.get_json()['summary'] == 'Status for Mom: Working from home'
    assert home.get('/statuses', agent).get_json()['statuses'][2] == {'name': 'Mom', 'text': 'Working from home'}
    resp = home.put('/statuses/me', agent, json={'text': '', 'by': 'Mom'})
    assert resp.get_json()['text'] is None
    with home.ctx():
        assert MemberStatus.query.count() == 0


def test_status_needs_by(home, agent):
    resp = home.put('/statuses/me', agent, json={'text': 'Hi'})
    assert resp.status_code == 400 and 'by' in resp.get_json()['message']


# --- The acting person ---

def test_by_must_be_a_family_member(home, agent):
    resp = home.post('/notes', agent, json={'content': 'x', 'by': 'Stranger'})
    assert resp.status_code == 400
    resp = home.put('/presence/Priya', agent, json={'status': 'home', 'by': 'Stranger'})
    assert resp.status_code == 400 and 'Stranger' in resp.get_json()['message']


@pytest.mark.parametrize('by', ['Administrator', 'administrator', 'admin'])
def test_admin_can_never_be_claimed(home, agent, by):
    resp = home.put('/presence/Priya', agent, json={'status': 'home', 'by': by})
    assert resp.status_code == 400 and 'admin' in resp.get_json()['message']


def test_admin_cannot_be_claimed_even_as_family(tmp_path, monkeypatch):
    home = Home(tmp_path, monkeypatch, family_members=['Priya', 'Administrator'])
    secret = home.all_tools('Hermes')
    resp = home.put('/presence/Priya', secret, json={'status': 'home', 'by': 'Administrator'})
    assert resp.status_code == 400


def test_by_not_allowed_without_act_as(home):
    secret = home.token('Script', reminders='write')
    resp = home.post('/reminders', secret, json={'title': 'X', 'date': date.today().isoformat(), 'by': 'Priya'})
    assert resp.status_code == 400 and 'by' in resp.get_json()['message']


def test_token_name_is_the_creator_without_by(home):
    secret = home.token('Script', reminders='write')
    resp = home.post('/reminders', secret, json={'title': 'Bins', 'date': date.today().isoformat()})
    assert resp.status_code == 201 and resp.get_json()['creator'] == 'Script'


# --- Notice board ---

def test_notice(home, agent):
    assert home.get('/notice', agent).get_json() == {'content': '', 'updated_by': None, 'updated_at': None}
    with home.ctx():
        db.session.add(Notice(content='<b>Party</b> on Sunday', updated_by='Administrator', updated_at=datetime(2026, 10, 1, 9, 0)))
        db.session.commit()
    assert home.get('/notice', agent).get_json() == {
        'content': '<b>Party</b> on Sunday', 'updated_by': 'Administrator', 'updated_at': '2026-10-01T09:00:00'}
    assert home.post('/notice', agent, json={'content': 'x'}).status_code == 405


# --- Reminders ---

def web(home, scope, d):
    data = home.client.get(f'/api/reminders?scope={scope}&date={d.isoformat()}').get_json()
    return [(r['id'], r['date'], r['title']) for r in data['reminders']]


def api(home, secret, query):
    data = home.get('/reminders' + query, secret).get_json()
    return [(r['id'], r['date'], r['title']) for r in data['reminders']]


def test_reminders_match_the_calendar(home, agent):
    today = date.today()
    add_reminder(home, 'Today', today)
    add_reminder(home, 'Tomorrow', today + timedelta(days=1))
    trashed = add_reminder(home, 'Trashed', today)
    with home.ctx():
        db.session.get(Reminder, trashed).deleted_at = datetime.now()
        db.session.add(RecurringReminder(title='Daily pills', creator='Mom', interval=1, unit='day', start_date=today - timedelta(days=3)))
        db.session.commit()
    assert api(home, agent, '') == web(home, 'day', today)
    assert api(home, agent, '?scope=today') == web(home, 'day', today)
    assert api(home, agent, '?scope=week') == web(home, 'week', today)
    titles = [t for _i, _d, t in api(home, agent, '?scope=week')]
    assert 'Trashed' not in titles and 'Daily pills' in titles
    recurring = [r for r in home.get('/reminders', agent).get_json()['reminders'] if r['recurring_id']]
    assert recurring and recurring[0]['id'] < 0


def test_reminders_month_end_clamp(home, agent):
    with home.ctx():
        db.session.add(RecurringReminder(title='Rent', creator='Dad', interval=1, unit='month', start_date=date(2026, 1, 31)))
        db.session.commit()
    week = web(home, 'week', date(2026, 2, 28))
    assert [d for _i, d, _t in week] == ['2026-02-28']
    assert api(home, agent, '?from=2026-02-23&to=2026-03-01') == week
    assert api(home, agent, '?from=2026-04-01&to=2026-04-30') == web(home, 'month', date(2026, 4, 1))


def test_reminder_range_rules(home, agent):
    assert home.get('/reminders?from=2026-01-01&to=2026-03-03', agent).status_code == 200  # 62 days, the most
    resp = home.get('/reminders?from=2026-01-01&to=2026-03-04', agent)
    assert resp.status_code == 400 and resp.get_json()['error'] == 'bad_request'
    assert home.get('/reminders?from=2026-01-02&to=2026-01-01', agent).status_code == 400
    assert home.get('/reminders?from=2026-01-01', agent).status_code == 400
    assert home.get('/reminders?scope=week&from=2026-01-01&to=2026-01-02', agent).status_code == 400
    assert home.get('/reminders?from=01/02/2026&to=2026-01-03', agent).status_code == 400
    assert home.get('/reminders?scope=month', agent).status_code == 400


def test_add_reminder(home, agent):
    resp = home.post('/reminders', agent, json={'title': 'Vet', 'date': '2026-10-26', 'time': '09:30',
                                                'category': 'health', 'by': 'Priya', 'description': '<script>x</script>Bring card'})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data['creator'] == 'Priya' and data['time'] == '09:30' and data['category'] == 'health'
    assert '<script>' not in data['description']
    assert data['summary'] == 'Reminder added: Vet, Mon 26 Oct 09:30.'
    assert ('2026-10-26', 'Vet') in [(d, t) for _i, d, t in web(home, 'day', date(2026, 10, 26))]


@pytest.mark.parametrize('body', [
    {'date': '2026-10-26'},
    {'title': 'X'},
    {'title': '   ', 'date': '2026-10-26'},
    {'title': 'X', 'date': '2026-13-01'},
    {'title': 'X', 'date': '2026-10-26', 'time': '25:00'},
    {'title': 'X', 'date': '2026-10-26', 'category': 'nope'},
    {'title': 'X' * 257, 'date': '2026-10-26'},
    {'title': 5, 'date': '2026-10-26'},
    {'title': 'X', 'date': '2026-10-26', 'recurring': {'unit': 'day'}},
])
def test_add_reminder_rejects(home, agent, body):
    assert home.post('/reminders', agent, json=body).status_code == 400


def test_mark_done_is_idempotent_and_owned(home, agent):
    rid = add_reminder(home, creator='Priya')
    resp = home.put(f'/reminders/{rid}/done', agent, json={'done': True, 'by': 'Priya'})
    assert resp.status_code == 200 and resp.get_json()['done'] is True
    first = resp.get_json()['completed_at']
    again = home.put(f'/reminders/{rid}/done', agent, json={'done': True, 'by': 'Priya'}).get_json()
    assert again['done'] is True and again['completed_at'] == first
    assert home.put(f'/reminders/{rid}/done', agent, json={'done': False, 'by': 'Priya'}).get_json()['done'] is False
    resp = home.put(f'/reminders/{rid}/done', agent, json={'done': True, 'by': 'Dad'})
    assert resp.status_code == 403
    assert home.put(f'/reminders/{rid}/done', agent, json={'done': 'yes', 'by': 'Priya'}).status_code == 400
    assert home.put('/reminders/99999/done', agent, json={'done': True}).status_code == 404


def test_recurring_dates_are_changed_in_homehub(home, agent):
    resp = home.put('/reminders/-1000001/done', agent, json={'done': True})
    assert resp.status_code == 400 and 'recurring' in resp.get_json()['message']


def test_trash_needs_allow_delete(home, agent):
    rid = add_reminder(home, creator='Priya')
    resp = home.delete(f'/reminders/{rid}', agent, json={'by': 'Priya'})
    assert resp.status_code == 403
    deleter = home.token('Deleter', act_as=True, allow_delete=True, reminders='write')
    assert home.delete(f'/reminders/{rid}', deleter, json={'by': 'Dad'}).status_code == 403
    resp = home.delete(f'/reminders/{rid}', deleter, json={'by': 'Priya'})
    assert resp.status_code == 200 and resp.get_json()['trashed'] is True
    # In the calendar's trash, where it can be restored
    trash = home.client.get('/api/reminders/trash').get_json()['reminders']
    assert [r['id'] for r in trash] == [rid]
    assert home.delete(f'/reminders/{rid}', deleter, json={'by': 'Priya'}).status_code == 404
    assert home.put(f'/reminders/{rid}/done', deleter, json={'done': True, 'by': 'Priya'}).status_code == 404


def test_only_reminders_can_be_deleted(home):
    from app.api import registry
    destructive = [op.name for op in registry.OPERATIONS if op.destructive]
    deletes = [op.name for op in registry.OPERATIONS if op.method == 'DELETE']
    assert destructive == deletes == ['reminders_trash']
