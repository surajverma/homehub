from datetime import date, timedelta

import pytest

from app import create_app, db
from app.models import Chore, RecurringChore


def make_app():
    test_config = {
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite://',
        'HOMEHUB_CONFIG': {
            'admin_name': 'Administrator',
            'family_members': ['Alice', 'Bob'],
            'feature_toggles': {
                'chores': True,
                'who_is_home': True,
                'personal_status': True,
                'show_chores_on_homepage': False,
            },
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
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['authed'] = True
    return c


def test_create_recurring_chore_generates_instance(client):
    today_obj = date.today()
    start = today_obj.replace(day=1)
    end = today_obj + timedelta(days=7)
    resp = client.post('/chores', data={
        'description': 'Take out trash',
        'creator': 'Alice',
        'tags': '["Alice"]',
        'is_recurring': 'on',
        'rec_interval': '1',
        'rec_unit': 'day',
        'rec_start_date': start.strftime('%Y-%m-%d'),
        'rec_end_date': end.strftime('%Y-%m-%d'),
    }, follow_redirects=False)
    assert resp.status_code == 302

    with client.application.app_context():
        rule = RecurringChore.query.first()
        assert rule is not None
        chore = Chore.query.filter_by(recurring_id=rule.id).first()
        assert chore is not None
        assert chore.description == 'Take out trash'
        assert chore.due_date is not None
        assert chore.due_date >= today_obj


def test_recurring_chore_rejects_past_end_date(client):
    today_obj = date.today()
    resp = client.post('/chores', data={
        'description': 'Old recurring chore',
        'creator': 'Alice',
        'tags': '[]',
        'is_recurring': 'on',
        'rec_interval': '1',
        'rec_unit': 'day',
        'rec_start_date': (today_obj - timedelta(days=10)).strftime('%Y-%m-%d'),
        'rec_end_date': (today_obj - timedelta(days=1)).strftime('%Y-%m-%d'),
    }, follow_redirects=True)
    assert resp.status_code == 200

    with client.application.app_context():
        rule = RecurringChore.query.filter_by(description='Old recurring chore').first()
        assert rule is None


def test_recurring_chore_advances_due_date_on_completion(client):
    today = date.today().strftime('%Y-%m-%d')
    client.post('/chores', data={
        'description': 'Water plants',
        'creator': 'Alice',
        'tags': '[]',
        'is_recurring': 'on',
        'rec_interval': '1',
        'rec_unit': 'day',
        'rec_start_date': today,
    }, follow_redirects=False)

    with client.application.app_context():
        chore = Chore.query.filter_by(description='Water plants').first()
        old_due = chore.due_date
        chore_id = chore.id

    resp = client.post(f'/chores/toggle/{chore_id}', follow_redirects=False)
    assert resp.status_code == 302

    with client.application.app_context():
        updated = Chore.query.get(chore_id)
        assert updated.due_date > old_due
        assert updated.done is False


def test_homepage_chores_toggle_shows_widget(client):
    add = client.post('/chores', data={
        'description': 'Wipe table',
        'creator': 'Bob',
        'tags': '[]',
    }, follow_redirects=False)
    assert add.status_code == 302

    save = client.post('/chores/settings', data={
        'user': 'Administrator',
        'show_chores_on_homepage': 'on',
    }, follow_redirects=False)
    assert save.status_code == 302

    page = client.get('/')
    assert page.status_code == 200
    assert b'Open full list' in page.data
    assert b'Wipe table' in page.data


def test_edit_form_posts_to_chores_route(client):
    resp = client.post('/chores', data={
        'description': 'Edit target',
        'creator': 'Alice',
        'tags': '[]',
    }, follow_redirects=False)
    assert resp.status_code == 302

    with client.application.app_context():
        chore = Chore.query.filter_by(description='Edit target').first()
        assert chore is not None
        cid = chore.id

    page = client.get(f'/chores/edit/{cid}')
    assert page.status_code == 200
    assert b'form method="POST" action="/chores" id="choreForm"' in page.data


def test_delete_recurring_chore_from_instance_deletes_rule(client):
    today = date.today().strftime('%Y-%m-%d')
    create = client.post('/chores', data={
        'description': 'Recurring delete target',
        'creator': 'Alice',
        'tags': '[]',
        'is_recurring': 'on',
        'rec_interval': '1',
        'rec_unit': 'day',
        'rec_start_date': today,
    }, follow_redirects=False)
    assert create.status_code == 302

    with client.application.app_context():
        rule = RecurringChore.query.filter_by(description='Recurring delete target').first()
        assert rule is not None
        chore = Chore.query.filter_by(recurring_id=rule.id).first()
        assert chore is not None
        cid = chore.id
        rid = rule.id

    delete = client.post(f'/chores/delete/{cid}', data={'user': 'Alice'}, follow_redirects=False)
    assert delete.status_code == 302

    with client.application.app_context():
        assert RecurringChore.query.get(rid) is None
        assert Chore.query.filter_by(recurring_id=rid).count() == 0


def test_update_chore_requires_admin_or_creator(client):
    create = client.post('/chores', data={
        'description': 'Owner chore',
        'creator': 'Alice',
        'tags': '[]',
    }, follow_redirects=False)
    assert create.status_code == 302

    with client.application.app_context():
        chore = Chore.query.filter_by(description='Owner chore').first()
        assert chore is not None
        cid = chore.id

    update = client.post('/chores', data={
        'chore_id': str(cid),
        'description': 'Hacked chore',
        'creator': 'Bob',
        'user': 'Bob',
        'tags': '[]',
    }, follow_redirects=False)
    assert update.status_code == 302

    with client.application.app_context():
        refreshed = Chore.query.get(cid)
        assert refreshed.description == 'Owner chore'
        assert refreshed.creator == 'Alice'


def test_update_recurring_rule_requires_admin_or_creator(client):
    today = date.today().strftime('%Y-%m-%d')
    create = client.post('/chores', data={
        'description': 'Rule owner',
        'creator': 'Alice',
        'tags': '[]',
        'is_recurring': 'on',
        'rec_interval': '1',
        'rec_unit': 'day',
        'rec_start_date': today,
    }, follow_redirects=False)
    assert create.status_code == 302

    with client.application.app_context():
        rule = RecurringChore.query.filter_by(description='Rule owner').first()
        assert rule is not None
        rid = rule.id

    update = client.post('/chores', data={
        'recurring_rule_id': str(rid),
        'description': 'Rule hacked',
        'creator': 'Bob',
        'user': 'Bob',
        'tags': '[]',
        'is_recurring': 'on',
        'rec_interval': '1',
        'rec_unit': 'day',
        'rec_start_date': today,
    }, follow_redirects=False)
    assert update.status_code == 302

    with client.application.app_context():
        refreshed = RecurringChore.query.get(rid)
        assert refreshed.description == 'Rule owner'
        assert refreshed.creator == 'Alice'


def test_non_recurring_branch_delete_rule_requires_permission(client):
    today = date.today().strftime('%Y-%m-%d')
    create = client.post('/chores', data={
        'description': 'Rule delete target',
        'creator': 'Alice',
        'tags': '[]',
        'is_recurring': 'on',
        'rec_interval': '1',
        'rec_unit': 'day',
        'rec_start_date': today,
    }, follow_redirects=False)
    assert create.status_code == 302

    with client.application.app_context():
        rule = RecurringChore.query.filter_by(description='Rule delete target').first()
        assert rule is not None
        rid = rule.id

    delete_attempt = client.post('/chores', data={
        'recurring_rule_id': str(rid),
        'description': 'Irrelevant',
        'creator': 'Bob',
        'user': 'Bob',
        'tags': '[]',
    }, follow_redirects=False)
    assert delete_attempt.status_code == 302

    with client.application.app_context():
        assert RecurringChore.query.get(rid) is not None
        assert Chore.query.filter_by(recurring_id=rid).count() > 0


def test_delete_recurring_endpoint_removes_completed_chores(client):
    today = date.today().strftime('%Y-%m-%d')
    create = client.post('/chores', data={
        'description': 'Delete all recurring rows',
        'creator': 'Alice',
        'tags': '[]',
        'is_recurring': 'on',
        'rec_interval': '1',
        'rec_unit': 'day',
        'rec_start_date': today,
    }, follow_redirects=False)
    assert create.status_code == 302

    with client.application.app_context():
        rule = RecurringChore.query.filter_by(description='Delete all recurring rows').first()
        assert rule is not None
        chore = Chore.query.filter_by(recurring_id=rule.id).first()
        assert chore is not None
        chore.done = True
        db.session.commit()
        rid = rule.id

    delete = client.post(f'/chores/recurring/delete/{rid}', data={'user': 'Alice'}, follow_redirects=False)
    assert delete.status_code == 302

    with client.application.app_context():
        assert RecurringChore.query.get(rid) is None
        assert Chore.query.filter_by(recurring_id=rid).count() == 0
