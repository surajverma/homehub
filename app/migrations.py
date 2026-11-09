"""Startup upgrades for existing SQLite databases.

There is no Alembic. ``db.create_all()`` creates missing tables, and this
module adds columns and tables that older ``data/app.db`` files lack. Every
step is additive and safe to run on every start. A step that fails is logged
and skipped so the rest still run.
"""
import json
import logging
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

TRASH_RETENTION_DAYS = 7

# (table, column, type, value to backfill into existing rows or None)
COLUMNS = [
    ('chore', 'done', 'INTEGER DEFAULT 0', None),
    ('chore', 'due_date', 'DATE', None),
    ('chore', 'recurring_id', 'INTEGER', None),
    ('shopping_item', 'tags', "TEXT DEFAULT '[]'", None),
    ('chore', 'tags', "TEXT DEFAULT '[]'", None),
    ('media', 'status', "TEXT DEFAULT 'done'", None),
    ('media', 'progress', 'TEXT', None),
    ('reminder', 'category', 'TEXT', None),
    ('reminder', 'color', 'TEXT', None),
    ('reminder', 'updated_at', 'TIMESTAMP', None),
    ('reminder', 'time', 'TEXT', None),
    ('reminder', 'start_date', 'DATE', None),
    ('reminder', 'start_time', 'TEXT', None),
    ('reminder', 'end_date', 'DATE', None),
    ('reminder', 'end_time', 'TEXT', None),
    ('reminder', 'all_day', 'INTEGER DEFAULT 0', None),
    ('reminder', 'completed_at', 'TIMESTAMP', None),
    # No longer used: trashed reminders moved to the shared trash (move_reminder_trash). The column
    # stays because dropping one is a table rebuild in SQLite.
    ('reminder', 'deleted_at', 'TIMESTAMP', None),
    ('file', 'stored_name', 'TEXT', None),
]

TABLES = [
    "CREATE TABLE IF NOT EXISTS member_status (id INTEGER PRIMARY KEY, name TEXT, text TEXT, updated_at TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS grocery_history (id INTEGER PRIMARY KEY, item TEXT, creator TEXT, timestamp TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS recurring_expense (id INTEGER PRIMARY KEY, title TEXT, unit_price REAL, default_quantity REAL, frequency TEXT, start_date DATE, end_date DATE, last_generated_date DATE, creator TEXT, timestamp TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS expense_budget (id INTEGER PRIMARY KEY, category TEXT, member TEXT, amount REAL NOT NULL, updated_by TEXT, updated_at TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS expense_entry (id INTEGER PRIMARY KEY, date DATE, title TEXT, category TEXT, unit_price REAL, quantity REAL, amount REAL, payer TEXT, recurring_id INTEGER, timestamp TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY, value TEXT)",
    """CREATE TABLE IF NOT EXISTS recurring_reminder (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT,
        creator TEXT,
        frequency TEXT,
        monthly_mode TEXT,
        interval INTEGER,
        unit TEXT,
        time TEXT,
        category TEXT,
        color TEXT,
        start_date DATE,
        end_date DATE,
        last_generated_date DATE,
        effective_from DATE,
        timestamp TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS recurring_chore (
        id INTEGER PRIMARY KEY,
        description TEXT NOT NULL,
        creator TEXT,
        tags TEXT,
        interval INTEGER,
        unit TEXT,
        start_date DATE,
        end_date DATE,
        last_generated_date DATE,
        timestamp TIMESTAMP
    )""",
    # Undo and the shared trash (app/undo.py); normally made by db.create_all(), but `flask restore` runs these alone
    """CREATE TABLE IF NOT EXISTS undo_stash (
        id INTEGER PRIMARY KEY,
        token VARCHAR(32) NOT NULL UNIQUE,
        kind VARCHAR(32) NOT NULL,
        payload TEXT NOT NULL,
        expires_at DATETIME NOT NULL
    )""",
    # The JSON API (app/api). Older versions ignore these tables.
    """CREATE TABLE IF NOT EXISTS api_token (
        id INTEGER PRIMARY KEY,
        name VARCHAR(40) COLLATE NOCASE NOT NULL UNIQUE,
        token_hash VARCHAR(64) NOT NULL UNIQUE,
        prefix VARCHAR(16) NOT NULL,
        permissions TEXT NOT NULL DEFAULT '{}',
        act_as_members BOOLEAN NOT NULL DEFAULT 0,
        allow_delete BOOLEAN NOT NULL DEFAULT 0,
        created_at TIMESTAMP,
        last_used_at TIMESTAMP,
        revoked_at TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS api_activity (
        id INTEGER PRIMARY KEY,
        token_id INTEGER,
        token_name VARCHAR(40),
        acting_as VARCHAR(64),
        action VARCHAR(64),
        target VARCHAR(200),
        created_at TIMESTAMP
    )""",
]

