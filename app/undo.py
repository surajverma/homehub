"""Undo for changes that rewrite or remove rows, and the shared trash.

A change stashes the rows it is about to replace as JSON and offers the token to the
browser, which shows an Undo toast for UNDO_SECONDS. A delete stashes the same way but
as a trash entry (module, label, owner, who deleted it), kept for TRASH_DAYS and listed
on the Trash page. Expired stashes are deleted the next time one is made or used, and at startup.
"""
import html
import json
import secrets
from datetime import date, datetime, timedelta

import bleach
from flask import current_app, flash, jsonify
from sqlalchemy import event, func, select
from flask_babel import gettext as _

from . import db
from .blueprints import main_bp
from .clock import utcnow
from .models import UndoStash

UNDO_SECONDS = 20
# Deleted records stay in the trash this long (the Trash page, app/trash.py)
TRASH_DAYS = 7
# The toast counts down in the browser, so the server waits a little longer than it shows
_GRACE_SECONDS = 5

# kind -> function(payload) that puts the stashed rows back (it must not commit)
_restorers = {}


def restorer(kind):
    def register(fn):
        _restorers[kind] = fn
        return fn
    return register


def row_to_dict(row) -> dict:
    """A model row as JSON-ready values."""
    data = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        data[column.name] = value.isoformat() if isinstance(value, (date, datetime)) else value
    return data


def row_values(model, data: dict) -> dict:
    """Turn row_to_dict() output back into column values for ``model``."""
    values = {}
    for column in model.__table__.columns:
        if column.name not in data:
            continue
        value = data[column.name]
        if value is not None and isinstance(column.type, db.DateTime):
            value = datetime.fromisoformat(value)
        elif value is not None and isinstance(column.type, db.Date):
            value = date.fromisoformat(value)
        values[column.name] = value
    return values


def stamp(row):
    """A row's creation time as stored in a stash; with its id, this says which record it is."""
    return row_to_dict(row).get('timestamp') if row is not None else None


def same_record(row, data: dict) -> bool:
    """True when ``row`` is the record ``data`` was stashed from.

    SQLite gives a new row the highest id that is free, so after a delete the same id can
    belong to something else entirely. The creation time tells the two apart.
    """
    return row is not None and stamp(row) == data.get('timestamp')


def restore_row(model, data: dict, **overrides):
    """Put a stashed row back and return it.

    The record itself, if it is still there, gets its old values. If it is gone it is added
    again, under its old id when that is free and under a new one when another record has
    taken it. ``overrides`` replace stashed values, e.g. to point a child at its parent's new id.
    """
    values = row_values(model, data)
    values.update(overrides)
    existing = db.session.get(model, values['id'])
    if same_record(existing, data):
        for name, value in values.items():
            setattr(existing, name, value)
        return existing
    if existing is not None:
        values.pop('id')
    row = model(**values)
    db.session.add(row)
    db.session.flush()
    return row


def purge_expired(now: datetime | None = None) -> None:
    """Delete stashes whose time is up: changes after a few seconds, trash entries after TRASH_DAYS."""
    UndoStash.query.filter(UndoStash.expires_at < (now or utcnow())).delete(synchronize_session=False)


def trash_label(text, limit: int = 80) -> str:
    """A short plain-text name for a trash entry: tags removed, spaces collapsed, cut at ``limit``."""
    plain = html.unescape(bleach.clean(str(text or ''), tags=set(), strip=True))
    plain = ' '.join(plain.split())
    return plain if len(plain) <= limit else plain[:limit - 1].rstrip() + '\u2026'


def trash_info(module: str, label, owner, deleted_by) -> dict:
    """What the Trash page shows for a deleted record and who may restore it (same rule as the module: can_modify)."""
    return {'module': module, 'label': trash_label(label), 'owner': owner or '', 'deleted_by': deleted_by or ''}


def stash_undo(kind: str, payload: dict, trash: dict | None = None) -> str:
    """Stash ``payload`` and return its token, for JSON endpoints whose page shows the Undo toast itself. Commits.

    With ``trash`` (see trash_info) the stash is a trash entry, kept for TRASH_DAYS.
    """
    purge_expired()
    token = secrets.token_hex(16)
    now = utcnow()
    stash = UndoStash(token=token, kind=kind, payload=json.dumps(payload))
    if trash:
        stash.expires_at = now + timedelta(days=TRASH_DAYS)
        stash.module = trash['module']
        stash.label = (trash.get('label') or '')[:256]
        stash.owner = (trash.get('owner') or '')[:64]
        stash.deleted_by = (trash.get('deleted_by') or '')[:128]
        stash.deleted_at = now
        record = payload.get('row') or payload.get('rule') or {}
        stash.record_id = record.get('id') if isinstance(record, dict) else None
    else:
        stash.expires_at = now + timedelta(seconds=UNDO_SECONDS + _GRACE_SECONDS)
    db.session.add(stash)
    db.session.commit()
    return token


