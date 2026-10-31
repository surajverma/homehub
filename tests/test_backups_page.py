import io
import json
import os
import zipfile

import pytest

from app import admin, backup, create_app, db
from test_export_backup import make_data, note_content


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
def home(tmp_path, monkeypatch):
    """A HomeHub data folder in tmp_path that the backup code uses instead of the real one."""
    base = make_data(tmp_path / 'home', note='current')
    monkeypatch.setattr(backup, 'BASE_DIR', str(base))
    monkeypatch.setattr(backup, 'jobs', backup.Jobs())
    return base


PASSWORD = '4321'
ADMIN = {'user': 'Administrator'}


def set_password(client, password=PASSWORD):
    with client.application.app_context():
        admin.set_admin_password(password)


@pytest.fixture()
def bare_client(home):
    """No admin password set, as on a fresh install."""
    return make_app().test_client()


@pytest.fixture()
def client(bare_client):
    """An admin password is set and this browser has unlocked it."""
    set_password(bare_client)
    bare_client.post('/admin/unlock', json={'password': PASSWORD})
    return bare_client


def create(client, parts=('uploads',)):
    resp = client.post('/admin/backups/create', data={**ADMIN, 'parts': list(parts)})
    backup.jobs.wait(10)
    return resp


def zips(home):
    return sorted(n for n in os.listdir(home / 'data' / 'backups') if n.endswith('.zip'))


# --- Admin only, and only with an admin password ---

ROUTES = [
    ('get', '/admin/backups'),
    ('get', '/admin/backups/status'),
    ('post', '/admin/backups/create'),
    ('get', '/admin/backups/download/x.zip'),
    ('post', '/admin/backups/delete/x.zip'),
    ('post', '/admin/backups/upload'),
    ('get', '/admin/backups/restore/x.zip'),
    ('post', '/admin/backups/restore/x.zip'),
    ('get', '/admin/backups/restore-upload/x.zip'),
    ('post', '/admin/backups/restore-upload/x.zip'),
    ('post', '/admin/backups/restore-upload/x.zip/cancel'),
    ('post', '/admin/backups/config-from-backup/dismiss'),
]


@pytest.mark.parametrize('method,url', ROUTES)
def test_every_route_refuses_without_admin_password(bare_client, home, method, url):
    (home / 'data' / 'backups' / 'x.zip').write_bytes(b'zip')
    resp = getattr(bare_client, method)(url + '?user=Administrator', data={**ADMIN, 'parts': ['uploads'], 'password': ''})
    assert resp.status_code == 403
    body = resp.get_data(as_text=True)
    assert 'flask set-admin-password' in body and 'github.com/surajverma/homehub#backup-and-restore' in body
    backup.jobs.wait(5)
    assert zips(home) == ['x.zip']


@pytest.mark.parametrize('method,url', ROUTES)
def test_every_route_is_admin_only(client, method, url):
    resp = getattr(client, method)(url + '?user=Alice', data={'user': 'Alice'})
    assert resp.status_code in (302, 403)
    assert resp.mimetype != 'application/zip'


def test_locked_admin_is_refused(client):
    client.post('/admin/lock')
    assert client.get('/admin/backups?user=Administrator').status_code == 302
    client.post('/admin/unlock', json={'password': PASSWORD})
    assert client.get('/admin/backups?user=Administrator').status_code == 200


def test_welcome_card_explains_admin_password(bare_client):
    html = bare_client.get('/').get_data(as_text=True)
    assert 'Set an admin password to use backups' in html
    assert 'href="https://github.com/surajverma/homehub#backup-and-restore"' in html
    assert 'action="/admin/backups"' not in html
    set_password(bare_client)
    html = bare_client.get('/').get_data(as_text=True)
    assert 'action="/admin/backups"' in html
    assert 'Set an admin password to use backups' not in html


def test_cross_site_create_is_blocked(client, home):
    resp = client.post('/admin/backups/create', data={**ADMIN, 'parts': ['uploads']}, headers={'Sec-Fetch-Site': 'cross-site'})
    assert resp.status_code == 403
    assert zips(home) == []


# --- Page, create, download, delete ---

def test_page_shows_sizes_and_password_note(client):
    html = client.get('/admin/backups?user=Administrator').get_data(as_text=True)
    assert 'data-bytes=' in html
    assert 'may contain the site password in plain text' in html
    assert '/admin/backups/restore/' not in html  # nothing to restore yet