# Columns on tables that the statements above may have just created
LATE_COLUMNS = [
    ('recurring_expense', 'monthly_mode', 'TEXT', 'day_of_month'),
    ('recurring_expense', 'category', 'TEXT', None),
    ('recurring_expense', 'effective_from', 'DATE', None),
    ('recurring_expense', 'split_with', 'TEXT', None),
    ('expense_entry', 'skipped', 'INTEGER DEFAULT 0', 0),
    ('expense_entry', 'split_with', 'TEXT', None),
    ('expense_entry', 'is_settlement', 'INTEGER DEFAULT 0', 0),
    ('expense_entry', 'created_by', 'TEXT', None),
    ('qr_code', 'original_input', 'TEXT', None),
    ('reminder', 'recurring_id', 'INTEGER', None),
    ('recipe', 'tags', "TEXT DEFAULT '[]'", None),
    ('recurring_reminder', 'interval', 'INTEGER', 1),
    # Filled from the legacy frequency below, then 'month' for anything left (matches app/recurrence.py)
    ('recurring_reminder', 'unit', 'TEXT', None),
    ('recurring_reminder', 'anchor_day', 'INTEGER', None),
    # API tables (app/api): a table left by an unreleased earlier layout gets the columns it lacks
    ('api_token', 'name', 'VARCHAR(40) COLLATE NOCASE', None),
    ('api_token', 'token_hash', 'VARCHAR(64)', None),
    ('api_token', 'prefix', "VARCHAR(16) NOT NULL DEFAULT ''", None),
    ('api_token', 'permissions', "TEXT NOT NULL DEFAULT '{}'", None),
    ('api_token', 'act_as_members', 'BOOLEAN NOT NULL DEFAULT 0', None),
    ('api_token', 'allow_delete', 'BOOLEAN NOT NULL DEFAULT 0', None),
    ('api_token', 'created_at', 'TIMESTAMP', None),
    ('api_token', 'last_used_at', 'TIMESTAMP', None),
    ('api_token', 'revoked_at', 'TIMESTAMP', None),
    ('api_activity', 'token_id', 'INTEGER', None),
    ('api_activity', 'token_name', 'VARCHAR(40)', None),
    ('api_activity', 'acting_as', 'VARCHAR(64)', None),
    ('api_activity', 'action', 'VARCHAR(64)', None),
    ('api_activity', 'target', 'VARCHAR(200)', None),
    ('api_activity', 'created_at', 'TIMESTAMP', None),
    # The shared trash keeps deleted records in the undo store (app/undo.py)
    ('undo_stash', 'module', 'VARCHAR(32)', None),
    ('undo_stash', 'label', 'VARCHAR(256)', None),
    ('undo_stash', 'owner', 'VARCHAR(64)', None),
    ('undo_stash', 'deleted_by', 'VARCHAR(128)', None),
    ('undo_stash', 'deleted_at', 'TIMESTAMP', None),
]


def has_column(cur, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def ensure_column(cur, table: str, column: str, type_spec: str, default=None) -> None:
    """Add ``column`` to ``table`` if it is missing, then backfill ``default`` into existing rows."""
    if has_column(cur, table, column):
        return
    cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_spec}")
    if default is not None:
        cur.execute(f"UPDATE {table} SET {column}=? WHERE {column} IS NULL", (default,))


def _stored_time(value):
    """A date or time as SQLite holds it, as the shared trash's copies hold it (ISO, "T" between date and time)."""
    if isinstance(value, str) and len(value) > 10 and value[10] == ' ':
        return value[:10] + 'T' + value[11:]
    return value


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace(' ', 'T', 1))


def move_reminder_trash(cur, now: datetime | None = None) -> int:
    """Move reminders in the calendar's old trash (deleted_at set) into the shared trash, then off the calendar.

    Each keeps the time it was deleted and leaves the trash 7 days after it, as before. Ones already older
    than that are deleted, as the old trash would have done. Nobody recorded who deleted them.
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    cur.execute("PRAGMA table_info(reminder)")
    names = [row[1] for row in cur.fetchall()]
    if 'deleted_at' not in names:
        return 0
    cur.execute(f"SELECT {', '.join(names)} FROM reminder WHERE deleted_at IS NOT NULL")
    moved = 0
    for values in cur.fetchall():
        row = dict(zip(names, values))
        try:
            deleted_at = _parse_time(row['deleted_at'])
        except ValueError:
            deleted_at = now
        expires_at = deleted_at + timedelta(days=TRASH_RETENTION_DAYS)
        if expires_at > now:
            data = {name: _stored_time(value) for name, value in row.items()}
            data['deleted_at'] = None
            for flag in ('all_day',):
                if flag in data and data[flag] is not None:
                    data[flag] = bool(data[flag])
            day = str(row.get('start_date') or row.get('date') or '')[:10]
            title = str(row.get('title') or '')
            label = f'{title} ({day})' if day else title
            cur.execute(
                "INSERT INTO undo_stash (token, kind, payload, expires_at, module, label, owner, deleted_by, deleted_at) "
                "VALUES (?, 'reminder', ?, ?, 'reminders', ?, ?, '', ?)",
                (secrets.token_hex(16), json.dumps({'row': data}), expires_at.strftime('%Y-%m-%d %H:%M:%S.%f'),
                 label[:256], str(row.get('creator') or '')[:64], deleted_at.strftime('%Y-%m-%d %H:%M:%S.%f')))
            moved += 1
        cur.execute("DELETE FROM reminder WHERE id=?", (row['id'],))
    return moved


def purge_expired_stashes(cur, now: datetime | None = None) -> int:
    """Delete undo stashes and trash entries whose time is up."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    cur.execute("DELETE FROM undo_stash WHERE expires_at < ?", (now.strftime('%Y-%m-%d %H:%M:%S.%f'),))
    return cur.rowcount