def offer_undo(kind: str, payload: dict, message: str, category: str = 'success', trash: dict | None = None) -> str:
    """Stash ``payload`` and flash ``message`` with an Undo button. Commits."""
    token = stash_undo(kind, payload, trash)
    flash(json.dumps({'token': token, 'message': message, 'type': category, 'seconds': UNDO_SECONDS}), 'undo')
    return token


def offer_batch_undo(stash_tokens: list, message: str, category: str = 'success') -> str | None:
    """One Undo for several records moved to the trash together (a bulk delete). Commits."""
    if not stash_tokens:
        flash(message, category)
        return None
    if len(stash_tokens) == 1:
        flash(json.dumps({'token': stash_tokens[0], 'message': message, 'type': category, 'seconds': UNDO_SECONDS}), 'undo')
        return stash_tokens[0]
    return offer_undo('trash_batch', {'tokens': list(stash_tokens)}, message, category)


def trash_row(kind: str, row, trash: dict, message: str | None = None, extra: dict | None = None) -> str:
    """Delete ``row`` and keep a copy in the trash as {'row': ..., **extra}. Commits.

    With ``message`` it is flashed with an Undo button (web pages); without, only the token is returned.
    """
    payload = {'row': row_to_dict(row), **(extra or {})}
    db.session.delete(row)
    if message is not None:
        return offer_undo(kind, payload, message, trash=trash)
    return stash_undo(kind, payload, trash)


def row_restorer(kind: str, model):
    """Register the restorer for a trash entry made by trash_row from a record that stands alone."""
    @restorer(kind)
    def restore(payload: dict) -> None:
        restore_row(model, payload['row'])
    return restore


class RestoreConflict(Exception):
    """A restore that cannot be done; the message tells the person why (and what to do instead)."""


def restore_stash(stash: UndoStash) -> str | None:
    """Put back what ``stash`` holds and delete it. Returns a note for the person, if the restorer has one.

    Raises RestoreConflict when it cannot be done. Does not commit.
    """
    restore = _restorers.get(stash.kind)
    if restore is None:
        raise RestoreConflict(_('It is too late to undo that.'))
    note = restore(json.loads(stash.payload))
    db.session.delete(stash)
    return note if isinstance(note, str) else None


@restorer('trash_batch')
def _restore_batch(payload: dict):
    notes = []
    for token in payload.get('tokens') or []:
        stash = UndoStash.query.filter_by(token=token).first()
        # Already restored or deleted for good from the Trash page
        if stash is None or stash.expires_at < utcnow():
            continue
        note = restore_stash(stash)
        if note:
            notes.append(note)
    return ' '.join(notes) or None


@main_bp.route('/undo/<token>', methods=['POST'])
def undo_change(token):
    # Holding the token is the permission: it only ever reaches the browser that made the change
    stash = UndoStash.query.filter_by(token=token).first()
    if stash is None or stash.expires_at < utcnow() or stash.kind not in _restorers:
        purge_expired()
        db.session.commit()
        return jsonify({'ok': False, 'error': _('It is too late to undo that.')}), 410
    kind = stash.kind
    try:
        note = restore_stash(stash)
        purge_expired()
        db.session.commit()
    except RestoreConflict as exc:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(exc)}), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Undo failed for %s', kind)
        return jsonify({'ok': False, 'error': _('Could not undo that change.')}), 500
    flash(_('Change undone.') + (' ' + note if note else ''), 'success')
    return jsonify({'ok': True})


# --- Ids of records in the trash stay theirs ---
#
# SQLite gives a new row the highest id plus one, so deleting the newest row frees its id for the
# next one. With the record in the trash that id must stay its own: a client retrying a delete, or
# a restore, must never meet a different record under it. New rows of these tables are numbered
# past every id held in the trash. (After 7 days the entry is gone and the id is free again.)

def _reserve_trashed_ids(model, *kinds):
    table = model.__table__

    @event.listens_for(model, 'before_insert')
    def skip_trashed_ids(_mapper, connection, target):
        if target.id is not None:
            return
        held = connection.execute(
            select(func.max(UndoStash.record_id))
            .where(UndoStash.kind.in_(kinds), UndoStash.module.isnot(None))
        ).scalar()
        if held is None:
            return
        highest = connection.execute(select(func.max(table.c.id))).scalar() or 0
        # Rows added in the same flush are not in the table yet; count the ids given to them here
        given = connection.info.setdefault('homehub_reserved_ids', {})
        last = given.get(table.name, 0)
        if held < highest and last <= highest:
            return
        target.id = max(held, highest, last) + 1
        given[table.name] = target.id


def _reserve_all():
    from .models import (Chore, ExpenseEntry, ExpiryItem, Note, Recipe, RecurringChore, RecurringExpense,
                         RecurringReminder, Reminder, ShoppingItem)
    for model, kind in ((ShoppingItem, 'shopping_item'), (Note, 'note'), (Recipe, 'recipe'),
                        (ExpiryItem, 'expiry_item'), (Chore, 'chore'), (RecurringChore, 'chore_rule'),
                        (Reminder, 'reminder'), (RecurringReminder, 'reminder_rule'),
                        (ExpenseEntry, 'expense_entry'), (RecurringExpense, 'expense_rule')):
        _reserve_trashed_ids(model, kind)


_reserve_all()
