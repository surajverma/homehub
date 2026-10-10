import logging
import sqlite3
from datetime import datetime

from sqlalchemy import create_engine

from app import db
from app import migrations
from app import models  # noqa: F401 registers the model tables


OLD_SCHEMA = [
    "CREATE TABLE chore (id INTEGER PRIMARY KEY, description TEXT, creator TEXT, timestamp TIMESTAMP)",
    "CREATE TABLE shopping_item (id INTEGER PRIMARY KEY, item TEXT, creator TEXT, timestamp TIMESTAMP)",
    "CREATE TABLE media (id INTEGER PRIMARY KEY, title TEXT, filename TEXT)",
    "CREATE TABLE reminder (id INTEGER PRIMARY KEY, date DATE, title TEXT, description TEXT, creator TEXT, timestamp TIMESTAMP)",
    "CREATE TABLE recipe (id INTEGER PRIMARY KEY, title TEXT)",
    "CREATE TABLE qr_code (id INTEGER PRIMARY KEY, filename TEXT)",
    "CREATE TABLE file (id INTEGER PRIMARY KEY, filename TEXT, creator TEXT, upload_time TIMESTAMP)",
    "CREATE TABLE recurring_reminder (id INTEGER PRIMARY KEY, title TEXT NOT NULL, frequency TEXT)",
    "CREATE TABLE recurring_expense (id INTEGER PRIMARY KEY, title TEXT, frequency TEXT)",
]


def make_old_db(path):
    conn = sqlite3.connect(path)
    for sql in OLD_SCHEMA:
        conn.execute(sql)
    conn.execute("INSERT INTO reminder (id, date, title) VALUES (1, '2026-01-05', 'Bins')")
    conn.execute("INSERT INTO recurring_reminder (id, title, frequency) VALUES (1, 'Water plants', 'weekly')")
    conn.execute("INSERT INTO recurring_reminder (id, title, frequency) VALUES (2, 'Pay rent', 'monthly')")
    conn.execute("INSERT INTO recurring_reminder (id, title, frequency) VALUES (3, 'Odd', NULL)")
    conn.execute("INSERT INTO recurring_expense (id, title, frequency) VALUES (1, 'Milk', 'daily')")
    conn.commit()
    conn.close()


def columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_old_database_is_upgraded_in_place(tmp_path):
    path = str(tmp_path / 'app.db')
    make_old_db(path)

    assert migrations.run(path) is True

    conn = sqlite3.connect(path)
    assert {'done', 'due_date', 'recurring_id', 'tags'} <= columns(conn, 'chore')
    assert {'start_date', 'start_time', 'deleted_at', 'recurring_id', 'all_day'} <= columns(conn, 'reminder')
    assert 'tags' in columns(conn, 'recipe')
    assert 'original_input' in columns(conn, 'qr_code')
    assert 'stored_name' in columns(conn, 'file')
    for table in ('member_status', 'grocery_history', 'expense_entry', 'app_setting', 'recurring_chore'):
        assert columns(conn, table), table
    # Backfills
    assert conn.execute("SELECT start_date FROM reminder WHERE id=1").fetchone()[0] == '2026-01-05'
    assert conn.execute("SELECT monthly_mode FROM recurring_expense WHERE id=1").fetchone()[0] == 'day_of_month'
    units = dict(conn.execute("SELECT id, unit FROM recurring_reminder"))
    intervals = dict(conn.execute("SELECT id, interval FROM recurring_reminder"))
    assert intervals == {1: 1, 2: 1, 3: 1}
    # Legacy frequency carries over to the new unit column
    assert units == {1: 'week', 2: 'month', 3: 'month'}
    conn.close()


def test_running_twice_is_harmless(tmp_path):
    path = str(tmp_path / 'app.db')
    make_old_db(path)
    assert migrations.run(path) is True
    assert migrations.run(path) is True


def test_current_schema_needs_no_changes(tmp_path):
    path = str(tmp_path / 'app.db')
    engine = create_engine('sqlite:///' + path)
    db.metadata.create_all(engine)
    engine.dispose()
    assert migrations.run(path) is True


def test_purge_removes_only_old_trash(tmp_path):
    path = str(tmp_path / 'app.db')
    make_old_db(path)
    migrations.run(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO reminder (id, date, title, deleted_at) VALUES (2, '2026-01-01', 'old', '2026-09-01 10:00:00.000000')")
    conn.execute("INSERT INTO reminder (id, date, title, deleted_at) VALUES (3, '2026-01-01', 'recent', '2026-10-01 10:00:00.000000')")
    conn.commit()
    removed = migrations.purge_reminder_trash(conn.cursor(), now=datetime(2026, 10, 4, 12, 0, 0))
    conn.commit()
    assert removed == 1
    assert {r[0] for r in conn.execute("SELECT id FROM reminder")} == {1, 3}
    conn.close()


def test_failed_step_is_logged_and_others_still_run(tmp_path, caplog):
    path = str(tmp_path / 'app.db')
    make_old_db(path)
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE media")
    conn.commit()
    conn.close()

    with caplog.at_level(logging.ERROR, logger='app.migrations'):
        assert migrations.run(path) is False

    assert any('add media.status' in rec.getMessage() for rec in caplog.records)
    conn = sqlite3.connect(path)
    assert 'deleted_at' in columns(conn, 'reminder')
    assert columns(conn, 'app_setting')
    conn.close()


def test_existing_null_units_keep_their_legacy_schedule(tmp_path):
    path = str(tmp_path / 'app.db')
    make_old_db(path)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE recurring_reminder ADD COLUMN unit TEXT")
    conn.execute("ALTER TABLE recurring_reminder ADD COLUMN interval INTEGER")
    conn.execute("INSERT INTO recurring_reminder (id, title, frequency, unit) VALUES (4, 'Daily pill', 'daily', NULL)")
    conn.execute("INSERT INTO recurring_reminder (id, title, frequency, unit) VALUES (5, 'Kept', 'daily', 'week')")
    conn.commit()
    conn.close()

    assert migrations.run(path) is True

    conn = sqlite3.connect(path)
    units = dict(conn.execute("SELECT id, unit FROM recurring_reminder"))
    conn.close()
    # Null units follow the same fallback the scheduler used: daily/weekly by frequency, else monthly
    assert units == {1: 'week', 2: 'month', 3: 'month', 4: 'day', 5: 'week'}
