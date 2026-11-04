"""API: shopping list, chores, notes, recipes and the expiry tracker."""
from datetime import date, timedelta

import pytest

from api_support import Home
from app.models import ApiActivity, Chore, GroceryHistory, RecurringChore, ShoppingItem, db


@pytest.fixture()
def home(tmp_path, monkeypatch):
    return Home(tmp_path, monkeypatch)


@pytest.fixture()
def agent(home):
    return home.all_tools('Hermes')


# --- Shopping ---

def test_shopping_add_list_and_tick(home, agent):
    resp = home.post('/shopping', agent, json={'item': 'Milk', 'tags': ['Dairy', '<b>Costco</b>', '  '], 'by': 'Priya'})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data['item'] == 'Milk' and data['creator'] == 'Priya' and data['checked'] is False
    # Tags are cleaned exactly as the page cleans them (blank ones dropped)
    assert data['tags'] == ['Dairy', '<b>Costco</b>']
    assert data['summary'] == 'Added to the shopping list: Milk.'
    with home.ctx():
        assert GroceryHistory.query.count() == 1  # suggestions learn from it, as on the page
    item_id = data['id']
    for _ in range(2):
        resp = home.put(f'/shopping/{item_id}/checked', agent, json={'checked': True})
        assert resp.get_json()['checked'] is True
    assert home.put(f'/shopping/{item_id}/checked', agent, json={'checked': False}).get_json()['checked'] is False
    items = home.get('/shopping', agent).get_json()['items']
    assert [(i['id'], i['item']) for i in items] == [(item_id, 'Milk')]
    assert home.put('/shopping/999/checked', agent, json={'checked': True}).status_code == 404
    # Same list as the page's own API
    assert home.client.get('/api/shopping').get_json() == home.get('/shopping', agent).get_json()['items']


@pytest.mark.parametrize('body', [{}, {'item': ''}, {'item': 'x', 'tags': 'Dairy'}, {'item': 'x', 'tags': ['a'] * 21},
                                  {'item': 'x', 'tags': [1]}, {'item': 'x' * 257}])
def test_shopping_rejects(home, agent, body):
    assert home.post('/shopping', agent, json=body).status_code == 400


def test_shopping_without_by_uses_token_name(home):
    secret = home.token('Kitchen tablet', shopping='write')
    assert home.post('/shopping', secret, json={'item': 'Eggs'}).get_json()['creator'] == 'Kitchen tablet'


# --- Chores ---

def test_chore_add_and_done(home, agent):
    resp = home.post('/chores', agent, json={'description': 'Water plants', 'due_date': '2026-10-26', 'tags': ['Priya'], 'by': 'Mom'})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data['due_date'] == '2026-10-26' and data['tags'] == ['Priya'] and data['creator'] == 'Mom'
    assert data['summary'] == 'Chore added: Water plants, due Mon 26 Oct.'
    cid = data['id']
    for _ in range(2):
        assert home.put(f'/chores/{cid}/done', agent, json={'done': True}).get_json()['done'] is True
    assert home.put(f'/chores/{cid}/done', agent, json={'done': False}).get_json()['done'] is False
    assert home.put('/chores/999/done', agent, json={'done': True}).status_code == 404


def make_recurring_chore(home, start):
    with home.ctx():
        rule = RecurringChore(description='Take out bins', creator='Dad', interval=1, unit='week', start_date=start)
        db.session.add(rule)
        db.session.commit()
        rule_id = rule.id
    home.get('/chores', home.all_tools('Reader', level='read', act_as=False))  # generates the current chore
    with home.ctx():
        return Chore.query.filter_by(recurring_id=rule_id).one().id


def test_recurring_chore_done_like_the_web(home, agent):
    today = date.today()
    cid = make_recurring_chore(home, today)
    resp = home.put(f'/chores/{cid}/done', agent, json={'done': True})
    data = resp.get_json()
    assert data['done'] is True and data['due_date'] == (today + timedelta(weeks=1)).isoformat()
    assert data['summary'].startswith('Take out bins done. Next due on')
    # Done again: already ticked for this round, so nothing moves
    again = home.put(f'/chores/{cid}/done', agent, json={'done': True}).get_json()
    assert again['due_date'] == data['due_date'] and 'already done' in again['summary']
    listed = [c for c in home.get('/chores', agent).get_json()['chores'] if c['id'] == cid][0]
    assert listed['done'] is True and listed['recurring_id']
    back = home.put(f'/chores/{cid}/done', agent, json={'done': False}).get_json()
    assert back['done'] is False and back['due_date'] == today.isoformat()
    assert home.put(f'/chores/{cid}/done', agent, json={'done': False}).get_json()['due_date'] == today.isoformat()


