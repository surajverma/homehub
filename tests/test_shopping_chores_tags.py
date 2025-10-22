import json
import pytest

from app import create_app, db
from app.models import ShoppingItem, Chore


def make_app():
    test_config = {
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite://',
        'HOMEHUB_CONFIG': {
            'admin_name': 'Administrator',
            'family_members': ['Alice', 'Bob'],
        },
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'test',
    }
    app = create_app(test_config)
    with app.app_context():
        db.create_all()
    return app


@pytest.fixture()
def client():
    app = make_app()
    return app.test_client()


def test_create_items_and_filter_and_edit(client):
    # Create shopping item with tags
    resp = client.post('/shopping', data={
        'item': 'Milk',
        'creator': 'Alice',
        'tags': json.dumps(['Costco', 'Dairy'])
    }, follow_redirects=True)
    assert resp.status_code == 200
    # Create chore with tags
    resp2 = client.post('/chores', data={
        'description': 'Clean kitchen',
        'creator': 'Bob',
        'tags': json.dumps(['Alice', 'Weekend'])
    }, follow_redirects=True)
    assert resp2.status_code == 200

    # Shopping API filter by tag
    j = client.get('/api/shopping?tags=' + json.dumps(['Costco'])).get_json()
    assert any(i['item'] == 'Milk' for i in j)
    j2 = client.get('/api/shopping?tags=' + json.dumps(['Alice'])).get_json()
    assert all(i['item'] != 'Milk' for i in j2)

    # Chores API filter by tag
    c = client.get('/api/chores?tags=' + json.dumps(['Alice'])).get_json()
    assert any(i['description'] == 'Clean kitchen' for i in c)
    c2 = client.get('/api/chores?tags=' + json.dumps(['Costco'])).get_json()
    assert all(i['description'] != 'Clean kitchen' for i in c2)

    # Edit shopping item via PUT
    with client.application.app_context():
        s = ShoppingItem.query.first()
        cid = s.id
    r = client.put(f'/api/shopping/{cid}', json={'item': 'Milk 2%', 'tags': ['Groceries']})
    assert r.status_code == 200
    jr = r.get_json()
    assert jr['ok'] is True and jr['item']['item'] == 'Milk 2%'
    assert 'Groceries' in jr['item']['tags']

    # Edit chore via PUT
    with client.application.app_context():
        ch = Chore.query.first()
        chid = ch.id
    r2 = client.put(f'/api/chores/{chid}', json={'description': 'Deep clean kitchen', 'tags': ['Bob']})
    assert r2.status_code == 200
    jr2 = r2.get_json()
    assert jr2['ok'] is True and 'Deep clean' in jr2['item']['description']
    assert 'Bob' in jr2['item']['tags']
