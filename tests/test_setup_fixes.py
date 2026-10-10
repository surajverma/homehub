"""Health check, SQLite settings, config fallback, upload names, PDF/QR storage and the site login."""
import io
import os
import sqlite3

import pytest

from app import admin, create_app, db, migrations
from app import config as config_module
from app.blueprints import media_pdfs, uploads
from app.models import File, PDF, QRCode


def make_client(db_uri='sqlite://'):
    app = create_app({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': db_uri,
        'SECRET_KEY': 'test',
    })
    with app.app_context():
        db.create_all()
        db.session.execute(db.text("CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY, value TEXT)"))
        db.session.commit()
    return app.test_client()


@pytest.fixture()
def client():
    return make_client()


def use_config(tmp_path, monkeypatch, text):
    path = tmp_path / 'config.yml'
    path.write_text(text, encoding='utf-8')
    monkeypatch.setattr(config_module, 'CONFIG_PATH', str(path))
    monkeypatch.setattr(config_module, '_cache', {'key': None, 'config': None})


# --- /healthz ---------------------------------------------------------------------------------

def test_healthz_answers_without_logging_in(tmp_path, monkeypatch):
    use_config(tmp_path, monkeypatch, 'password: "secret"\n')
    client = make_client()
    assert client.get('/').status_code == 302
    resp = client.get('/healthz')
    assert resp.status_code == 200 and resp.get_json() == {'ok': True}


def test_healthz_reports_a_broken_database(client, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError('database is gone')
    monkeypatch.setattr(db.session, 'execute', broken)
    resp = client.get('/healthz')
    assert resp.status_code == 503 and resp.get_json() == {'ok': False}


# --- SQLite ------------------------------------------------------------------------------------

def test_sqlite_runs_in_wal_mode_with_a_busy_timeout(tmp_path):
    path = tmp_path / 'app.db'
    client = make_client('sqlite:///' + str(path).replace(os.sep, '/'))
    with client.application.app_context():
        assert db.session.execute(db.text('PRAGMA journal_mode')).scalar() == 'wal'
        assert db.session.execute(db.text('PRAGMA busy_timeout')).scalar() == 5000
        db.session.remove()
        db.engine.dispose()


# --- config.yml missing ------------------------------------------------------------------------

@pytest.mark.parametrize('as_folder', [False, True])
def test_missing_config_falls_back_to_the_example(tmp_path, monkeypatch, caplog, as_folder):
    path = tmp_path / 'config.yml'
    if as_folder:
        path.mkdir()
    monkeypatch.setattr(config_module, 'CONFIG_PATH', str(path))
    monkeypatch.setattr(config_module, '_cache', {'key': None, 'config': None})
    monkeypatch.setattr(config_module, '_warned', set())
    with caplog.at_level('WARNING'):
        client = make_client()
        assert client.get('/').status_code == 200
        client.get('/')
    cfg = config_module.load_config()
    assert cfg['admin_name'] == 'Administrator' and cfg['family_members']
    warnings = [r.getMessage() for r in caplog.records if 'config.yml' in r.getMessage()]
    # Said once, not on every request
    assert len(warnings) == 1
    assert ('is a folder' if as_folder else 'is missing') in warnings[0]


def test_config_and_example_both_missing_still_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, 'CONFIG_PATH', str(tmp_path / 'config.yml'))
    monkeypatch.setattr(config_module, 'EXAMPLE_CONFIG_PATH', str(tmp_path / 'config-example.yml'))
    monkeypatch.setattr(config_module, '_cache', {'key': None, 'config': None})
    monkeypatch.setattr(config_module, '_warned', set())
    assert make_client().get('/').status_code == 200


# --- Shared Cloud ------------------------------------------------------------------------------

def upload(client, name, content):
    return client.post('/upload', data={'files': (io.BytesIO(content), name), 'creator': 'Alice'},
                       content_type='multipart/form-data')


def test_uploading_the_same_name_twice_keeps_both_files(client):
    upload(client, 'notes.txt', b'first')
    upload(client, 'notes.txt', b'second')
    with client.application.app_context():
        rows = File.query.order_by(File.id).all()
        assert [r.filename for r in rows] == ['notes.txt', 'notes.txt']
        first, second = (r.disk_name for r in rows)
    assert first == 'notes.txt' and second != first and second.endswith('.txt')
    assert client.get(f'/uploads/{first}').data == b'first'
    resp = client.get(f'/uploads/{second}')
    assert resp.data == b'second'
    # Downloaded under the name it was uploaded with
    assert 'filename=notes.txt' in resp.headers['Content-Disposition']
    page = client.get('/upload').get_data(as_text=True)
    assert f'href="/uploads/{second}"' in page and f'href="/uploads/preview/{second}"' in page


