"""API edits: shopping items, chores, reminders and recipes, with the web's ownership rule."""
import json
from datetime import date

import pytest

from api_support import Home
from app.models import ApiActivity, Chore, Recipe, RecurringChore, RecurringReminder, Reminder, ShoppingItem, db


@pytest.fixture()
def home(tmp_path, monkeypatch):
    return Home(tmp_path, monkeypatch)


@pytest.fixture()
def agent(home):
    return home.all_tools('Hermes')


def add(home, model, **fields):
    with home.ctx():
        row = model(**fields)
        db.session.add(row)
        db.session.commit()
        return row.id


# --- Shopping ---

def test_shopping_edit(home, agent):
    item_id = add(home, ShoppingItem, item='Bred', creator='Priya', tags='["Old"]')
    resp = home.patch(f'/shopping/{item_id}', agent, json={'item': 'Bread', 'tags': ['Costco', 'Bakery', ' '], 'by': 'Priya'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['item'] == 'Bread' and data['tags'] == ['Costco', 'Bakery']
    assert data['summary'] == 'Shopping item updated: Bread (Costco, Bakery).'
    # Only the tags: the text stays
    data = home.patch(f'/shopping/{item_id}', agent, json={'tags': [], 'by': 'Priya'}).get_json()
    assert data['item'] == 'Bread' and data['tags'] == []
    # The shopping page shows the change
    assert 'Bread' in home.client.get('/shopping').get_data(as_text=True)
    with home.ctx():
        assert [a.action for a in ApiActivity.query.all()] == ['shopping_edit', 'shopping_edit']


def test_shopping_edit_rules(home, agent):
    item_id = add(home, ShoppingItem, item='Milk', creator='Priya', tags='[]')
    resp = home.patch(f'/shopping/{item_id}', agent, json={'item': 'Oat milk', 'by': 'Mom'})
    assert resp.status_code == 403 and resp.get_json()['message'] == 'Mom cannot change this item.'
    assert home.patch(f'/shopping/{item_id}', agent, json={'item': 'X', 'by': 'Administrator'}).status_code == 400
    assert home.patch('/shopping/999', agent, json={'item': 'X', 'by': 'Priya'}).status_code == 404
    resp = home.patch(f'/shopping/{item_id}', agent, json={'by': 'Priya'})
    assert resp.status_code == 400 and 'at least one field' in resp.get_json()['message']
    assert home.patch(f'/shopping/{item_id}', agent, json={'item': 'X', 'checked': True, 'by': 'Priya'}).status_code == 400
    assert home.patch(f'/shopping/{item_id}', agent, json={'item': '  ', 'by': 'Priya'}).status_code == 400
    with home.ctx():
        assert db.session.get(ShoppingItem, item_id).item == 'Milk'


def test_token_name_owns_what_the_token_added(home):
    secret = home.token('Kitchen tablet', shopping='write')
    item_id = home.post('/shopping', secret, json={'item': 'Eggs'}).get_json()['id']
    assert home.patch(f'/shopping/{item_id}', secret, json={'item': 'Brown eggs'}).status_code == 200
    other = add(home, ShoppingItem, item='Tea', creator='Dad', tags='[]')
    assert home.patch(f'/shopping/{other}', secret, json={'item': 'Green tea'}).status_code == 403


def test_web_shopping_edit_unchanged(home):
    item_id = add(home, ShoppingItem, item='Milk', creator='Priya', tags='[]')
    resp = home.client.put(f'/api/shopping/{item_id}', json={'user': 'Priya', 'item': 'Oat milk', 'tags': ['A', '']})
    assert resp.get_json() == {'ok': True, 'item': {'id': item_id, 'item': 'Oat milk', 'tags': ['A', '']}}


# --- Chores ---

def test_chores_edit(home, agent):
    chore_id = add(home, Chore, description='Water plants', creator='Mom', tags='[]')
    resp = home.patch(f'/chores/{chore_id}', agent, json={'description': 'Water the plants', 'due_date': '2026-11-20',
                                                          'tags': ['Priya'], 'by': 'Mom'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert (data['description'], data['due_date'], data['tags']) == ('Water the plants', '2026-11-20', ['Priya'])
    assert data['summary'] == 'Chore updated: Water the plants, due Fri 20 Nov.'
    data = home.patch(f'/chores/{chore_id}', agent, json={'due_date': None, 'by': 'Mom'}).get_json()
    assert data['due_date'] is None and data['description'] == 'Water the plants' and data['tags'] == ['Priya']
    assert 'Water the plants' in home.client.get('/chores').get_data(as_text=True)


def test_chores_edit_rules(home, agent):
    chore_id = add(home, Chore, description='Bins', creator='Mom', tags='[]')
    resp = home.patch(f'/chores/{chore_id}', agent, json={'description': 'X', 'by': 'Dad'})
    assert resp.status_code == 403 and resp.get_json()['message'] == 'Dad cannot change this chore.'
    assert home.patch('/chores/999', agent, json={'description': 'X', 'by': 'Mom'}).status_code == 404
    assert home.patch(f'/chores/{chore_id}', agent, json={'by': 'Mom'}).status_code == 400
    assert home.patch(f'/chores/{chore_id}', agent, json={'done': True, 'by': 'Mom'}).status_code == 400
    assert home.patch(f'/chores/{chore_id}', agent, json={'due_date': 'Friday', 'by': 'Mom'}).status_code == 400


def test_recurring_chore_edit_is_a_conflict(home, agent):
    rule_id = add(home, RecurringChore, description='Bins', creator='Mom', interval=1, unit='week', start_date=date.today())
    chore_id = add(home, Chore, description='Bins', creator='Mom', tags='[]', recurring_id=rule_id, due_date=date.today())
    resp = home.patch(f'/chores/{chore_id}', agent, json={'description': 'X', 'by': 'Mom'})
    assert resp.status_code == 409 and 'HomeHub' in resp.get_json()['message']


# --- Reminders ---

def test_reminders_edit(home, agent):
    rid = add(home, Reminder, date=date(2026, 11, 12), start_date=date(2026, 11, 12), time='09:30', start_time='09:30',
              title='Dentist', creator='Priya')
    resp = home.patch(f'/reminders/{rid}', agent, json={'title': 'Dentist check-up', 'date': '2026-11-13', 'time': '10:15',
                                                        'category': 'health', 'description': 'Bring card', 'by': 'Priya'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert (data['title'], data['date'], data['time'], data['category']) == ('Dentist check-up', '2026-11-13', '10:15', 'health')
    assert data['summary'] == 'Reminder updated: Dentist check-up, Fri 13 Nov 10:15.'
    data = home.patch(f'/reminders/{rid}', agent, json={'time': None, 'by': 'Priya'}).get_json()
    assert data['time'] is None and data['all_day'] is True and data['date'] == '2026-11-13'
    # The calendar's own API shows the change
    web = home.client.get('/api/reminders?scope=day&date=2026-11-13').get_json()['reminders']
    assert [(r['title'], r['start_time']) for r in web] == [('Dentist check-up', None)]


def test_reminders_edit_rules(home, agent):
    rid = add(home, Reminder, date=date(2026, 11, 12), start_date=date(2026, 11, 12), title='Dentist', creator='Priya')
    resp = home.patch(f'/reminders/{rid}', agent, json={'title': 'X', 'by': 'Mom'})
    assert resp.status_code == 403 and resp.get_json()['message'] == 'Mom cannot change this reminder.'
    assert home.patch(f'/reminders/{rid}', agent, json={'title': 'X', 'by': 'admin'}).status_code == 400
    assert home.patch('/reminders/999', agent, json={'title': 'X', 'by': 'Priya'}).status_code == 404
    assert home.patch(f'/reminders/{rid}', agent, json={'by': 'Priya'}).status_code == 400
    assert home.patch(f'/reminders/{rid}', agent, json={'category': 'nope', 'by': 'Priya'}).status_code == 400
    assert home.patch(f'/reminders/{rid}', agent, json={'time': '9am', 'by': 'Priya'}).status_code == 400
    assert home.patch(f'/reminders/{rid}', agent, json={'color': 'red', 'by': 'Priya'}).status_code == 400
    # Moved to the trash: it is no longer a reminder
    with home.ctx():
        from app.blueprints import dashboard
        dashboard.trash_reminder(db.session.get(Reminder, rid), 'Priya')
    assert home.patch(f'/reminders/{rid}', agent, json={'title': 'X', 'by': 'Priya'}).status_code == 404


def test_recurring_reminders_are_a_conflict(home, agent):
    rule_id = add(home, RecurringReminder, title='Pills', creator='Mom', interval=1, unit='day', start_date=date.today())
    resp = home.patch(f'/reminders/{-(1000000 + rule_id)}', agent, json={'title': 'X', 'by': 'Mom'})
    assert resp.status_code == 409
    rid = add(home, Reminder, date=date.today(), start_date=date.today(), title='Pills', creator='Mom', recurring_id=rule_id)
    resp = home.patch(f'/reminders/{rid}', agent, json={'title': 'X', 'by': 'Mom'})
    assert resp.status_code == 409 and 'HomeHub' in resp.get_json()['message']


# --- Recipes ---

def test_recipes_edit(home, agent):
    rec_id = add(home, Recipe, title='Dal', ingredients='Lentils', creator='Dad', tags='[]')
    resp = home.patch(f'/recipes/{rec_id}', agent, json={'title': 'Tadka dal', 'link': 'https://example.com/dal',
                                                         'tags': ['Quick'], 'by': 'Dad'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert (data['title'], data['link'], data['ingredients'], data['tags']) == ('Tadka dal', 'https://example.com/dal', 'Lentils', ['Quick'])
    assert data['summary'] == 'Recipe updated: Tadka dal.'
    assert 'Tadka dal' in home.client.get('/recipes').get_data(as_text=True)


@pytest.mark.parametrize('body, message', [
    ({'link': 'javascript:alert(1)'}, 'Invalid link URL'),
    ({'ingredients': '', 'instructions': ''}, 'ingredients or instructions'),
    ({'title': ''}, 'title'),
    ({}, 'at least one field'),
    ({'creator': 'Dad'}, 'creator'),
])
def test_recipes_edit_validation(home, agent, body, message):
    rec_id = add(home, Recipe, title='Dal', ingredients='Lentils', creator='Dad', tags='[]')
    resp = home.patch(f'/recipes/{rec_id}', agent, json={**body, 'by': 'Dad'})
    assert resp.status_code == 400 and message in resp.get_json()['message']
    with home.ctx():
        assert db.session.get(Recipe, rec_id).title == 'Dal'


def test_recipes_edit_owner(home, agent):
    rec_id = add(home, Recipe, title='Dal', ingredients='Lentils', creator='Dad', tags='[]')
    resp = home.patch(f'/recipes/{rec_id}', agent, json={'title': 'X', 'by': 'Priya'})
    assert resp.status_code == 403 and resp.get_json()['message'] == 'Priya cannot change this recipe.'
    assert home.patch('/recipes/999', agent, json={'title': 'X', 'by': 'Dad'}).status_code == 404


# --- Permissions and MCP ---

def test_edits_need_write(home):
    reader = home.token('Reader', shopping='read', chores='read', reminders='read', recipes='read')
    item_id = add(home, ShoppingItem, item='Milk', creator='Reader', tags='[]')
    assert home.patch(f'/shopping/{item_id}', reader, json={'item': 'X'}).status_code == 403


def test_mcp_lists_edits_only_with_write(home):
    def tools(secret):
        resp = home.post('/mcp', secret, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        return {t['name']: t for t in resp.get_json()['result']['tools']}
    reader = tools(home.token('Reader', shopping='read', chores='read', reminders='read', recipes='read'))
    assert not any(name.endswith('_edit') for name in reader)
    writer = tools(home.token('Writer', act_as=True, shopping='write', chores='write', reminders='write', recipes='write'))
    for name in ('shopping_edit', 'chores_edit', 'reminders_edit', 'recipes_edit'):
        assert writer[name]['annotations'] == {'title': writer[name]['title'], 'readOnlyHint': False, 'openWorldHint': False,
                                               'destructiveHint': False, 'idempotentHint': True}
    assert writer['chores_edit']['inputSchema']['properties']['due_date']['type'] == ['string', 'null']
    assert writer['reminders_edit']['inputSchema']['properties']['time']['type'] == ['string', 'null']


def test_mcp_edit_call(home):
    secret = home.token('Writer', act_as=True, shopping='write')
    item_id = add(home, ShoppingItem, item='Bred', creator='Priya', tags='[]')
    resp = home.post('/mcp', secret, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                           'params': {'name': 'shopping_edit', 'arguments': {'item_id': item_id, 'item': 'Bread', 'by': 'Priya'}}})
    result = resp.get_json()['result']
    assert result['isError'] is False and result['content'][0]['text'] == 'Shopping item updated: Bread.'
    assert json.loads(result['content'][1]['text'])['item'] == 'Bread'


def test_notes_and_expiry_stay_add_only(home):
    from app.api import registry
    names = {op.name for op in registry.OPERATIONS}
    assert {'shopping_edit', 'chores_edit', 'reminders_edit', 'recipes_edit'} <= names
    assert not any(n.startswith(('notes_', 'expiry_')) and n.endswith('_edit') for n in names)
