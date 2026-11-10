"""API deletes: every tool with a trash can move its items there, never delete them for good."""
import json
from datetime import date, timedelta

import pytest

from api_support import Home, trash_action
from app import db, trash
from app.models import (ApiActivity, Chore, ExpenseEntry, ExpiryItem, Note, Recipe, RecurringChore, RecurringExpense,
                        ShoppingItem)

TODAY = date.today()


@pytest.fixture()
def home(tmp_path, monkeypatch):
    home = Home(tmp_path, monkeypatch)
    with home.ctx():
        db.session.execute(db.text("CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY, value TEXT)"))
        db.session.commit()
    return home


@pytest.fixture()
def deleter(home):
    return home.all_tools('Hermes', allow_delete=True)


def entries(home, module=None):
    with home.ctx():
        return [(e.module, e.label, e.owner, e.deleted_by) for e in trash.entries(module)]


ADDS = {
    'shopping': ('/shopping', {'item': 'Milk'}, 'Milk', ShoppingItem),
    'chores': ('/chores', {'description': 'Water plants'}, 'Water plants', Chore),
    'notes': ('/notes', {'content': 'Call the plumber'}, 'Call the plumber', Note),
    'recipes': ('/recipes', {'title': 'Dal', 'ingredients': 'Lentils'}, 'Dal', Recipe),
    'expiry': ('/expiry', {'name': 'Yoghurt', 'expiry_date': (TODAY + timedelta(days=3)).isoformat()}, 'Yoghurt', ExpiryItem),
    'expenses': ('/expenses', {'title': 'Groceries', 'amount': 120}, 'Groceries', ExpenseEntry),
}


@pytest.mark.parametrize('tool', list(ADDS))
def test_delete_moves_to_the_trash_and_restores(home, deleter, tool):
    path, body, name, model = ADDS[tool]
    item = home.post(path, deleter, json={**body, 'by': 'Priya'}).get_json()
    resp = home.delete(f"{path}/{item['id']}", deleter, json={'by': 'Priya'})
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data['trashed'] is True and data['id'] == item['id']
    assert data['summary'] == f'{name} moved to the trash. It can be restored from the Trash page for 7 days.'
    with home.ctx():
        assert model.query.count() == 0
    [(module, label, owner, deleted_by)] = entries(home)
    assert (module, label.startswith(name), owner, deleted_by) == (tool, True, 'Priya', 'Priya (via Hermes)')
    # Logged like every other change
    with home.ctx():
        assert ApiActivity.query.order_by(ApiActivity.id.desc()).first().action == f'{tool}_delete'
    # Gone for the API until it is restored on the Trash page
    assert home.delete(f"{path}/{item['id']}", deleter, json={'by': 'Priya'}).status_code == 404
    with home.ctx():
        entry_id = trash.entries()[0].id
    trash_action(home.client, entry_id, 'restore', {'user': 'Priya'})
    with home.ctx():
        assert model.query.one().id == item['id']


def test_without_by_the_token_name_is_who_deleted(home):
    secret = home.token('Kitchen tablet', shopping='write', allow_delete=True)
    item = home.post('/shopping', secret, json={'item': 'Eggs'}).get_json()
    assert home.delete(f"/shopping/{item['id']}", secret).status_code == 200
    assert entries(home) == [('shopping', 'Eggs', 'Kitchen tablet', 'Kitchen tablet')]


@pytest.mark.parametrize('tool', list(ADDS))
def test_delete_needs_allow_deleting(home, tool):
    path, body, _name, _model = ADDS[tool]
    writer = home.all_tools('Writer')
    item = home.post(path, writer, json={**body, 'by': 'Priya'}).get_json()
    resp = home.delete(f"{path}/{item['id']}", writer, json={'by': 'Priya'})
    assert resp.status_code == 403 and resp.get_json()['message'] == 'This token is not allowed to delete.'
    assert entries(home) == []


