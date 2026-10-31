import csv
import io
import json
import os
import sqlite3
import zipfile
from datetime import date

import pytest

from app import backup, create_app, db
from app.models import ExpenseEntry


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
    return make_app().test_client()


def add_entries(client, *entries):
    with client.application.app_context():
        db.session.add_all(entries)
        db.session.commit()


def read_csv(response):
    body = response.get_data()
    assert body.startswith('\ufeff'.encode('utf-8'))
    return list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))


# --- CSV export ---

def test_export_month_lists_entries_and_skips_skipped_days(client):
    add_entries(
        client,
        ExpenseEntry(date=date(2026, 10, 2), title='दूध', category='Food', unit_price=30, quantity=2, amount=60, payer='Alice',
                     split_with=json.dumps(['Alice', 'Bob'])),
        ExpenseEntry(date=date(2026, 10, 3), title='Paper', amount=10, payer='Bob', recurring_id=1, skipped=True),
        ExpenseEntry(date=date(2026, 10, 5), title='Rent', amount=500, payer='Bob',
                     split_with=json.dumps({'mode': 'percent', 'weights': {'Alice': 60, 'Bob': 40}})),
        ExpenseEntry(date=date(2026, 11, 1), title='Next month', amount=5, payer='Alice'),
    )
    resp = client.get('/expenses/export.csv?month=2026-10')
    assert resp.status_code == 200
    assert resp.mimetype == 'text/csv'
    assert 'homehub-expenses-2026-10.csv' in resp.headers['Content-Disposition']
    rows = read_csv(resp)
    assert [r['title'] for r in rows] == ['दूध', 'Rent']
    milk, rent = rows
    assert list(milk) == ['date', 'title', 'category', 'unit_price', 'quantity', 'amount', 'payer', 'split', 'recurring', 'settlement', 'added_by']
    assert milk['date'] == '2026-10-02'
    assert milk['unit_price'] == '30.00' and milk['quantity'] == '2' and milk['amount'] == '60.00'
    assert milk['split'] == 'Alice;Bob'
    assert milk['recurring'] == 'no' and milk['settlement'] == 'no'
    assert rent['split'] == 'Alice (60%);Bob (40%)'


def test_export_date_range(client):
    add_entries(
        client,
        ExpenseEntry(date=date(2026, 9, 30), title='Before', amount=1, payer='Alice'),
        ExpenseEntry(date=date(2026, 10, 1), title='First', amount=2, payer='Alice', recurring_id=3),
        ExpenseEntry(date=date(2026, 10, 15), title='Last', amount=3, payer='Alice', is_settlement=True,
                     split_with=json.dumps(['Bob'])),
        ExpenseEntry(date=date(2026, 10, 16), title='After', amount=4, payer='Alice'),
    )
    resp = client.get('/expenses/export.csv?start=2026-10-01&end=2026-10-15')
    assert resp.status_code == 200
    assert 'homehub-expenses-2026-10-01-to-2026-10-15.csv' in resp.headers['Content-Disposition']
    rows = read_csv(resp)
    assert [r['title'] for r in rows] == ['First', 'Last']
    assert rows[0]['recurring'] == 'yes'
    assert rows[1]['settlement'] == 'yes' and rows[1]['split'] == 'Bob'


def test_export_escapes_formulas_but_keeps_numbers_numeric(client):
    add_entries(
        client,
        ExpenseEntry(date=date(2026, 10, 2), title='=HYPERLINK("http://evil")', category='+cat', amount=-5, payer='@bob'),
        ExpenseEntry(date=date(2026, 10, 3), title='-refund', category='\tTab', amount=7, payer='\rAlice'),
    )
    rows = read_csv(client.get('/expenses/export.csv?month=2026-10'))
    first, second = rows
    assert first['title'] == '\'=HYPERLINK("http://evil")'
    assert first['category'] == "'+cat"
    assert first['payer'] == "'@bob"
    assert first['amount'] == '-5.00'
    assert second['title'] == "'-refund"
    assert second['category'] == "'\tTab"
    assert second['payer'] == "'\rAlice"


