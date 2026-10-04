import pytest

from app import admin, create_app, db
from app.models import Note


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
        db.session.execute(db.text("CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY, value TEXT)"))
        db.session.commit()
    return app


@pytest.fixture()
def client():
    admin._failed_attempts.clear()
    app = make_app()
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['authed'] = True
    return c


def set_password(client, password='4321'):
    with client.application.app_context():
        admin.set_admin_password(password)


def add_note(client, creator='Alice'):
    with client.application.app_context():
        note = Note(content='hello', creator=creator)
        db.session.add(note)
        db.session.commit()
        return note.id


def note_exists(client, note_id):
    with client.application.app_context():
        return db.session.get(Note, note_id) is not None


def delete_note_as(client, note_id, user):
    return client.post(f'/notes/delete/{note_id}', data={'user': user})


def test_without_password_admin_name_still_works(client):
    note_id = add_note(client)
    delete_note_as(client, note_id, 'Administrator')
    assert not note_exists(client, note_id)


def test_password_blocks_admin_name_until_unlocked(client):
    set_password(client)
    note_id = add_note(client)

    delete_note_as(client, note_id, 'Administrator')
    assert note_exists(client, note_id)

    assert client.post('/admin/unlock', json={'password': '4321'}).get_json()['ok'] is True
    delete_note_as(client, note_id, 'Administrator')
    assert not note_exists(client, note_id)


def test_wrong_password_is_rejected(client):
    set_password(client)
    note_id = add_note(client)
    resp = client.post('/admin/unlock', json={'password': '0000'})
    assert resp.status_code == 403
    delete_note_as(client, note_id, 'Administrator')
    assert note_exists(client, note_id)


def test_members_are_unaffected_by_password(client):
    set_password(client)
    own = add_note(client, creator='Alice')
    other = add_note(client, creator='Bob')
    delete_note_as(client, own, 'Alice')
    delete_note_as(client, other, 'Alice')
    assert not note_exists(client, own)
    assert note_exists(client, other)


def test_lock_ends_admin_access(client):
    set_password(client)
    note_id = add_note(client)
    client.post('/admin/unlock', json={'password': '4321'})
    client.post('/admin/lock')
    delete_note_as(client, note_id, 'Administrator')
    assert note_exists(client, note_id)


def test_resetting_password_locks_existing_sessions(client):
    set_password(client, '4321')
    note_id = add_note(client)
    client.post('/admin/unlock', json={'password': '4321'})
    set_password(client, '9876')
    delete_note_as(client, note_id, 'Administrator')
    assert note_exists(client, note_id)


def test_repeated_failures_are_throttled(client):
    set_password(client)
    for _ in range(admin.MAX_FAILED_ATTEMPTS):
        assert client.post('/admin/unlock', json={'password': 'nope'}).status_code == 403
    assert client.post('/admin/unlock', json={'password': '4321'}).status_code == 429


def test_admin_only_settings_need_the_password(client):
    set_password(client)
    client.post('/notice', data={'user': 'Administrator', 'content': 'locked out'})
    assert 'locked out' not in client.get('/').get_data(as_text=True)
    client.post('/admin/unlock', json={'password': '4321'})
    client.post('/notice', data={'user': 'Administrator', 'content': 'from admin'})
    assert 'from admin' in client.get('/').get_data(as_text=True)


def test_password_is_stored_hashed(client):
    set_password(client, '4321')
    with client.application.app_context():
        stored = admin.get_admin_password_hash()
    assert stored and '4321' not in stored


def test_cli_sets_and_clears_password(client):
    runner = client.application.test_cli_runner()
    result = runner.invoke(args=['set-admin-password'], input='4321\n4321\n')
    assert result.exit_code == 0
    with client.application.app_context():
        assert admin.admin_password_enabled()

    too_short = runner.invoke(args=['set-admin-password'], input='12\n12\n')
    assert too_short.exit_code != 0

    cleared = runner.invoke(args=['set-admin-password', '--clear'])
    assert cleared.exit_code == 0
    with client.application.app_context():
        assert not admin.admin_password_enabled()


def test_page_exposes_password_state(client):
    assert 'passwordEnabled: false' in client.get('/').get_data(as_text=True)
    set_password(client)
    html = client.get('/').get_data(as_text=True)
    assert 'passwordEnabled: true' in html and 'unlocked: false' in html
    client.post('/admin/unlock', json={'password': '4321'})
    assert 'unlocked: true' in client.get('/').get_data(as_text=True)