def fail_interrupted_downloads(cur) -> int:
    """Downloads run in a thread of the app, so one still pending at startup was cut off by a restart."""
    cur.execute("UPDATE media SET status='error', progress=NULL WHERE status='pending'")
    return cur.rowcount


def remove_leftover_pdf_originals(cur, pdf_dir: str) -> list:
    """Delete the uncompressed PDFs that older versions kept next to each compressed one.

    A file goes only when a row names it as its upload, that row's compressed file is on
    disk, and no row serves that name as its compressed file. Nothing matches on a later
    start, so this is safe to run every time.
    """
    cur.execute("SELECT filename, compressed_path FROM pdf")
    rows = cur.fetchall()
    served = {compressed for _filename, compressed in rows if compressed}
    removed = []
    for filename, compressed in rows:
        name = os.path.basename(filename or '')
        if not name or name != filename or name in served or name in removed:
            continue
        if not compressed or not os.path.isfile(os.path.join(pdf_dir, compressed)):
            continue
        path = os.path.join(pdf_dir, name)
        if os.path.isfile(path):
            os.remove(path)
            removed.append(name)
    if removed:
        log.warning('Removed %d uncompressed PDF original(s) left by an older version: %s', len(removed), ', '.join(removed))
    return removed


def _step(conn, description: str, fn, *args) -> bool:
    try:
        fn(*args)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        log.exception('Database upgrade step failed: %s', description)
        return False


def run(db_path: str, pdf_dir: str | None = None) -> bool:
    """Bring the SQLite database at ``db_path`` up to date. Returns True if every step succeeded."""
    try:
        conn = sqlite3.connect(db_path)
    except Exception:
        log.exception('Could not open %s to upgrade it', db_path)
        return False
    ok = True
    try:
        cur = conn.cursor()
        for table, column, type_spec, default in COLUMNS:
            ok &= _step(conn, f'add {table}.{column}', ensure_column, cur, table, column, type_spec, default)
        ok &= _step(conn, 'mark interrupted downloads as failed', fail_interrupted_downloads, cur)
        ok &= _step(conn, 'backfill reminder start_date', cur.execute,
                    "UPDATE reminder SET start_date=date WHERE start_date IS NULL AND date IS NOT NULL")
        ok &= _step(conn, 'backfill reminder start_time', cur.execute,
                    "UPDATE reminder SET start_time=time WHERE start_time IS NULL AND time IS NOT NULL")
        for sql in TABLES:
            name = sql.split('EXISTS', 1)[1].split('(', 1)[0].strip()
            ok &= _step(conn, f'create table {name}', cur.execute, sql)
        for table, column, type_spec, default in LATE_COLUMNS:
            ok &= _step(conn, f'add {table}.{column}', ensure_column, cur, table, column, type_spec, default)
        for frequency, unit in (('daily', 'day'), ('weekly', 'week'), ('monthly', 'month')):
            ok &= _step(conn, f'backfill recurring_reminder unit for {frequency}', cur.execute,
                        "UPDATE recurring_reminder SET unit=? WHERE (unit IS NULL OR unit='') AND frequency=?",
                        (unit, frequency))
        # Anything else is scheduled monthly by the app's legacy fallback, so store that
        ok &= _step(conn, 'default recurring_reminder unit', cur.execute,
                    "UPDATE recurring_reminder SET unit='month' WHERE unit IS NULL OR unit=''")
        ok &= _step(conn, 'move trashed reminders to the shared trash', move_reminder_trash, cur)
        ok &= _step(conn, 'purge expired trash', purge_expired_stashes, cur)
        if pdf_dir and os.path.isdir(pdf_dir):
            ok &= _step(conn, 'remove leftover PDF originals', remove_leftover_pdf_originals, cur, pdf_dir)
    finally:
        conn.close()
    if not ok:
        log.warning('Some database upgrade steps failed; see the errors above. The app will keep running.')
    return ok