@pytest.mark.parametrize('query', [
    'month=2026-13',
    'month=October',
    'start=2026-10-01',
    'start=2026-10-10&end=2026-10-01',
    'start=yesterday&end=today',
    'month=2026-10&start=2026-10-01&end=2026-10-02',
])
def test_export_rejects_bad_parameters(client, query):
    resp = client.get('/expenses/export.csv?' + query)
    assert resp.status_code == 400
    assert resp.mimetype == 'text/plain'
    assert resp.get_data(as_text=True)


def test_export_defaults_to_current_month(client):
    resp = client.get('/expenses/export.csv')
    assert resp.status_code == 200
    assert f'homehub-expenses-{date.today():%Y-%m}.csv' in resp.headers['Content-Disposition']


def test_export_needs_site_login(client, tmp_path, monkeypatch):
    from werkzeug.security import generate_password_hash
    from app import config as config_module
    path = tmp_path / 'config.yml'
    path.write_text('password_hash: "%s"\n' % generate_password_hash('pw'), encoding='utf-8')
    monkeypatch.setattr(config_module, 'CONFIG_PATH', str(path))
    monkeypatch.setattr(config_module, '_cache', {'key': None, 'config': None})
    resp = client.get('/expenses/export.csv?month=2026-10')
    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']


# --- Backup ---

def make_data(base, db_tables=('note', 'shopping_item', 'chore'), note='hello'):
    """A HomeHub folder layout under ``base`` with a small database, a config.yml and a few files."""
    data = base / 'data'
    data.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data / 'app.db')
    for table in db_tables:
        conn.execute(f'CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, content TEXT)')
    conn.execute('DELETE FROM note')
    conn.execute('INSERT INTO note(content) VALUES (?)', (note,))
    conn.commit()
    conn.close()
    (data / 'secret_key').write_text('secret-' + note, encoding='utf-8')
    (data / 'backups').mkdir(exist_ok=True)
    (data / 'backups' / 'notes.txt').write_bytes(b'not a backup')
    for folder in ('uploads', 'pdfs', 'media'):
        (base / folder).mkdir(exist_ok=True)
    (base / 'uploads' / 'photo.jpg').write_bytes(b'jpg-' + note.encode())
    (base / 'uploads' / 'sub').mkdir(exist_ok=True)
    (base / 'uploads' / 'sub' / 'doc.txt').write_text(note, encoding='utf-8')
    (base / 'pdfs' / 'bill.pdf').write_bytes(b'%PDF-' + note.encode())
    (base / 'media' / 'video.mp4').write_bytes(b'video-' + note.encode())
    (base / 'config.yml').write_text(f'instance_name: "{note}"\npassword: "site-pw-{note}"\n', encoding='utf-8')
    return base


def note_content(base):
    conn = sqlite3.connect(base / 'data' / 'app.db')
    try:
        return conn.execute('SELECT content FROM note').fetchone()[0]
    finally:
        conn.close()


def backup_names(base):
    return sorted(n for n in os.listdir(base / 'data' / 'backups') if n.endswith('.zip'))


def test_backup_zip_contents_and_manifest(tmp_path):
    base = make_data(tmp_path / 'home')
    path = backup.create_backup(base_dir=str(base))
    assert os.path.dirname(path) == str(base / 'data' / 'backups')
    assert os.path.basename(path).startswith('homehub-backup-')
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read('manifest.json'))
        db_bytes = zf.read('data/app.db')
    assert names == {'manifest.json', 'data/app.db', 'data/secret_key', 'uploads/photo.jpg', 'uploads/sub/doc.txt',
                     'pdfs/bill.pdf', 'config/config.yml'}
    assert db_bytes.startswith(b'SQLite format 3\x00')
    assert manifest['app'] == 'HomeHub'
    assert manifest['kind'] == 'backup'
    assert manifest['created_at'].endswith('Z')
    assert manifest['parts'] == ['database', 'uploads', 'pdfs', 'config']
    assert set(manifest['files']) == names - {'manifest.json'}
    # Nothing from data/backups, and no leftover temp files beside the zip
    assert not any(n.startswith('data/backups') for n in names)
    assert sorted(os.listdir(base / 'data' / 'backups')) == sorted(['notes.txt', os.path.basename(path)])