def test_create_backup_in_background_then_list_and_download(client, home):
    resp = create(client, parts=['uploads', 'config'])
    assert resp.status_code == 302
    names = zips(home)
    assert len(names) == 1
    with zipfile.ZipFile(home / 'data' / 'backups' / names[0]) as zf:
        assert json.loads(zf.read('manifest.json'))['parts'] == ['database', 'uploads', 'config']

    page = client.get('/admin/backups?user=Administrator').get_data(as_text=True)
    ready = f'Backup {names[0]} is ready.'
    assert ready in page
    # The outcome is only reported once
    assert ready not in client.get('/admin/backups?user=Administrator').get_data(as_text=True)

    resp = client.get(f'/admin/backups/download/{names[0]}?user=Administrator')
    assert resp.status_code == 200
    assert resp.mimetype == 'application/zip'
    assert resp.headers.get('Accept-Ranges') == 'bytes'
    full = resp.get_data()
    resp.close()
    # A cut-off download can carry on where it stopped
    resp = client.get(f'/admin/backups/download/{names[0]}?user=Administrator', headers={'Range': 'bytes=10-'})
    assert resp.status_code == 206
    assert resp.get_data() == full[10:]
    resp.close()


def test_status_while_running(client, home):
    import threading
    release = threading.Event()
    reported = threading.Event()

    def slow(progress):
        progress(5, 10)
        reported.set()
        release.wait(5)
        return {'name': 'x.zip'}

    backup.jobs.start('backup', slow)
    try:
        # The job runs in its own thread; ask for its status only once it has reported progress
        assert reported.wait(5)
        data = client.get('/admin/backups/status?user=Administrator').get_json()
        assert data['running'] and data['kind'] == 'backup' and data['done'] == 5 and data['total'] == 10
        # Only one job at a time
        resp = client.post('/admin/backups/create', data={**ADMIN, 'parts': ['uploads']}, follow_redirects=True)
        assert 'already running' in resp.get_data(as_text=True)
    finally:
        release.set()
        backup.jobs.wait(5)
    assert client.get('/admin/backups/status?user=Administrator').get_json()['running'] is False


def test_failed_backup_is_reported(client, home, monkeypatch):
    monkeypatch.setattr(backup.shutil, 'disk_usage', lambda _p: type('U', (), {'free': 1})())
    create(client)
    page = client.get('/admin/backups?user=Administrator').get_data(as_text=True)
    assert 'Not enough free disk space' in page
    assert zips(home) == []


def test_download_and_delete_only_reach_data_backups(client, home):
    (home / 'data' / 'secret.zip').write_bytes(b'outside')
    for name in ('..%2Fsecret.zip', '..%5Csecret.zip', 'notes.txt', 'missing.zip'):
        assert client.get(f'/admin/backups/download/{name}?user=Administrator').status_code == 404
        client.post(f'/admin/backups/delete/{name}', data=ADMIN)
    assert (home / 'data' / 'secret.zip').exists()
    assert (home / 'data' / 'backups' / 'notes.txt').exists()


def test_delete_backup(client, home):
    create(client)
    name = zips(home)[0]
    client.post(f'/admin/backups/delete/{name}', data=ADMIN)
    assert zips(home) == []


def test_stale_part_cleaned_when_page_opens(client, home):
    stale = home / 'data' / 'backups' / '.homehub-backup-dead.part'
    stale.write_bytes(b'x')
    os.utime(stale, (0, 0))
    client.get('/admin/backups?user=Administrator')
    assert not stale.exists()


# --- Upload ---

def backup_bytes(tmp_path, note='uploaded'):
    src = make_data(tmp_path / f'src-{note}', note=note)
    path = backup.create_backup(str(tmp_path / f'{note}.zip'), parts=backup.PARTS, base_dir=str(src))
    with open(path, 'rb') as f:
        return f.read()


def uploads_dir(home):
    folder = home / 'data' / 'restore-uploads'
    return sorted(os.listdir(folder)) if folder.exists() else []


