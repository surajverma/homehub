import json
import logging
import sqlite3

import pytest
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


def test_trashed_reminders_move_to_the_shared_trash(tmp_path):
    path = str(tmp_path / 'app.db')
    make_old_db(path)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE undo_stash (id INTEGER PRIMARY KEY, token VARCHAR(32) NOT NULL UNIQUE, kind VARCHAR(32) NOT NULL, "
                 "payload TEXT NOT NULL, expires_at DATETIME NOT NULL)")
    conn.commit()
    conn.close()
    migrations.run(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO reminder (id, date, title, creator, deleted_at) VALUES (2, '2026-01-01', 'old', 'Mom', '2026-09-01 10:00:00.000000')")
    conn.execute("INSERT INTO reminder (id, date, start_date, title, creator, timestamp, deleted_at) "
                 "VALUES (3, '2026-01-02', '2026-01-02', 'recent', 'Mom', '2025-12-30 08:00:00.000000', '2026-10-01 10:00:00.000000')")
    # Text whose 11th character is a space must come through unchanged (only timestamps are rewritten)
    conn.execute("INSERT INTO reminder (id, date, start_date, title, creator, description, timestamp, deleted_at) "
                 "VALUES (4, '2026-01-03', '2026-01-03', 'Dentist at home', 'Abigail Jo Smith' || substr(hex(zeroblob(40)), 1, 70), 'Bring the x-ray', "
                 "'2025-12-30 09:00:00.000000', '2026-10-02 10:00:00.000000')")
    conn.commit()
    moved = migrations.move_reminder_trash(conn.cursor(), now=datetime(2026, 10, 4, 12, 0, 0))
    conn.commit()
    assert moved == 2
    # All leave the reminder table: the old one was past its 7 days, the recent ones are in the shared trash
    assert {r[0] for r in conn.execute("SELECT id FROM reminder")} == {1}
    rows = conn.execute("SELECT kind, module, label, owner, deleted_by, deleted_at, expires_at, payload FROM undo_stash "
                        "ORDER BY deleted_at").fetchall()
    assert len(rows) == 2
    spaced = json.loads(rows[1][7])['row']
    full = 'Abigail Jo Smith' + '0' * 70  # longer than 64 characters: kept whole
    assert (spaced['title'], spaced['creator'], rows[1][3]) == ('Dentist at home', full, full)
    assert spaced['timestamp'] == '2025-12-30T09:00:00.000000'
    rows = rows[:1]
    kind, module, label, owner, deleted_by, deleted_at, expires_at, payload = rows[0]
    assert (kind, module, label, owner, deleted_by) == ('reminder', 'reminders', 'recent (2026-01-02)', 'Mom', '')
    assert deleted_at.startswith('2026-10-01 10:00:00') and expires_at.startswith('2026-10-08 10:00:00')
    row = json.loads(payload)['row']
    assert (row['id'], row['title'], row['date'], row['deleted_at'], row['timestamp']) == (3, 'recent', '2026-01-02', None, '2025-12-30T08:00:00.000000')
    # Running again finds nothing more to move
    assert migrations.move_reminder_trash(conn.cursor(), now=datetime(2026, 10, 4, 12, 0, 0)) == 0
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


def test_api_tables_from_an_earlier_layout_get_missing_columns(tmp_path):
    """An api_token table left by an unreleased earlier version is completed, never dropped."""
    path = str(tmp_path / 'app.db')
    make_old_db(path)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE api_token (id INTEGER PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL, "
                 "permissions TEXT, created_at TIMESTAMP)")
    conn.execute("INSERT INTO api_token (id, name, token_hash, permissions) VALUES (1, 'Old', 'abc', '{}')")
    conn.execute("CREATE TABLE api_activity (id INTEGER PRIMARY KEY, token_id INTEGER, created_at TIMESTAMP)")
    conn.commit()
    conn.close()
    assert migrations.run(path)
    conn = sqlite3.connect(path)
    for table, column, _type, _default in migrations.LATE_COLUMNS:
        if table.startswith('api_'):
            assert column in columns(conn, table), (table, column)
    assert conn.execute("SELECT name, token_hash, prefix, act_as_members, allow_delete, revoked_at FROM api_token").fetchall() == [
        ('Old', 'abc', '', 0, 0, None)]
    # Recurring expenses get a uid each
    uids = [r[0] for r in conn.execute("SELECT uid FROM recurring_expense")]
    assert uids and all(len(u) == 32 for u in uids)
    # Tokens from before keep Allow deleting for reminders only
    assert conn.execute("SELECT delete_scope FROM api_token").fetchall() == [('reminders',)]
    conn.close()
    # The app can read the upgraded table
    engine = create_engine('sqlite:///' + path)
    with engine.connect() as c:
        assert c.execute(db.select(models.ApiToken.__table__)).fetchall()


def test_tables_with_a_trash_are_rebuilt_with_autoincrement(tmp_path):
    path = str(tmp_path / 'app.db')
    make_old_db(path)
    conn = sqlite3.connect(path)
    conn.execute("CREATE INDEX ix_shopping_item_item ON shopping_item (item)")
    # As SQLAlchemy made it: with a foreign key to its rule
    conn.execute("CREATE TABLE expense_entry (id INTEGER NOT NULL, date DATE NOT NULL, title VARCHAR(256) NOT NULL, "
                 "amount FLOAT NOT NULL, recurring_id INTEGER, PRIMARY KEY (id), "
                 "FOREIGN KEY(recurring_id) REFERENCES recurring_expense (id))")
    conn.execute("INSERT INTO expense_entry (id, date, title, amount, recurring_id) VALUES (3, '2026-01-01', 'Milk', 30, 1)")
    for i, item in ((1, 'Milk'), (2, 'Eggs'), (5, 'Rice')):
        conn.execute("INSERT INTO shopping_item (id, item, creator) VALUES (?, ?, 'Mom')", (i, item))
    conn.commit()
    conn.close()
    assert migrations.run(path) is True
    conn = sqlite3.connect(path)
    # A trashed item with a higher id than any row: new ids must start past it
    conn.execute("INSERT INTO undo_stash (token, kind, payload, expires_at, module, record_id) "
                 "VALUES ('t', 'shopping_item', '{\"row\": {\"id\": 9}}', '2999-01-01 00:00:00', 'shopping', 9)")
    conn.commit()
    conn.close()
    # Undo the rebuild of shopping_item to see the trash counted (as on a first upgrade with things in the trash)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE old_shopping (id INTEGER PRIMARY KEY, item TEXT, creator TEXT, timestamp TIMESTAMP, checked INTEGER, tags TEXT DEFAULT '[]');
        INSERT INTO old_shopping (id, item, creator) SELECT id, item, creator FROM shopping_item;
        DROP TABLE shopping_item;
        ALTER TABLE old_shopping RENAME TO shopping_item;
        CREATE INDEX ix_shopping_item_item ON shopping_item (item);
        DELETE FROM sqlite_sequence WHERE name = 'shopping_item';
    """)
    conn.close()
    assert migrations.run(path) is True
    conn = sqlite3.connect(path)
    # (Tables the old file never had are made by the app with AUTOINCREMENT already)
    present = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    rebuilt = [t for t in migrations.AUTOINCREMENT_TABLES if t in present]
    assert {'shopping_item', 'chore', 'reminder', 'recipe', 'expense_entry', 'recurring_expense'} <= set(rebuilt)
    for table in rebuilt:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
        assert 'AUTOINCREMENT' in sql.upper(), table
    assert conn.execute("SELECT id, item, creator, tags FROM shopping_item ORDER BY id").fetchall() == [
        (1, 'Milk', 'Mom', '[]'), (2, 'Eggs', 'Mom', '[]'), (5, 'Rice', 'Mom', '[]')]
    assert conn.execute("SELECT seq FROM sqlite_sequence WHERE name='shopping_item'").fetchone()[0] == 9
    assert conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='ix_shopping_item_item'").fetchone()
    conn.execute("INSERT INTO shopping_item (item, creator) VALUES ('Tea', 'Dad')")
    assert conn.execute("SELECT id FROM shopping_item WHERE item='Tea'").fetchone()[0] == 10
    # The foreign key from expense entries to their rule survives the rebuild
    fks = conn.execute("PRAGMA foreign_key_list(expense_entry)").fetchall()
    assert [(fk[2], fk[3], fk[4]) for fk in fks] == [('recurring_expense', 'recurring_id', 'id')]
    assert conn.execute("SELECT id, title, amount, recurring_id FROM expense_entry").fetchall() == [(3, 'Milk', 30.0, 1)]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
    # The trash keeps its unique token through the rebuild
    conn = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO undo_stash (token, kind, payload, expires_at) VALUES ('t', 'x', '{}', '2999-01-01')")
    conn.close()
    # Running again changes nothing
    assert migrations.run(path) is True


def test_upgrade_keeps_the_id_of_an_expired_trashed_reminder(tmp_path):
    """The newest reminder sat in the old trash for over 7 days, so the upgrade deletes it. Its id must
    still never be given to a new reminder: a client may retry a DELETE with it."""
    path = str(tmp_path / 'app.db')
    make_old_db(path)
    assert migrations.run(path) is True
    conn = sqlite3.connect(path)
    # Back to the old layout, as on a first upgrade
    conn.executescript("""
        CREATE TABLE old_reminder (id INTEGER PRIMARY KEY, date DATE, title TEXT, deleted_at TIMESTAMP);
        INSERT INTO old_reminder (id, date, title) SELECT id, date, title FROM reminder;
        DROP TABLE reminder;
        ALTER TABLE old_reminder RENAME TO reminder;
        DELETE FROM sqlite_sequence WHERE name = 'reminder';
        INSERT INTO reminder (id, date, title, deleted_at) VALUES (7, '2026-01-01', 'Old', '2000-01-01 00:00:00.000000');
    """)
    conn.close()
    assert migrations.run(path) is True
    conn = sqlite3.connect(path)
    assert [r[0] for r in conn.execute("SELECT id FROM reminder")] == [1]
    conn.execute("INSERT INTO reminder (date, title) VALUES ('2026-02-01', 'New')")
    assert conn.execute("SELECT id FROM reminder WHERE title='New'").fetchone()[0] == 8