def test_backup_parts_are_chosen(tmp_path):
    base = make_data(tmp_path / 'home')
    out = tmp_path / 'elsewhere' / 'b.zip'
    seen = []
    path = backup.create_backup(str(out), parts=['media'], base_dir=str(base), progress=lambda d, t: seen.append((d, t)))
    assert path == str(out)
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        assert json.loads(zf.read('manifest.json'))['parts'] == ['database', 'media']
    # The database always goes in, even when not asked for
    assert names == {'manifest.json', 'data/app.db', 'data/secret_key', 'media/video.mp4'}
    assert seen and seen[-1][0] == seen[-1][1]


def test_part_sizes(tmp_path):
    base = make_data(tmp_path / 'home', note='abc')
    sizes = backup.part_sizes(str(base))
    assert sizes['uploads'] == len(b'jpg-abc') + len('abc')
    assert sizes['media'] == len(b'video-abc')
    assert sizes['database'] > 0 and sizes['config'] > 0


def test_backup_without_database_fails(tmp_path):
    with pytest.raises(backup.BackupError) as exc:
        backup.create_backup(base_dir=str(tmp_path))
    assert exc.value.code == 'no_database'


def test_backup_refuses_without_disk_space(tmp_path, monkeypatch):
    base = make_data(tmp_path / 'home')
    monkeypatch.setattr(backup.shutil, 'disk_usage', lambda _p: type('U', (), {'free': 10})())
    with pytest.raises(backup.BackupError) as exc:
        backup.create_backup(base_dir=str(base))
    assert exc.value.code == 'no_space'
    assert backup_names(base) == []


def test_list_backups_and_names_stay_inside_the_folder(tmp_path):
    base = make_data(tmp_path / 'home')
    first = backup.create_backup(base_dir=str(base))
    safety = backup.create_backup(base_dir=str(base), kind='safety')
    (base / 'data' / 'backups' / 'junk.zip').write_bytes(b'not a zip')
    listed = {b['name']: b for b in backup.list_backups(str(base))}
    assert set(listed) == {os.path.basename(first), os.path.basename(safety), 'junk.zip'}
    assert listed[os.path.basename(safety)]['kind'] == 'safety'
    assert listed[os.path.basename(first)]['parts']['uploads']['files'] == 2
    assert listed['junk.zip']['error'] == 'not_zip'
    assert backup.backup_file(os.path.basename(first), str(base)) == os.path.realpath(first)
    for bad in ('../app.db', '..\\app.db', 'notes.txt', '.hidden.zip', 'x/../../app.db', '', 'missing.zip'):
        assert backup.backup_file(bad, str(base)) is None


def test_stale_part_files_are_removed(tmp_path):
    base = make_data(tmp_path / 'home')
    folder = base / 'data' / 'backups'
    old = folder / '.homehub-backup-old.part'
    fresh = folder / '.homehub-backup-new.part'
    old.write_bytes(b'x')
    fresh.write_bytes(b'x')
    os.utime(old, (0, 0))
    staging = base / 'data' / '.homehub-restore-dead'
    staging.mkdir()
    os.utime(staging, (0, 0))
    assert backup.remove_stale_parts(str(base)) == 2
    assert not old.exists() and fresh.exists() and not staging.exists()


def test_masked_config_hides_passwords_and_keys():
    text = 'instance_name: Home\npassword: hunter2\nweather:\n  api_key: abc\n  enabled: true\nreminders:\n  categories:\n    - key: health\n'
    masked = backup.masked_config(text)
    assert 'hunter2' not in masked and 'abc' not in masked
    assert 'Home' in masked and 'health' in masked
    assert backup.masked_config('a: [unclosed') is None


# --- Restore ---

