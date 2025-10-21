import json
from datetime import date

import pytest

from app import create_app, db


def make_app():
    test_config = {
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite://',
        'HOMEHUB_CONFIG': {
            'admin_name': 'Administrator',
            'family_members': ['Alice', 'Bob'],
            'reminders': {'calendar_start_day': 'sunday'}
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


def list_month(client, y, m):
    base = date(y, m, 1)
    r = client.get(f"/api/reminders?scope=month&date={base.strftime('%Y-%m-%d')}")
    assert r.status_code == 200
    return r.get_json()


def test_single_to_recurring_and_back(client):
    today = date(2025, 10, 21)
    ds = today.strftime('%Y-%m-%d')
    # Create a single reminder
    r = client.post('/api/reminders', json={
        'date': ds,
        'title': 'ConvertMe',
        'description': 'x',
        'creator': 'Alice'
    })
    assert r.status_code == 200
    rid = r.get_json()['reminder']['id']

    # Simulate UI conversion: create recurring rule for same date, then delete original
    make_rule = client.post('/api/reminders', json={
        'date': ds,
        'title': 'ConvertMe',
        'description': 'x',
        'creator': 'Alice',
        'recurring': {'interval': 1, 'unit': 'week'}
    })
    assert make_rule.status_code == 200
    j = make_rule.get_json()
    assert j['ok'] is True
    assert 'recurring_id' in j

    # Delete original single (admin-or-creator allowed)
    del_resp = client.delete('/api/reminders', json={'ids': [rid], 'creator': 'Alice'})
    assert del_resp.status_code == 200
    assert del_resp.get_json()['ok'] is True

    # Month list should include recurring synthesized entries for that title
    data = list_month(client, today.year, today.month)
    assert any(r['title'] == 'ConvertMe' and r.get('recurring_id') for r in data['reminders'])

    # Now convert recurring back to single: create a one-off and delete the rule
    # Find the rule ID from the list endpoint
    rr_ids = [rr['id'] for rr in data.get('recurring_rules') or [] if rr['title'] == 'ConvertMe']
    assert rr_ids, 'Recurring rule not found in list'
    rrid = rr_ids[0]

    make_single = client.post('/api/reminders', json={
        'date': ds,
        'title': 'ConvertMe',
        'description': 'x',
        'creator': 'Alice'
    })
    assert make_single.status_code == 200
    j2 = make_single.get_json()
    assert j2['ok'] is True and 'reminder' in j2

    # Delete the rule
    del_rule = client.delete(f'/api/recurring_rules/{rrid}', json={'creator': 'Alice'})
    assert del_rule.status_code == 200
    assert del_rule.get_json()['ok'] is True

    # Month list should still have at least the single reminder and no recurring rule for the title
    data2 = list_month(client, today.year, today.month)
    assert any(r['title'] == 'ConvertMe' and not r.get('recurring_id') for r in data2['reminders'])
    rr_titles = [rr['title'] for rr in data2.get('recurring_rules') or []]
    assert 'ConvertMe' not in rr_titles
