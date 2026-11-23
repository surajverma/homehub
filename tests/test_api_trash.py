"""trash_list and trash_restore: the shared Trash through the API and MCP, with the same rules as the Trash page."""
import json
from datetime import date, timedelta

import pytest

from api_support import Home
from app import db, trash
from app.clock import utcnow
from app.models import ApiActivity, ApiToken, ExpenseEntry, Note, ShoppingItem, UndoStash

TODAY = date.today()


@pytest.fixture()
def home(tmp_path, monkeypatch):
    home = Home(tmp_path, monkeypatch)
    with home.ctx():
        db.session.execute(db.text("CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY, value TEXT)"))
        db.session.commit()
    return home


@pytest.fixture()
def agent(home):
    return home.all_tools('Hermes', allow_delete=True)


def add_and_trash(home, secret, path, body, by='Priya'):
    item = home.post(path, secret, json={**body, 'by': by}).get_json()
    resp = home.delete(f"{path}/{item['id']}", secret, json={'by': by})
    assert resp.status_code == 200, resp.get_json()
    return item


def listing(home, secret, **query):
    resp = home.get('/trash', secret, query_string=query)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def rpc(home, secret, method, params=None):
    body = {'jsonrpc': '2.0', 'id': 1, 'method': method, **({'params': params} if params is not None else {})}
    return home.post('/mcp', secret, json=body, headers={'Accept': 'application/json, text/event-stream'}).get_json()


def tool_names(home, secret):
    return {t['name'] for t in rpc(home, secret, 'tools/list')['result']['tools']}


def test_list_shows_the_trash_with_the_cli_refs(home, agent):
    add_and_trash(home, agent, '/shopping', {'item': 'Milk'})
    add_and_trash(home, agent, '/notes', {'content': 'Call the plumber'}, by='Dad')
    data = listing(home, agent)
    assert data['count'] == 2
    newest, oldest = data['entries']
    assert (newest['module'], newest['label'], newest['owner'], newest['deleted_by']) == (
        'notes', 'Call the plumber', 'Dad', 'Dad (via Hermes)')
    assert (oldest['module'], oldest['label']) == ('shopping', 'Milk')
    assert newest['days_left'] == 7 and newest['deleted_at'] and newest['expires_at']
    with home.ctx():
        assert {trash.entry_ref(e) for e in trash.entries()} == {newest['ref'], oldest['ref']}
        tokens = {e.token for e in UndoStash.query.all()}
    # The Undo secret never leaves the server
    assert not any(token in json.dumps(data) for token in tokens)
    assert listing(home, agent, module='shopping')['entries'] == [oldest]
    assert listing(home, agent, module='SHOPPING')['entries'] == [oldest]


def test_list_only_shows_tools_the_token_can_read(home, agent):
    add_and_trash(home, agent, '/shopping', {'item': 'Milk'})
    add_and_trash(home, agent, '/notes', {'content': 'Secret plans'})
    notes_only = home.token('Notes reader', notes='read')
    assert [e['module'] for e in listing(home, notes_only)['entries']] == ['notes']
    resp = home.get('/trash', notes_only, query_string={'module': 'shopping'})
    assert resp.status_code == 403
    # A tool switched off in config.yml leaves the list too
    home.toggle('notes', False)
    assert listing(home, notes_only)['entries'] == []


def test_unknown_module_is_refused(home, agent):
    resp = home.get('/trash', agent, query_string={'module': 'media'})
    assert resp.status_code == 400 and 'module' in resp.get_json()['message']


def test_expired_entries_are_left_out(home, agent):
    add_and_trash(home, agent, '/shopping', {'item': 'Milk'})
    with home.ctx():
        entry = UndoStash.query.filter(UndoStash.module.isnot(None)).one()
        entry.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
    assert listing(home, agent)['entries'] == []


def test_restore_puts_it_back_and_is_logged(home, agent):
    item = add_and_trash(home, agent, '/shopping', {'item': 'Milk'})
    [entry] = listing(home, agent)['entries']
    resp = home.post(f"/trash/{entry['ref']}/restore", agent, json={'by': 'Priya'})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json() == {'ref': entry['ref'], 'module': 'shopping', 'label': 'Milk', 'restored': True,
                               'summary': 'Milk restored.'}
    with home.ctx():
        row = ShoppingItem.query.one()
        assert (row.id, row.item, row.creator) == (item['id'], 'Milk', 'Priya')
        log = ApiActivity.query.order_by(ApiActivity.id.desc()).first()
        assert (log.action, log.acting_as, log.target) == ('trash_restore', 'Priya', f"{entry['ref']} Milk")
    assert listing(home, agent)['entries'] == []
    # Twice: it is no longer in the trash
    again = home.post(f"/trash/{entry['ref']}/restore", agent, json={'by': 'Priya'})
    assert again.status_code == 404


def test_restore_ref_is_not_case_sensitive(home, agent):
    add_and_trash(home, agent, '/notes', {'content': 'Buy stamps'})
    [entry] = listing(home, agent)['entries']
    resp = home.post(f"/trash/{entry['ref'].upper()}/restore", agent, json={'by': 'Priya'})
    assert resp.status_code == 200
    with home.ctx():
        assert Note.query.count() == 1


@pytest.mark.parametrize('ref', ['1', 't-', 't-zzzzzzzzzz', 't-0123456789'])
def test_restore_of_an_unknown_ref_is_not_found(home, agent, ref):
    add_and_trash(home, agent, '/shopping', {'item': 'Milk'})
    resp = home.post(f'/trash/{ref}/restore', agent, json={'by': 'Priya'})
    assert resp.status_code == 404
    with home.ctx():
        assert ShoppingItem.query.count() == 0