def test_delete_needs_write_on_that_tool(home):
    secret = home.token('Mixed', act_as=True, allow_delete=True, shopping='read', notes='write')
    note = home.post('/notes', secret, json={'content': 'x', 'by': 'Priya'}).get_json()
    with home.ctx():
        db.session.add(ShoppingItem(item='Milk', creator='Priya'))
        db.session.commit()
        item_id = ShoppingItem.query.one().id
    assert home.delete(f'/shopping/{item_id}', secret, json={'by': 'Priya'}).status_code == 403
    assert home.delete(f"/notes/{note['id']}", secret, json={'by': 'Priya'}).status_code == 200


def test_only_whoever_added_it_may_delete(home, deleter):
    item = home.post('/shopping', deleter, json={'item': 'Milk', 'by': 'Priya'}).get_json()
    resp = home.delete(f"/shopping/{item['id']}", deleter, json={'by': 'Dad'})
    assert resp.status_code == 403 and resp.get_json()['message'] == 'Dad cannot change this item.'
    assert entries(home) == []


def test_recurring_chores_and_expense_days_are_refused(home, deleter):
    with home.ctx():
        rule = RecurringChore(description='Bins', creator='Priya', interval=1, unit='week', start_date=TODAY)
        db.session.add(rule)
        db.session.commit()
        db.session.add(Chore(description='Bins', creator='Priya', due_date=TODAY, recurring_id=rule.id))
        expense_rule = RecurringExpense(title='Milk', unit_price=30, default_quantity=1, frequency='daily',
                                        start_date=TODAY, effective_from=TODAY, creator='Priya')
        db.session.add(expense_rule)
        db.session.commit()
        db.session.add(ExpenseEntry(date=TODAY, title='Milk', amount=30, payer='Priya', created_by='Priya',
                                    recurring_id=expense_rule.id))
        db.session.commit()
        chore_id = Chore.query.one().id
        entry_id = ExpenseEntry.query.one().id
    resp = home.delete(f'/chores/{chore_id}', deleter, json={'by': 'Priya'})
    assert resp.status_code == 409 and 'Recurring chores are changed in HomeHub' in resp.get_json()['message']
    resp = home.delete(f'/expenses/{entry_id}', deleter, json={'by': 'Priya'})
    assert resp.status_code == 409 and 'Skip it instead' in resp.get_json()['message']
    assert entries(home) == []


def test_mcp_lists_the_deletes_as_destructive(home, deleter):
    headers = {'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-06-18'}
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
    tools = {t['name']: t for t in home.post('/mcp', deleter, json=body, headers=headers).get_json()['result']['tools']}
    destructive = sorted(n for n, t in tools.items() if t['annotations'].get('destructiveHint'))
    assert destructive == ['chores_delete', 'expenses_delete', 'expiry_delete', 'notes_delete', 'recipes_delete',
                           'reminders_trash', 'shopping_delete']
    item = home.post('/shopping', deleter, json={'item': 'Milk', 'by': 'Priya'}).get_json()
    body = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
            'params': {'name': 'shopping_delete', 'arguments': {'item_id': item['id'], 'by': 'Priya'}}}
    result = home.post('/mcp', deleter, json=body, headers=headers).get_json()['result']
    assert result['isError'] is False and 'moved to the trash' in result['content'][0]['text']
    assert json.loads(result['content'][1]['text'])['trashed'] is True
    assert entries(home, 'shopping') == [('shopping', 'Milk', 'Priya', 'Priya (via Hermes)')]


def test_openapi_marks_the_deletes(home, deleter):
    spec = home.get('/openapi.json', deleter).get_json()
    for path in ('/shopping/{item_id}', '/chores/{chore_id}', '/notes/{note_id}', '/recipes/{recipe_id}',
                 '/expiry/{item_id}', '/expenses/{entry_id}'):
        assert spec['paths'][path]['delete']['x-homehub-needs-allow-delete'] is True, path