def test_upload_goes_straight_to_its_restore_page(client, home, tmp_path):
    data = {**ADMIN, 'backup': (io.BytesIO(backup_bytes(tmp_path)), '../../My Backup!.zip')}
    resp = client.post('/admin/backups/upload', data=data, content_type='multipart/form-data')
    assert resp.status_code == 302
    assert '/admin/backups/restore-upload/uploaded-backup-' in resp.headers['Location']
    # Not in the list of saved backups; it waits in its own folder, under a name of its own
    assert zips(home) == []
    [name] = uploads_dir(home)
    assert name.startswith('uploaded-backup-') and name.endswith('.zip') and 'My' not in name
    html = client.get(resp.headers['Location']).get_data(as_text=True)
    assert 'Database &amp; settings' in html and 'Admin password' in html
    # The file's own name is shown, without the path it came with
    assert 'My Backup!.zip' in html and '../' not in html.split('page-subtitle')[1][:80]
    # The password is the confirmation: no extra dialog on this form
    start = html.index('<form method="POST" action="/admin/backups/restore-upload/')
    assert 'data-confirm' not in html[start:html.index('>', start)]


def test_upload_rejects_bad_zip(client, home):
    data = {**ADMIN, 'backup': (io.BytesIO(b'not a zip'), 'bad.zip')}
    resp = client.post('/admin/backups/upload', data=data, content_type='multipart/form-data', follow_redirects=True)
    assert 'not a zip file' in resp.get_data(as_text=True)
    assert zips(home) == []
    assert uploads_dir(home) == []
    assert os.listdir(home / 'data' / 'backups') == ['notes.txt']


def test_cancel_removes_the_upload(client, home, tmp_path):
    name = upload(client, tmp_path)
    resp = client.post(f'/admin/backups/restore-upload/{name}/cancel', data=ADMIN, follow_redirects=True)
    assert 'Restore cancelled' in resp.get_data(as_text=True)
    assert uploads_dir(home) == []
    assert note_content(home) == 'current'


def test_upload_routes_only_reach_their_folder(client, home):
    create(client)
    saved = zips(home)[0]
    # A saved backup is not an upload, and paths cannot climb out
    for name in (saved, '..%2Fbackups%2F' + saved, 'missing.zip'):
        assert client.get(f'/admin/backups/restore-upload/{name}?user=Administrator').status_code == 404
        client.post(f'/admin/backups/restore-upload/{name}/cancel', data=ADMIN)
    assert zips(home) == [saved]


def test_stale_upload_cleaned_when_page_opens(client, home, tmp_path):
    name = upload(client, tmp_path)
    path = home / 'data' / 'restore-uploads' / name
    os.utime(path, (0, 0))
    client.get('/admin/backups?user=Administrator')
    assert not path.exists()


# --- Restore ---

def upload(client, tmp_path, note='uploaded'):
    data = {**ADMIN, 'backup': (io.BytesIO(backup_bytes(tmp_path, note)), f'{note}.zip')}
    resp = client.post('/admin/backups/upload', data=data, content_type='multipart/form-data')
    return resp.headers['Location'].split('/restore-upload/')[1].split('?')[0]


def test_restore_asks_password_again_and_counts_attempts(client, home, tmp_path):
    name = upload(client, tmp_path)
    preview = client.get(f'/admin/backups/restore-upload/{name}?user=Administrator')
    assert preview.status_code == 200
    html = preview.get_data(as_text=True)
    assert 'Database &amp; settings' in html and 'config.yml' in html

    for _ in range(admin.MAX_FAILED_ATTEMPTS):
        resp = client.post(f'/admin/backups/restore-upload/{name}', data={**ADMIN, 'password': 'wrong', 'parts': ['database']},
                           follow_redirects=True)
    assert note_content(home) == 'current'
    # Locked out now, even with the right password
    resp = client.post(f'/admin/backups/restore-upload/{name}', data={**ADMIN, 'password': '4321', 'parts': ['database']},
                       follow_redirects=True)
    assert 'Too many attempts' in resp.get_data(as_text=True)
    backup.jobs.wait(5)
    assert note_content(home) == 'current'
    # Still there to try again later
    assert uploads_dir(home) == [name]


