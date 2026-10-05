"""Startup upgrades for existing SQLite databases.

There is no Alembic. ``db.create_all()`` creates missing tables, and this
module adds columns and tables that older ``data/app.db`` files lack. Every
step is additive and safe to run on every start. A step that fails is logged
and skipped so the rest still run.
"""
import logging
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
    ('reminder', 'deleted_at', 'TIMESTAMP', None),
]

TABLES = [
    "CREATE TABLE IF NOT EXISTS member_status (id INTEGER PRIMARY KEY, name TEXT, text TEXT, updated_at TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS grocery_history (id INTEGER PRIMARY KEY, item TEXT, creator TEXT, timestamp TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS recurring_expense (id INTEGER PRIMARY KEY, title TEXT, unit_price REAL, default_quantity REAL, frequency TEXT, start_date DATE, end_date DATE, last_generated_date DATE, creator TEXT, timestamp TIMESTAMP)",
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
    ('qr_code', 'original_input', 'TEXT', None),
    ('reminder', 'recurring_id', 'INTEGER', None),
    ('recipe', 'tags', "TEXT DEFAULT '[]'", None),
    ('recurring_reminder', 'interval', 'INTEGER', 1),
    # Filled from the legacy frequency below, then 'month' for anything left (matches app/recurrence.py)
    ('recurring_reminder', 'unit', 'TEXT', None),
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


def purge_reminder_trash(cur, now: datetime | None = None) -> int:
    """Delete reminders that have sat in the trash longer than the retention period."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = (now - timedelta(days=TRASH_RETENTION_DAYS)).strftime('%Y-%m-%d %H:%M:%S')
    cur.execute("DELETE FROM reminder WHERE deleted_at IS NOT NULL AND deleted_at < ?", (cutoff,))
    return cur.rowcount


def _step(conn, description: str, fn, *args) -> bool:
    try:
        fn(*args)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        log.exception('Database upgrade step failed: %s', description)
        return False


def run(db_path: str) -> bool:
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
        ok &= _step(conn, 'purge old reminder trash', purge_reminder_trash, cur)
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
    finally:
        conn.close()
    if not ok:
        log.warning('Some database upgrade steps failed; see the errors above. The app will keep running.')
    return ok
