"""Backups of HomeHub's data, and restoring one.

A backup is a zip in data/backups/. It always holds data/app.db and data/secret_key
("database & settings") and can add uploads/, pdfs/, media/ and config.yml, plus a
manifest.json that describes it. The database is copied with SQLite's backup API, so the
copy is consistent even while the app is writing to it. Everything is written to files on
disk, never held in memory. The web page and ``flask backup`` / ``flask restore`` share this code.
"""
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone

import yaml

from . import config as config_module

log = logging.getLogger(__name__)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

MANIFEST_NAME = 'manifest.json'
FORMAT_VERSION = 1
DB_ENTRY = 'data/app.db'
SECRET_KEY_ENTRY = 'data/secret_key'
CONFIG_ENTRY = 'config/config.yml'
FOLDERS = ('uploads', 'pdfs', 'media')
# Every part a backup can hold; the database always goes in
PARTS = ('database',) + FOLDERS + ('config',)
DEFAULT_PARTS = ('database', 'uploads', 'pdfs', 'config')
CONFIG_FROM_BACKUP = 'config-from-backup.yml'
# Tables every HomeHub database has had; a zip without them is not a HomeHub backup
REQUIRED_TABLES = {'note', 'shopping_item', 'chore'}
SQLITE_HEADER = b'SQLite format 3\x00'
# "C:evil.txt" names a drive on Windows
_DRIVE_RE = re.compile(r'^[A-Za-z]:')
_BACKUP_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*\.zip$')
_PART_PREFIX = '.homehub-'
# A .part file nobody has written to for this long was left by a backup that died
STALE_PART_SECONDS = 600
_CHUNK = 1024 * 1024


class BackupError(Exception):
    """A backup that cannot be made or a zip that cannot be restored.

    ``code`` says which (the web page shows a translated message for it); the text is for logs and the CLI.
    """

    def __init__(self, message: str, code: str = 'invalid'):
        super().__init__(message)
        self.code = code


def paths(base_dir: str | None = None) -> dict:
    base = os.path.abspath(base_dir or BASE_DIR)
    data = os.path.join(base, 'data')
    return {
        'base': base,
        'data': data,
        'db': os.path.join(data, 'app.db'),
        'secret_key': os.path.join(data, 'secret_key'),
        'backups': os.path.join(data, 'backups'),
        'config_from_backup': os.path.join(data, CONFIG_FROM_BACKUP),
        'uploads': os.path.join(base, 'uploads'),
        'pdfs': os.path.join(base, 'pdfs'),
        'media': os.path.join(base, 'media'),
        # config.yml lives wherever the app reads it from (tests point that elsewhere)
        'config': os.path.join(base, 'config.yml') if base_dir else config_module.CONFIG_PATH,
    }


def default_output_path(base_dir: str | None = None, kind: str = 'backup') -> str:
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    prefix = 'homehub-safety-backup' if kind == 'safety' else 'homehub-backup'
    path = os.path.join(paths(base_dir)['backups'], f'{prefix}-{stamp}.zip')
    # Two backups in the same second (a safety backup right after a manual one) get a suffix
    n = 2
    while os.path.exists(path):
        path = os.path.join(paths(base_dir)['backups'], f'{prefix}-{stamp}-{n}.zip')
        n += 1
    return path


def app_version() -> str | None:
    # The Docker image sets this from the release tag (see Dockerfile)
    version = os.environ.get('SW_CACHE_VERSION') or os.environ.get('APP_VERSION')
    return version if version and version != 'dev' else None


def remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _folder_files(folder: str, skip: set):
    """(full path, path relative to the folder) for every file under it, in a stable order."""
    for root, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if os.path.abspath(os.path.join(root, d)) not in skip)
        for name in sorted(names):
            full = os.path.abspath(os.path.join(root, name))
            if full in skip or not os.path.isfile(full):
                continue
            yield full, os.path.relpath(full, folder).replace(os.sep, '/')


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path) if os.path.isfile(path) else 0
    except OSError:
        return 0


def part_sizes(base_dir: str | None = None) -> dict:
    """Bytes each part would add to a backup, before compression."""
    p = paths(base_dir)
    skip = {p['backups']}
    sizes = {
        'database': _file_size(p['db']) + _file_size(p['secret_key']),
        'config': _file_size(p['config']),
    }
    for folder in FOLDERS:
        sizes[folder] = sum(_file_size(full) for full, _rel in _folder_files(p[folder], skip))
    return sizes