def test_deleting_one_duplicate_leaves_the_other(client):
    upload(client, 'notes.txt', b'first')
    upload(client, 'notes.txt', b'second')
    with client.application.app_context():
        first_id, second_id = (r.id for r in File.query.order_by(File.id))
        second_name = db.session.get(File, second_id).disk_name
    client.post(f'/upload/delete/{first_id}', data={'user': 'Alice'})
    assert not os.path.exists(os.path.join(uploads.UPLOAD_FOLDER, 'notes.txt'))
    assert client.get(f'/uploads/{second_name}').data == b'second'


def test_uploads_from_before_stored_names_still_work(client):
    with open(os.path.join(uploads.UPLOAD_FOLDER, 'old.txt'), 'wb') as f:
        f.write(b'old')
    with client.application.app_context():
        # Two rows for one file on disk, which is what overwriting used to leave behind
        db.session.add_all([File(filename='old.txt', creator='Alice'), File(filename='old.txt', creator='Alice')])
        db.session.commit()
        first_id, second_id = (r.id for r in File.query.order_by(File.id))
    assert 'href="/uploads/old.txt"' in client.get('/upload').get_data(as_text=True)
    assert client.get('/uploads/old.txt').data == b'old'
    client.post(f'/upload/delete/{first_id}', data={'user': 'Alice'})
    assert client.get('/uploads/old.txt').data == b'old'
    client.post(f'/upload/delete/{second_id}', data={'user': 'Alice'})
    assert not os.path.exists(os.path.join(uploads.UPLOAD_FOLDER, 'old.txt'))


def test_upload_with_an_unusable_name_gets_a_fallback_name(client):
    upload(client, '...', b'data')
    with client.application.app_context():
        assert File.query.one().filename == 'file'


# --- PDFs --------------------------------------------------------------------------------------

def compress(client, name, content):
    return client.post('/pdfs', data={'pdf': (io.BytesIO(content), name), 'creator': 'Alice'},
                       content_type='multipart/form-data')


def test_pdf_original_is_removed_and_same_name_keeps_both(client):
    # Ghostscript is not needed: when it fails or is missing, the upload is kept as the result
    compress(client, 'bill.pdf', b'%PDF-first')
    compress(client, 'bill.pdf', b'%PDF-second')
    with client.application.app_context():
        rows = PDF.query.order_by(PDF.id).all()
        assert [r.filename for r in rows] == ['bill.pdf', 'bill.pdf']
        first, second = (r.compressed_path for r in rows)
        first_id = rows[0].id
    assert first == 'compressed_bill.pdf' and second != first
    # Only the two results are on disk: no originals, no temporary uploads
    assert sorted(os.listdir(media_pdfs.PDF_FOLDER)) == sorted([first, second])
    resp = client.get(f'/pdfs/{second}')
    assert resp.status_code == 200 and 'filename=compressed_bill.pdf' in resp.headers['Content-Disposition']
    client.post(f'/pdfs/delete/{first_id}', data={'user': 'Alice'})
    assert os.listdir(media_pdfs.PDF_FOLDER) == [second]


def test_deleting_an_older_pdf_row_removes_its_leftover_original(client):
    for name in ('scan.pdf', 'compressed_scan.pdf'):
        with open(os.path.join(media_pdfs.PDF_FOLDER, name), 'wb') as f:
            f.write(b'%PDF')
    with client.application.app_context():
        db.session.add(PDF(filename='scan.pdf', creator='Alice', compressed_path='compressed_scan.pdf'))
        db.session.commit()
        pid = PDF.query.one().id
    client.post(f'/pdfs/delete/{pid}', data={'user': 'Alice'})
    assert os.listdir(media_pdfs.PDF_FOLDER) == []


def test_deleting_a_pdf_never_removes_another_rows_result(client):
    with open(os.path.join(media_pdfs.PDF_FOLDER, 'compressed_a.pdf'), 'wb') as f:
        f.write(b'%PDF')
    with client.application.app_context():
        # Someone uploaded a file that was itself called compressed_a.pdf
        db.session.add_all([
            PDF(filename='a.pdf', creator='Alice', compressed_path='compressed_a.pdf'),
            PDF(filename='compressed_a.pdf', creator='Alice', compressed_path='compressed_compressed_a.pdf'),
        ])
        db.session.commit()
        second_id = PDF.query.order_by(PDF.id.desc()).first().id
    client.post(f'/pdfs/delete/{second_id}', data={'user': 'Alice'})
    assert os.listdir(media_pdfs.PDF_FOLDER) == ['compressed_a.pdf']


# --- QR history --------------------------------------------------------------------------------