def test_restore_round_trip_with_safety_backup(tmp_path):
    source = make_data(tmp_path / 'source', note='from-backup')
    zip_path = backup.create_backup(str(tmp_path / 'b.zip'), parts=backup.PARTS, base_dir=str(source))

    target = make_data(tmp_path / 'target', note='current')
    (target / 'uploads' / 'only-here.txt').write_text('x', encoding='utf-8')
    result = backup.restore_backup(zip_path, base_dir=str(target))

    assert note_content(target) == 'from-backup'
    assert (target / 'data' / 'secret_key').read_text(encoding='utf-8') == 'secret-from-backup'
    assert (target / 'uploads' / 'photo.jpg').read_bytes() == b'jpg-from-backup'
    assert (target / 'uploads' / 'sub' / 'doc.txt').read_text(encoding='utf-8') == 'from-backup'
    assert not (target / 'uploads' / 'only-here.txt').exists()
    assert (target / 'media' / 'video.mp4').read_bytes() == b'video-from-backup'
    assert set(result['restored']) == {'data/app.db', 'data/secret_key', 'uploads/', 'pdfs/', 'media/'}

    # config.yml itself is never overwritten; the backup's copy is put beside the data
    assert 'site-pw-current' in (target / 'config.yml').read_text(encoding='utf-8')
    assert result['config_from_backup'] == str(target / 'data' / 'config-from-backup.yml')
    assert 'from-backup' in (target / 'data' / 'config-from-backup.yml').read_text(encoding='utf-8')

    # The safety backup holds what was replaced, as it was before the restore
    safety = result['safety_backup']
    assert safety and os.path.isfile(safety)
    assert os.path.basename(safety).startswith('homehub-safety-backup-')
    with zipfile.ZipFile(safety) as zf:
        assert zf.read('uploads/only-here.txt') == b'x'
        assert zf.read('media/video.mp4') == b'video-current'
        assert json.loads(zf.read('manifest.json'))['kind'] == 'safety'
    # No staging folder left behind in data/
    assert sorted(os.listdir(target / 'data')) == ['app.db', 'backups', 'config-from-backup.yml', 'secret_key']


def test_restore_only_chosen_parts(tmp_path):
    source = make_data(tmp_path / 'source', note='from-backup')
    zip_path = backup.create_backup(str(tmp_path / 'b.zip'), parts=backup.PARTS, base_dir=str(source))
    target = make_data(tmp_path / 'target', note='current')
    result = backup.restore_backup(zip_path, base_dir=str(target), parts=['uploads'])
    assert result['restored'] == ['uploads/']
    assert (target / 'uploads' / 'photo.jpg').read_bytes() == b'jpg-from-backup'
    assert note_content(target) == 'current'
    assert (target / 'media' / 'video.mp4').read_bytes() == b'video-current'
    assert result['config_from_backup'] is None
    with zipfile.ZipFile(result['safety_backup']) as zf:
        assert json.loads(zf.read('manifest.json'))['parts'] == ['database', 'uploads']


def test_restore_without_media_in_zip_leaves_media_alone(tmp_path):
    source = make_data(tmp_path / 'source', note='from-backup')
    zip_path = backup.create_backup(str(tmp_path / 'b.zip'), base_dir=str(source))
    target = make_data(tmp_path / 'target', note='current')
    backup.restore_backup(zip_path, base_dir=str(target))
    assert (target / 'media' / 'video.mp4').read_bytes() == b'video-current'


def test_restore_nothing_selected(tmp_path):
    zip_path = backup.create_backup(str(tmp_path / 'b.zip'), base_dir=str(make_data(tmp_path / 'source')))
    target = make_data(tmp_path / 'target', note='current')
    with pytest.raises(backup.BackupError) as exc:
        backup.restore_backup(zip_path, base_dir=str(target), parts=['media'])
    assert exc.value.code == 'nothing_selected'
    assert backup_names(target) == []


def write_zip(path, entries):
    with zipfile.ZipFile(path, 'w') as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return str(path)