def test_only_the_owner_may_restore(home, agent):
    add_and_trash(home, agent, '/shopping', {'item': 'Milk'}, by='Priya')
    [entry] = listing(home, agent)['entries']
    resp = home.post(f"/trash/{entry['ref']}/restore", agent, json={'by': 'Dad'})
    assert resp.status_code == 403
    # Without "by" the token acts as itself, which does not own Priya's item
    resp = home.post(f"/trash/{entry['ref']}/restore", agent)
    assert resp.status_code == 403
    with home.ctx():
        assert ShoppingItem.query.count() == 0
    # Never as the admin
    resp = home.post(f"/trash/{entry['ref']}/restore", agent, json={'by': 'Administrator'})
    assert resp.status_code == 400


def test_entry_without_an_owner_is_the_admins_alone(home, agent):
    with home.ctx():
        row = ShoppingItem(item='Old milk', creator='')
        db.session.add(row)
        db.session.commit()
        from app.blueprints.shopping import trash_shopping_item
        trash_shopping_item(row, 'Administrator')
    [entry] = listing(home, agent)['entries']
    for body in ({'by': 'Priya'}, None):
        assert home.post(f"/trash/{entry['ref']}/restore", agent, json=body).status_code == 403


def test_restore_needs_allow_delete_on_that_tool(home, agent):
    add_and_trash(home, agent, '/shopping', {'item': 'Milk'})
    [entry] = listing(home, agent)['entries']
    # Read & write without Allow deleting: it can see the entry, but not restore it
    writer = home.token('Writer', act_as=True, shopping='write')
    assert listing(home, writer)['count'] == 1
    resp = home.post(f"/trash/{entry['ref']}/restore", writer, json={'by': 'Priya'})
    assert resp.status_code == 403 and 'Allow deleting' in resp.get_json()['message']
    # Allow deleting on another tool does not cover this one
    other = home.token('Other', act_as=True, shopping='read', notes='write', allow_delete=True)
    assert home.post(f"/trash/{entry['ref']}/restore", other, json={'by': 'Priya'}).status_code == 403
    # Cannot read the tool at all: as far as it knows, there is no such entry
    stranger = home.token('Stranger', act_as=True, notes='write', allow_delete=True)
    assert home.post(f"/trash/{entry['ref']}/restore", stranger, json={'by': 'Priya'}).status_code == 404
    with home.ctx():
        assert ShoppingItem.query.count() == 0


def test_token_from_before_allow_delete_covered_everything_restores_reminders_only(home, agent):
    add_and_trash(home, agent, '/shopping', {'item': 'Milk'})
    reminder = add_and_trash(home, agent, '/reminders', {'title': 'Dentist', 'date': TODAY.isoformat()})
    secret = home.token('Old agent', act_as=True, allow_delete=True, reminders='write', shopping='write')
    with home.ctx():
        row = ApiToken.query.filter_by(name='Old agent').one()
        row.delete_scope = 'reminders'
        db.session.commit()
    refs = {e['module']: e['ref'] for e in listing(home, secret)['entries']}
    assert home.post(f"/trash/{refs['shopping']}/restore", secret, json={'by': 'Priya'}).status_code == 403
    resp = home.post(f"/trash/{refs['reminders']}/restore", secret, json={'by': 'Priya'})
    assert resp.status_code == 200, resp.get_json()
    with home.ctx():
        from app.models import Reminder
        assert Reminder.query.one().id == reminder['id']


def test_restore_conflict_says_why(home, agent):
    item = add_and_trash(home, agent, '/expenses', {'title': 'Groceries', 'amount': 120})
    [entry] = listing(home, agent)['entries']
    with home.ctx():
        from app.undo import RestoreConflict
        from app import undo

        def refuse(_payload):
            raise RestoreConflict('Groceries is already there again.')
        original = undo._restorers['expense_entry']
        undo._restorers['expense_entry'] = refuse
    try:
        resp = home.post(f"/trash/{entry['ref']}/restore", agent, json={'by': 'Priya'})
    finally:
        undo._restorers['expense_entry'] = original
    assert resp.status_code == 409 and resp.get_json()['message'] == 'Groceries is already there again.'
    with home.ctx():
        assert ExpenseEntry.query.count() == 0 and len(trash.entries()) == 1
    assert item['id']


def test_mcp_tools_follow_the_token(home, agent):
    assert {'trash_list', 'trash_restore'} <= tool_names(home, agent)
    reader = home.token('Reader', shopping='read')
    assert 'trash_list' in tool_names(home, reader) and 'trash_restore' not in tool_names(home, reader)
    writer = home.token('Writer', shopping='write')
    assert 'trash_restore' not in tool_names(home, writer)
    presence = home.token('Presence', presence='read', notice='read')
    assert not {'trash_list', 'trash_restore'} & tool_names(home, presence)
    tools = {t['name']: t for t in rpc(home, agent, 'tools/list')['result']['tools']}
    assert tools['trash_list']['annotations']['readOnlyHint'] is True
    restore = tools['trash_restore']['annotations']
    assert restore['readOnlyHint'] is False and restore['destructiveHint'] is False


def test_mcp_restore(home, agent):
    add_and_trash(home, agent, '/notes', {'content': 'Water the plants'})
    listed = rpc(home, agent, 'tools/call', {'name': 'trash_list', 'arguments': {}})['result']
    [entry] = json.loads(listed['content'][0]['text'])['entries']
    result = rpc(home, agent, 'tools/call', {'name': 'trash_restore',
                                             'arguments': {'ref': entry['ref'], 'by': 'Priya'}})['result']
    assert result['isError'] is False
    assert result['content'][0] == {'type': 'text', 'text': 'Water the plants restored.'}
    with home.ctx():
        assert Note.query.one().content == 'Water the plants'