def test_qr_history_is_served_from_the_stored_text(client, tmp_path, monkeypatch):
    from app.blueprints import qr
    static_dir = tmp_path / 'static'
    static_dir.mkdir()
    monkeypatch.setattr(qr, 'STATIC_DIR', str(static_dir))
    page = client.post('/qr', data={'qrtext': 'https://example.com', 'creator': 'Alice'}).get_data(as_text=True)
    with client.application.app_context():
        rec = QRCode.query.one()
        assert rec.filename == ''
    # Nothing is written next to the app's own static files any more
    assert os.listdir(static_dir) == []
    assert f'href="/qr/image/{rec.id}.png"' in page
    view = client.get(f'/qr/image/{rec.id}.png')
    assert view.status_code == 200 and view.mimetype == 'image/png' and view.data.startswith(b'\x89PNG')
    assert 'attachment' not in view.headers.get('Content-Disposition', '')
    download = client.get(f'/qr/image/{rec.id}.png?download=1')
    assert 'attachment' in download.headers['Content-Disposition']
    assert client.get('/qr/image/999.png').status_code == 404


def test_deleting_an_older_qr_code_removes_its_png(client, tmp_path, monkeypatch):
    from app.blueprints import qr
    static_dir = tmp_path / 'static'
    static_dir.mkdir()
    monkeypatch.setattr(qr, 'STATIC_DIR', str(static_dir))
    (static_dir / 'qr_5_123.png').write_bytes(b'png')
    (static_dir / 'output.css').write_text('keep me')
    with client.application.app_context():
        db.session.add_all([
            QRCode(text='hello', filename='qr_5_123.png', creator='Alice'),
            # A filename that is not one of ours is never touched
            QRCode(text='hello', filename='output.css', creator='Alice'),
        ])
        db.session.commit()
        ids = [r.id for r in QRCode.query.order_by(QRCode.id)]
    assert client.get(f'/qr/image/{ids[0]}.png').status_code == 200
    for qr_id in ids:
        client.post(f'/qr/delete/{qr_id}', data={'user': 'Alice'})
    assert os.listdir(static_dir) == ['output.css']


# --- Media downloads ---------------------------------------------------------------------------

def test_downloads_left_pending_by_a_restart_are_marked_failed(tmp_path):
    conn = sqlite3.connect(str(tmp_path / 'app.db'))
    conn.execute("CREATE TABLE media (id INTEGER PRIMARY KEY, status TEXT, progress TEXT)")
    conn.executemany("INSERT INTO media (id, status, progress) VALUES (?, ?, ?)",
                     [(1, 'pending', '40%'), (2, 'done', None), (3, 'error', None)])
    assert migrations.fail_interrupted_downloads(conn.cursor()) == 1
    assert list(conn.execute("SELECT id, status, progress FROM media ORDER BY id")) == [
        (1, 'error', None), (2, 'done', None), (3, 'error', None)]


# --- Site login --------------------------------------------------------------------------------

def login(client, password):
    return client.post('/login', data={'password': password})


def test_site_password_is_kept_as_a_salted_hash(tmp_path, monkeypatch):
    use_config(tmp_path, monkeypatch, 'password: "secret"\n')
    cfg = config_module.load_config()
    assert 'password' not in cfg
    assert cfg['password_hash'].startswith('pbkdf2:sha256') and 'secret' not in cfg['password_hash']


@pytest.mark.parametrize('password', ['secret', 'p&ss <word> "quoted"', 'पासवर्ड'])
def test_login_works_with_the_password_from_config(tmp_path, monkeypatch, password):
    use_config(tmp_path, monkeypatch, 'password: %r\n' % password if password.isascii() else f'password: "{password}"\n')
    client = make_client()
    assert 'Invalid password' in login(client, 'nope').get_data(as_text=True)
    assert client.get('/').status_code == 302
    resp = login(client, password)
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/')
    assert client.get('/').status_code == 200


def test_numeric_site_password_in_config_works(tmp_path, monkeypatch):
    use_config(tmp_path, monkeypatch, 'password: 1234\n')
    client = make_client()
    assert login(client, '1234').status_code == 302