def test_restore_from_page_round_trip(client, home, tmp_path, example_config_path):
    with open(example_config_path, encoding='utf-8') as f:
        config_before = f.read()
    name = upload(client, tmp_path, note='from-backup')
    resp = client.post(f'/admin/backups/restore-upload/{name}',
                       data={**ADMIN, 'password': '4321', 'parts': ['database', 'uploads', 'config']})
    assert resp.status_code == 302
    backup.jobs.wait(10)

    assert note_content(home) == 'from-backup'
    assert (home / 'uploads' / 'photo.jpg').read_bytes() == b'jpg-from-backup'
    # Not ticked, so left alone
    assert (home / 'pdfs' / 'bill.pdf').read_bytes() == b'%PDF-current'
    # The uploaded zip is gone; a safety backup of what was replaced is in the list
    assert uploads_dir(home) == []
    assert [n for n in zips(home) if n.startswith('homehub-safety-backup-')]

    page = client.get('/admin/backups?user=Administrator').get_data(as_text=True)
    assert 'Restore complete' in page
    assert 'docker compose restart homehub' in page
    # The backup's config is shown with the password hidden, and saved beside the data
    assert 'Config from the restored backup' in page
    assert 'from-backup' in page and 'site-pw-from-backup' not in page and '********' in page
    assert 'site-pw-from-backup' in (home / 'data' / 'config-from-backup.yml').read_text(encoding='utf-8')

    # The config.yml the app reads is never touched
    with open(example_config_path, encoding='utf-8') as f:
        assert f.read() == config_before

    client.post('/admin/backups/config-from-backup/dismiss', data=ADMIN)
    assert not (home / 'data' / 'config-from-backup.yml').exists()


def test_restore_with_nothing_ticked(client, home, tmp_path):
    name = upload(client, tmp_path)
    resp = client.post(f'/admin/backups/restore-upload/{name}', data={**ADMIN, 'password': '4321'}, follow_redirects=True)
    assert 'Choose at least one part' in resp.get_data(as_text=True)
    assert note_content(home) == 'current'


def restore_upload(client, tmp_path, note):
    name = upload(client, tmp_path, note=note)
    client.post(f'/admin/backups/restore-upload/{name}', data={**ADMIN, 'password': '4321', 'parts': ['database']})
    backup.jobs.wait(10)


def test_only_safety_backups_restore_from_the_list(client, home, tmp_path):
    create(client)
    own = zips(home)[0]
    restore_upload(client, tmp_path, 'restored')
    [safety] = [n for n in zips(home) if n.startswith('homehub-safety-backup-')]

    page = client.get('/admin/backups?user=Administrator').get_data(as_text=True)
    assert f'/admin/backups/restore/{safety}' in page
    assert f'/admin/backups/restore/{own}' not in page
    assert 'Restore it to undo that restore.' in page
    # The route agrees with the page
    assert client.get(f'/admin/backups/restore/{own}?user=Administrator').status_code == 404
    client.post(f'/admin/backups/restore/{own}', data={**ADMIN, 'password': '4321', 'parts': ['database']})
    backup.jobs.wait(5)
    assert note_content(home) == 'restored'

    # Restoring the safety backup undoes the restore, and keeps the safety backup in the list
    client.post(f'/admin/backups/restore/{safety}', data={**ADMIN, 'password': '4321', 'parts': ['database']})
    backup.jobs.wait(10)
    assert note_content(home) == 'current'
    assert safety in zips(home)


def test_only_the_newest_safety_backups_are_kept(client, home, tmp_path):
    create(client)
    own = zips(home)[0]
    for i in range(backup.KEEP_SAFETY_BACKUPS + 2):
        restore_upload(client, tmp_path, f'round{i}')
    safety = [n for n in zips(home) if n.startswith('homehub-safety-backup-')]
    assert len(safety) == backup.KEEP_SAFETY_BACKUPS
    # Your own backups are never removed
    assert own in zips(home)


def test_pruning_spares_the_backup_being_restored(home):
    base = str(home)
    made = [backup.create_backup(base_dir=base, kind='safety') for _ in range(backup.KEEP_SAFETY_BACKUPS)]
    # Made within the same second; file times say which is oldest
    for age, path in enumerate(reversed(made), start=1):
        os.utime(path, (1_700_000_000 - age * 60, 1_700_000_000 - age * 60))
    oldest = made[0]
    # Restoring the oldest safety backup makes a new one; the oldest stays, being the source
    backup.restore_backup(oldest, base_dir=base, parts=['database'])
    names = [b['name'] for b in backup.list_backups(base) if b['kind'] == 'safety']
    assert os.path.basename(oldest) in names
    assert len(names) == backup.KEEP_SAFETY_BACKUPS + 1


def test_home_page_links_to_backups(client):
    html = client.get('/').get_data(as_text=True)
    assert 'action="/admin/backups"' in html