def test_web_toggle_still_works(home):
    today = date.today()
    cid = make_recurring_chore(home, today)
    home.client.post(f'/chores/toggle/{cid}')
    with home.ctx():
        assert db.session.get(Chore, cid).due_date == today + timedelta(weeks=1)
    home.client.post(f'/chores/toggle/{cid}')
    with home.ctx():
        assert db.session.get(Chore, cid).due_date == today


# --- Notes ---

def test_notes(home, agent):
    resp = home.post('/notes', agent, json={'content': '<p>Call <b>plumber</b></p><script>bad()</script>', 'by': 'Dad'})
    assert resp.status_code == 201
    data = resp.get_json()
    assert '<script>' not in data['content'] and '<b>plumber</b>' in data['content']
    assert data['creator'] == 'Dad' and data['summary'] == 'Note added.'
    assert [n['id'] for n in home.get('/notes', agent).get_json()['notes']] == [data['id']]
    assert home.post('/notes', agent, json={'content': '   '}).status_code == 400
    # No edits or deletes through the API
    assert home.put(f"/notes/{data['id']}", agent, json={'content': 'x'}).status_code in (404, 405)
    assert home.delete(f"/notes/{data['id']}", agent).status_code in (404, 405)


# --- Recipes ---

def test_recipes(home, agent):
    resp = home.post('/recipes', agent, json={'title': 'Dal', 'link': 'https://example.com/dal', 'ingredients': 'Lentils', 'tags': ['Quick']})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data['link'] == 'https://example.com/dal' and data['tags'] == ['Quick'] and data['creator'] == 'Hermes'
    assert home.get('/recipes', agent).get_json()['recipes'][0]['title'] == 'Dal'


@pytest.mark.parametrize('body, message', [
    ({'title': 'X', 'link': 'javascript:alert(1)', 'ingredients': 'a'}, 'Invalid link URL'),
    ({'title': 'X', 'link': 'ftp://example.com', 'ingredients': 'a'}, 'Invalid link URL'),
    ({'title': 'X'}, 'ingredients or instructions'),
    ({'title': 'X', 'ingredients': '<script></script>'}, 'ingredients or instructions'),
])
def test_recipe_validation_matches_the_form(home, agent, body, message):
    resp = home.post('/recipes', agent, json=body)
    assert resp.status_code == 400 and message in resp.get_json()['message']


# --- Expiry ---

def test_expiry(home, agent):
    soon = date.today() + timedelta(days=3)
    resp = home.post('/expiry', agent, json={'name': 'Yoghurt', 'expiry_date': soon.isoformat()})
    assert resp.status_code == 201
    assert resp.get_json()['days_left'] == 3
    items = home.get('/expiry', agent).get_json()['items']
    assert items[0]['name'] == 'Yoghurt' and items[0]['days_left'] == 3
    assert home.post('/expiry', agent, json={'name': 'X'}).status_code == 400
    assert home.post('/expiry', agent, json={'name': 'X', 'expiry_date': 'tomorrow'}).status_code == 400


# --- Across tools ---

@pytest.mark.parametrize('feature, path', [('shopping_list', '/shopping'), ('chores', '/chores'), ('notes', '/notes'),
                                           ('recipes', '/recipes'), ('expiry_tracker', '/expiry')])
def test_switched_off_features(home, agent, feature, path):
    home.toggle(feature, False)
    assert home.get(path, agent).status_code == 404
    assert home.post(path, agent, json={}).status_code == 404
    home.toggle(feature, True)
    assert home.get(path, agent).status_code == 200


def test_read_token_cannot_add(home):
    reader = home.token('Reader', shopping='read', notes='read')
    assert home.post('/shopping', reader, json={'item': 'Milk'}).status_code == 403
    assert home.post('/notes', reader, json={'content': 'x'}).status_code == 403
    assert home.get('/recipes', reader).status_code == 403
    with home.ctx():
        assert ShoppingItem.query.count() == 0


def test_each_write_is_logged(home, agent):
    home.post('/shopping', agent, json={'item': 'Milk', 'by': 'Priya'})
    home.post('/notes', agent, json={'content': 'Hello'})
    with home.ctx():
        rows = [(a.acting_as, a.action) for a in ApiActivity.query.order_by(ApiActivity.id).all()]
    assert rows == [('Priya', 'shopping_add'), ('Hermes', 'notes_add')]