def valid_entries(tmp_path):
    zip_path = backup.create_backup(str(tmp_path / 'good.zip'), base_dir=str(make_data(tmp_path / 'src')))
    with zipfile.ZipFile(zip_path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


@pytest.mark.parametrize('bad_name', ['../evil.txt', 'uploads/../../evil.txt', '/etc/evil', '\\evil.txt', 'C:/evil.txt', 'uploads\\..\\..\\evil.txt'])
def test_restore_rejects_zip_slip(tmp_path, bad_name):
    entries = valid_entries(tmp_path)
    entries[bad_name] = b'pwned'
    zip_path = write_zip(tmp_path / 'bad.zip', entries)
    target = make_data(tmp_path / 'target', note='current')
    with pytest.raises(backup.BackupError) as exc:
        backup.restore_backup(zip_path, base_dir=str(target))
    assert exc.value.code == 'unsafe'
    assert note_content(target) == 'current'
    assert not (tmp_path / 'evil.txt').exists()
    # Rejected before the safety backup, so nothing new was written
    assert backup_names(target) == []


@pytest.mark.parametrize('change', ['no_manifest', 'no_db', 'not_sqlite', 'missing_tables', 'unexpected_file', 'not_homehub'])
def test_restore_rejects_invalid_backups(tmp_path, change):
    entries = valid_entries(tmp_path)
    if change == 'no_manifest':
        del entries['manifest.json']
    elif change == 'no_db':
        del entries['data/app.db']
    elif change == 'not_sqlite':
        entries['data/app.db'] = b'not a database'
    elif change == 'missing_tables':
        other = tmp_path / 'other.db'
        conn = sqlite3.connect(other)
        conn.execute('CREATE TABLE something (id INTEGER)')
        conn.commit()
        conn.close()
        entries['data/app.db'] = other.read_bytes()
    elif change == 'unexpected_file':
        entries['run.py'] = b'print(1)'
    elif change == 'not_homehub':
        entries['manifest.json'] = b'{"app": "Other"}'
    zip_path = write_zip(tmp_path / 'bad.zip', entries)
    with pytest.raises(backup.BackupError):
        backup.validate_backup(zip_path)


def test_restore_rejects_non_zip(tmp_path):
    path = tmp_path / 'x.zip'
    path.write_bytes(b'nope')
    with pytest.raises(backup.BackupError):
        backup.validate_backup(str(path))


def test_restore_cli_round_trip(tmp_path, monkeypatch):
    source = make_data(tmp_path / 'source', note='from-backup')
    zip_path = backup.create_backup(str(tmp_path / 'b.zip'), parts=backup.PARTS, base_dir=str(source))
    target = make_data(tmp_path / 'target', note='current')
    monkeypatch.setattr(backup, 'BASE_DIR', str(target))
    runner = make_app().test_cli_runner()

    declined = runner.invoke(args=['restore', zip_path], input='n\n')
    assert declined.exit_code != 0
    assert note_content(target) == 'current'

    result = runner.invoke(args=['restore', zip_path, '--yes'])
    assert result.exit_code == 0, result.output
    assert note_content(target) == 'from-backup'
    assert 'Safety backup of the previous data:' in result.output
    assert 'config-from-backup.yml' in result.output
    assert 'docker compose restart homehub' in result.output


def test_backup_cli(tmp_path, monkeypatch, example_config_path):
    base = make_data(tmp_path / 'home')
    monkeypatch.setattr(backup, 'BASE_DIR', str(base))
    runner = make_app().test_cli_runner()
    out = tmp_path / 'cli.zip'
    result = runner.invoke(args=['backup', '--output', str(out), '--include-media'])
    assert result.exit_code == 0, result.output
    assert 'may contain the site password in plain text' in result.output
    with zipfile.ZipFile(out) as zf:
        assert {'media/video.mp4', 'config/config.yml'} <= set(zf.namelist())

    out2 = tmp_path / 'cli2.zip'
    result = runner.invoke(args=['backup', '--output', str(out2), '--no-config'])
    assert result.exit_code == 0, result.output
    with zipfile.ZipFile(out2) as zf:
        names = set(zf.namelist())
    assert 'config/config.yml' not in names and 'media/video.mp4' not in names