def test_login_locks_out_after_repeated_wrong_passwords(tmp_path, monkeypatch):
    use_config(tmp_path, monkeypatch, 'password: "secret"\n')
    client = make_client()
    for _attempt in range(admin.MAX_FAILED_ATTEMPTS):
        assert 'Invalid password' in login(client, 'nope').get_data(as_text=True)
    # Locked: even the right password is refused until the wait is over
    locked = login(client, 'secret')
    assert locked.status_code == 200 and 'Too many attempts' in locked.get_data(as_text=True)
    # A reload shows the same disabled form with the seconds left for the countdown
    page = client.get('/login').get_data(as_text=True)
    assert 'id="loginLockout"' in page and 'data-seconds="' in page and 'js/pages/login.js' in page
    assert page.count(' disabled>') == 2
    assert client.get('/').status_code == 302
    # The wait ends
    count, _until = admin._failed_attempts['login:127.0.0.1']
    admin._failed_attempts['login:127.0.0.1'] = (count, 0.0)
    assert login(client, 'secret').status_code == 302
    assert 'login:127.0.0.1' not in admin._failed_attempts


def test_login_form_is_usable_when_not_locked_out(tmp_path, monkeypatch):
    use_config(tmp_path, monkeypatch, 'password: "secret"\n')
    client = make_client()
    login(client, 'nope')
    page = client.get('/login').get_data(as_text=True)
    assert 'loginLockout' not in page and ' disabled' not in page.split('id="loginForm"')[1].split('</form>')[0]


def test_site_login_and_admin_unlock_are_counted_separately(tmp_path, monkeypatch):
    use_config(tmp_path, monkeypatch, 'password: "secret"\n')
    client = make_client()
    for _attempt in range(admin.MAX_FAILED_ATTEMPTS):
        login(client, 'nope')
    assert admin.unlock_wait_seconds('127.0.0.1') == 0


def test_session_cookie_is_samesite_lax(tmp_path, monkeypatch):
    use_config(tmp_path, monkeypatch, 'password: "secret"\n')
    client = make_client()
    cookie = login(client, 'secret').headers['Set-Cookie']
    assert 'SameSite=Lax' in cookie


# --- URL shortener toggle ----------------------------------------------------------------------

def test_shortener_is_off_for_new_configs_but_links_still_resolve(client):
    from app.models import ShortURL
    assert 'href="/shorten"' not in client.get('/').get_data(as_text=True)
    with client.application.app_context():
        db.session.add(ShortURL(original_url='https://example.com/page', short_code='abc123', creator='Alice'))
        db.session.commit()
    resp = client.get('/s/abc123')
    assert resp.status_code in (301, 302) and resp.headers['Location'] == 'https://example.com/page'


def test_config_that_turns_the_shortener_on_keeps_its_link(tmp_path, monkeypatch):
    use_config(tmp_path, monkeypatch, 'feature_toggles:\n  url_shortener: true\n')
    assert 'href="/shorten"' in make_client().get('/').get_data(as_text=True)


# --- Leftover PDF originals --------------------------------------------------------------------

def test_startup_sweep_removes_only_unneeded_pdf_originals(tmp_path):
    folder = tmp_path / 'pdfs'
    folder.mkdir()
    for name in ('scan.pdf', 'compressed_scan.pdf',          # old pair: the original can go
                 'lost.pdf',                                  # its compressed file is missing: keep
                 'compressed_a.pdf', 'compressed_compressed_a.pdf',  # an upload that is another row's result
                 'stray.pdf'):                                # no row at all: not ours to judge
        (folder / name).write_bytes(b'%PDF')
    conn = sqlite3.connect(str(tmp_path / 'app.db'))
    conn.execute("CREATE TABLE pdf (id INTEGER PRIMARY KEY, filename TEXT, compressed_path TEXT)")
    conn.executemany("INSERT INTO pdf (filename, compressed_path) VALUES (?, ?)", [
        ('scan.pdf', 'compressed_scan.pdf'),
        ('scan.pdf', 'compressed_scan.pdf'),
        ('lost.pdf', 'compressed_lost.pdf'),
        ('a.pdf', 'compressed_a.pdf'),
        ('compressed_a.pdf', 'compressed_compressed_a.pdf'),
        ('../scan.pdf', 'compressed_scan.pdf'),
        (None, None),
    ])
    assert migrations.remove_leftover_pdf_originals(conn.cursor(), str(folder)) == ['scan.pdf']
    assert sorted(os.listdir(folder)) == ['compressed_a.pdf', 'compressed_compressed_a.pdf', 'compressed_scan.pdf',
                                          'lost.pdf', 'stray.pdf']
    # Running it again finds nothing
    assert migrations.remove_leftover_pdf_originals(conn.cursor(), str(folder)) == []


def test_journal_mode_can_be_switched_back(tmp_path, monkeypatch):
    monkeypatch.setenv('SQLITE_JOURNAL_MODE', 'delete')
    client = make_client('sqlite:///' + str(tmp_path / 'app.db').replace(os.sep, '/'))
    with client.application.app_context():
        assert db.session.execute(db.text('PRAGMA journal_mode')).scalar() == 'delete'
        db.session.remove()
        db.engine.dispose()