def _normalise_parts(parts) -> list:
    chosen = set(parts or DEFAULT_PARTS) | {'database'}
    return [part for part in PARTS if part in chosen]


def _copy_database(src: str, dst: str) -> None:
    source = sqlite3.connect(src)
    try:
        target = sqlite3.connect(dst)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def create_backup(output_path: str | None = None, parts=None, base_dir: str | None = None,
                  kind: str = 'backup', progress=None) -> str:
    """Write a backup zip and return its path.

    ``parts`` picks what goes in (the database always does); ``progress(done, total)`` is called
    as bytes are written. Raises BackupError when there is no database or not enough disk space.
    """
    p = paths(base_dir)
    if not os.path.isfile(p['db']):
        raise BackupError(f'No database found at {p["db"]}', 'no_database')
    parts = _normalise_parts(parts)
    output_path = os.path.abspath(output_path or default_output_path(base_dir, kind))
    out_dir = os.path.dirname(output_path)
    os.makedirs(out_dir, exist_ok=True)

    sizes = part_sizes(base_dir)
    total = sum(sizes[part] for part in parts)
    # Room for the zip (at worst as big as its files) and the database copy
    needed = total + sizes['database']
    free = shutil.disk_usage(out_dir).free
    if free < needed:
        raise BackupError(f'Not enough free disk space: about {needed} bytes needed, {free} free', 'no_space')

    # Built next to the target and renamed at the end, so a failed backup never leaves half a zip
    fd, part_path = tempfile.mkstemp(prefix=_PART_PREFIX + 'backup-', suffix='.part', dir=out_dir)
    os.close(fd)
    fd, db_copy = tempfile.mkstemp(prefix=_PART_PREFIX + 'db-', suffix='.part', dir=out_dir)
    os.close(fd)
    # Earlier backups (and this one) are never backed up again
    skip = {p['backups'], output_path, part_path, db_copy}
    done = 0

    def add(zf, full, name):
        nonlocal done
        zf.write(full, name)
        done += _file_size(full)
        if progress:
            progress(done, total)

    try:
        _copy_database(p['db'], db_copy)
        files = []
        with zipfile.ZipFile(part_path, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            add(zf, db_copy, DB_ENTRY)
            files.append(DB_ENTRY)
            if os.path.isfile(p['secret_key']):
                add(zf, p['secret_key'], SECRET_KEY_ENTRY)
                files.append(SECRET_KEY_ENTRY)
            for folder in FOLDERS:
                if folder not in parts:
                    continue
                for full, rel in _folder_files(p[folder], skip):
                    name = f'{folder}/{rel}'
                    add(zf, full, name)
                    files.append(name)
            if 'config' in parts and os.path.isfile(p['config']):
                add(zf, p['config'], CONFIG_ENTRY)
                files.append(CONFIG_ENTRY)
            manifest = {
                'app': 'HomeHub',
                'format': FORMAT_VERSION,
                'kind': kind,
                'version': app_version(),
                'created_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'parts': parts,
                'files': files,
            }
            zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
        os.replace(part_path, output_path)
    except BaseException:
        remove_quietly(part_path)
        raise
    finally:
        remove_quietly(db_copy)
    return output_path


def _safe_entry_parts(name: str) -> list:
    """The parts of a zip entry name, or BackupError if it could land outside the restore folder."""
    if not name or name.startswith(('/', '\\')) or _DRIVE_RE.match(name):
        raise BackupError(f'Unsafe path in backup: {name}', 'unsafe')
    parts = [part for part in name.replace('\\', '/').split('/') if part not in ('', '.')]
    if not parts or '..' in parts:
        raise BackupError(f'Unsafe path in backup: {name}', 'unsafe')
    return parts


def _entry_part(parts: list) -> str | None:
    """Which backup part a zip entry belongs to, or None when it does not belong in a backup."""
    joined = '/'.join(parts)
    if joined in (DB_ENTRY, SECRET_KEY_ENTRY):
        return 'database'
    if joined == CONFIG_ENTRY:
        return 'config'
    if joined == MANIFEST_NAME:
        return 'manifest'
    if parts[0] in FOLDERS and len(parts) > 1:
        return parts[0]
    return None


def _check_database(path: str) -> None:
    with open(path, 'rb') as f:
        if f.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
            raise BackupError('data/app.db in the backup is not a SQLite database')
    try:
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise BackupError('data/app.db in the backup is damaged')
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise BackupError(f'data/app.db in the backup cannot be read: {exc}')
    missing = REQUIRED_TABLES - tables
    if missing:
        raise BackupError('data/app.db in the backup is missing tables: ' + ', '.join(sorted(missing)))


def _extract(zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest_root: str) -> str:
    target = os.path.join(dest_root, *_safe_entry_parts(info.filename))
    # Belt and braces: the joined path must still sit inside dest_root
    root = os.path.abspath(dest_root)
    if os.path.commonpath([os.path.abspath(target), root]) != root:
        raise BackupError(f'Unsafe path in backup: {info.filename}', 'unsafe')
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with zf.open(info) as src, open(target, 'wb') as dst:
        shutil.copyfileobj(src, dst, _CHUNK)
    return target


def _named(infos: list, name: str):
    return next((i for i in infos if '/'.join(_safe_entry_parts(i.filename)) == name), None)


def _read_zip(zf: zipfile.ZipFile) -> tuple[dict, dict]:
    """(manifest, {part: [ZipInfo]}) of an open backup zip; raises BackupError when it is not one."""
    by_part: dict[str, list] = {}
    for info in zf.infolist():
        if info.is_dir():
            continue
        part = _entry_part(_safe_entry_parts(info.filename))
        if part is None:
            raise BackupError(f'Unexpected file in backup: {info.filename}')
        by_part.setdefault(part, []).append(info)
    if 'manifest' not in by_part:
        raise BackupError('The backup has no manifest.json')
    try:
        manifest = json.loads(zf.read(by_part.pop('manifest')[0]).decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        raise BackupError('manifest.json in the backup cannot be read')
    if not isinstance(manifest, dict) or manifest.get('app') != 'HomeHub':
        raise BackupError('manifest.json does not describe a HomeHub backup')
    if _named(by_part.get('database', []), DB_ENTRY) is None:
        raise BackupError('The backup has no data/app.db')
    # The folders a backup was asked to hold, even when one of them was empty
    listed = manifest.get('parts') or manifest.get('folders') or []
    present = set(by_part) | {p for p in listed if p in PARTS}
    manifest['parts'] = [p for p in PARTS if p in present]
    return manifest, by_part


def describe_backup(zip_path: str) -> dict:
    """What a backup zip holds, for the list and the restore preview. Never raises: ``error`` is set instead."""
    info = {
        'name': os.path.basename(zip_path),
        'size': _file_size(zip_path),
        'modified': datetime.fromtimestamp(os.path.getmtime(zip_path), timezone.utc),
        'created_at': None,
        'kind': 'backup',
        'version': None,
        'parts': {},
        'error': None,
    }
    try:
        if not zipfile.is_zipfile(zip_path):
            raise BackupError(f'{zip_path} is not a zip file', 'not_zip')
        with zipfile.ZipFile(zip_path) as zf:
            manifest, by_part = _read_zip(zf)
        info['kind'] = manifest.get('kind') or 'backup'
        info['version'] = manifest.get('version')
        try:
            info['created_at'] = datetime.strptime(manifest.get('created_at') or '', '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        info['parts'] = {
            part: {'files': len(by_part.get(part, [])), 'bytes': sum(i.file_size for i in by_part.get(part, []))}
            for part in manifest['parts']
        }
    except (BackupError, zipfile.BadZipFile, OSError) as exc:
        info['error'] = getattr(exc, 'code', 'invalid')
    return info


def validate_backup(zip_path: str, staging_dir: str | None = None, parts=None, progress=None) -> dict:
    """Check a backup zip and return its manifest. Raises BackupError when it is not safe to restore.

    With ``staging_dir`` the chosen ``parts`` are also unpacked there, ready for restore_backup().
    """
    if not os.path.isfile(zip_path) or not zipfile.is_zipfile(zip_path):
        raise BackupError(f'{zip_path} is not a zip file', 'not_zip')
    with zipfile.ZipFile(zip_path) as zf:
        manifest, by_part = _read_zip(zf)
        wanted = set(manifest['parts']) if parts is None else set(parts) & set(manifest['parts'])
        own_staging = staging_dir is None
        root = staging_dir or tempfile.mkdtemp(prefix='homehub-restore-')
        try:
            # The database is always checked, even when it is not being restored
            _extract(zf, _named(by_part['database'], DB_ENTRY), root)
            _check_database(os.path.join(root, 'data', 'app.db'))
            if not own_staging:
                infos = [i for part in wanted for i in by_part.get(part, [])]
                total = sum(i.file_size for i in infos)
                done = 0
                for i in infos:
                    _extract(zf, i, root)
                    done += i.file_size
                    if progress:
                        progress(done, total)
        finally:
            if own_staging:
                shutil.rmtree(root, ignore_errors=True)
    manifest['has_secret_key'] = _named(by_part['database'], SECRET_KEY_ENTRY) is not None
    manifest['has_config'] = 'config' in by_part
    return manifest


def _replace_folder_contents(staged: str, target: str) -> None:
    # The folders are Docker bind mounts, so empty them in place rather than swapping the folder itself
    os.makedirs(target, exist_ok=True)
    for name in os.listdir(target):
        path = os.path.join(target, name)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    if os.path.isdir(staged):
        for name in os.listdir(staged):
            shutil.move(os.path.join(staged, name), os.path.join(target, name))


def restore_backup(zip_path: str, base_dir: str | None = None, parts=None, safety_backup: bool = True,
                   progress=None) -> dict:
    """Replace current data with a backup's. Returns {'manifest', 'safety_backup', 'restored', 'config_from_backup'}.

    ``parts`` picks what to restore (default: everything in the zip). The zip is checked and
    unpacked before anything is touched, and the parts about to be replaced are backed up first.
    The database is written through SQLite's backup API, so a running app never sees a half-copied
    file. config.yml is never overwritten (it is mounted read-only): the backup's copy is saved as
    data/config-from-backup.yml for the owner to look at.
    """
    p = paths(base_dir)
    os.makedirs(p['data'], exist_ok=True)
    staging = tempfile.mkdtemp(prefix=_PART_PREFIX + 'restore-', dir=p['data'])
    try:
        manifest = validate_backup(zip_path, staging_dir=staging, parts=parts, progress=progress)
        chosen = [part for part in manifest['parts'] if parts is None or part in parts]
        if not chosen:
            raise BackupError('Nothing selected to restore', 'nothing_selected')
        replaced = [part for part in chosen if part in FOLDERS]
        safety_path = None
        if safety_backup and os.path.isfile(p['db']):
            safety_path = create_backup(parts=['database'] + replaced, base_dir=base_dir, kind='safety')

        restored = []
        config_path = None
        if 'database' in chosen:
            _copy_database(os.path.join(staging, 'data', 'app.db'), p['db'])
            restored.append(DB_ENTRY)
            if manifest['has_secret_key']:
                shutil.copyfile(os.path.join(staging, 'data', 'secret_key'), p['secret_key'])
                restored.append(SECRET_KEY_ENTRY)
        for folder in replaced:
            _replace_folder_contents(os.path.join(staging, folder), p[folder])
            restored.append(folder + '/')
        if 'config' in chosen and manifest['has_config']:
            config_path = p['config_from_backup']
            shutil.copyfile(os.path.join(staging, 'config', 'config.yml'), config_path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {'manifest': manifest, 'safety_backup': safety_path, 'restored': restored, 'config_from_backup': config_path}


# --- Backups kept in data/backups/ (the web page lists these) ---

def backup_file(name: str, base_dir: str | None = None) -> str | None:
    """Full path of the backup called ``name`` in data/backups/, or None. Never reaches outside that folder."""
    if not name or not _BACKUP_NAME_RE.match(name):
        return None
    folder = os.path.realpath(paths(base_dir)['backups'])
    path = os.path.realpath(os.path.join(folder, name))
    if os.path.dirname(path) != folder or not os.path.isfile(path):
        return None
    return path


def list_backups(base_dir: str | None = None) -> list:
    """Backups in data/backups/, newest first."""
    folder = paths(base_dir)['backups']
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    found = [describe_backup(os.path.join(folder, n)) for n in names if backup_file(n, base_dir)]
    return sorted(found, key=lambda b: b['created_at'] or b['modified'], reverse=True)


def _latest_mtime(folder: str) -> float:
    # A restore still unpacking (say from the CLI) keeps writing somewhere inside its folder
    latest = os.path.getmtime(folder)
    for root, _dirs, names in os.walk(folder):
        for name in names:
            try:
                latest = max(latest, os.path.getmtime(os.path.join(root, name)))
            except OSError:
                pass
    return latest


def remove_stale_parts(base_dir: str | None = None, max_age: int = STALE_PART_SECONDS) -> int:
    """Delete half-written files left by a backup, upload or restore that was cut off (say by a restart)."""
    p = paths(base_dir)
    folder = p['backups']
    removed = 0
    now = time.time()
    try:
        for name in os.listdir(p['data']):
            path = os.path.join(p['data'], name)
            if name.startswith(_PART_PREFIX + 'restore-') and os.path.isdir(path) and now - _latest_mtime(path) > max_age:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
    except OSError:
        pass
    try:
        names = os.listdir(folder)
    except OSError:
        return removed
    for name in names:
        path = os.path.join(folder, name)
        if name.startswith(_PART_PREFIX) and name.endswith('.part'):
            try:
                if now - os.path.getmtime(path) > max_age:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
    return removed


def unique_upload_path(filename: str, base_dir: str | None = None) -> str:
    """Where an uploaded backup goes: data/backups/<safe name>.zip, never over an existing file."""
    stem = re.sub(r'[^A-Za-z0-9._-]+', '-', os.path.splitext(os.path.basename(filename or ''))[0]).strip('.-_')
    stem = stem or 'uploaded-backup'
    folder = paths(base_dir)['backups']
    path = os.path.join(folder, f'{stem}.zip')
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f'{stem}-{n}.zip')
        n += 1
    return path


def upload_part_path(base_dir: str | None = None) -> str:
    folder = paths(base_dir)['backups']
    os.makedirs(folder, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=_PART_PREFIX + 'upload-', suffix='.part', dir=folder)
    os.close(fd)
    return path


_SECRET_KEY_RE = re.compile(r'pass|secret|token|api_?key', re.IGNORECASE)


def masked_config(text: str) -> str | None:
    """config.yml with passwords and keys replaced by ********, or None if it cannot be read as YAML."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None

    def mask(value):
        if isinstance(value, dict):
            return {k: ('********' if _SECRET_KEY_RE.search(str(k)) and v not in (None, '') else mask(v))
                    for k, v in value.items()}
        if isinstance(value, list):
            return [mask(v) for v in value]
        return value

    return yaml.safe_dump(mask(data), sort_keys=False, allow_unicode=True)


# --- One backup or restore at a time, run in the background for the web page ---

class Jobs:
    """The one backup or restore job the web page can run, in a background thread.

    State lives in this process: HomeHub runs a single worker, and after a restart there is
    nothing running anyway (remove_stale_parts() tidies what a cut-off job left behind).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self.state = None
        # Set once data has been restored; until the app restarts it runs with the old secret key
        self.needs_restart = False

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict | None:
        with self._lock:
            return dict(self.state) if self.state else None

    def take_finished(self) -> dict | None:
        """The state of a job that has ended, once; later calls return None until the next job ends."""
        with self._lock:
            if not self.state or self.state['status'] == 'running' or self.state.get('reported'):
                return None
            self.state['reported'] = True
            return dict(self.state)

    def start(self, kind: str, fn) -> None:
        """Run ``fn(progress)`` in the background; raises BackupError('busy') if a job is already running."""
        with self._lock:
            if self.running():
                raise BackupError('A backup or restore is already running', 'busy')
            self.state = {'kind': kind, 'status': 'running', 'done': 0, 'total': 0, 'error': None, 'result': None,
                          'started_at': time.time()}
            self._thread = threading.Thread(target=self._run, args=(fn,), name=f'homehub-{kind}', daemon=True)
            self._thread.start()

    def _progress(self, done, total):
        with self._lock:
            self.state['done'], self.state['total'] = done, total

    def _run(self, fn):
        try:
            result = fn(self._progress)
            with self._lock:
                self.state.update(status='done', result=result)
        except BackupError as exc:
            log.warning('Background %s failed: %s', self.state['kind'], exc)
            with self._lock:
                self.state.update(status='failed', error=exc.code)
        except Exception:
            log.exception('Background %s failed', self.state['kind'])
            with self._lock:
                self.state.update(status='failed', error='unexpected')

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)


jobs = Jobs()
