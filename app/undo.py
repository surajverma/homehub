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

from contextvars import ContextVar

import bleach
from flask import current_app, flash, jsonify
from flask_babel import gettext as _

from . import db
from .admin import can_modify
from .blueprints import main_bp
from .clock import utcnow
from .models import UndoStash
from .recurrence import generation_lock

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


def stash_undo(kind: str, payload: dict, trash: dict | None = None, commit: bool = True) -> str:
    """Stash ``payload`` and return its token, for JSON endpoints whose page shows the Undo toast itself.

    With ``trash`` (see trash_info) the stash is a trash entry, kept for TRASH_DAYS. Commits, unless
    ``commit`` is False: then it joins the caller's transaction (a bulk delete commits once, at the end).
    """
    purge_expired()
    token = secrets.token_hex(16)
    now = utcnow()
    stash = UndoStash(token=token, kind=kind, payload=json.dumps(payload))
    if trash:
        stash.expires_at = now + timedelta(days=TRASH_DAYS)
        stash.module = trash['module']
        stash.label = (trash.get('label') or '')[:256]
        stash.owner = trash.get('owner') or ''
        stash.deleted_by = trash.get('deleted_by') or ''
        stash.deleted_at = now
        record = payload.get('row') or payload.get('rule') or {}
        stash.record_id = record.get('id') if isinstance(record, dict) else None
    else:
        stash.expires_at = now + timedelta(seconds=UNDO_SECONDS + _GRACE_SECONDS)
    db.session.add(stash)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return token


def offer_undo(kind: str, payload: dict, message: str, category: str = 'success', trash: dict | None = None) -> str:
    """Stash ``payload`` and flash ``message`` with an Undo button. Commits."""
    token = stash_undo(kind, payload, trash)
    flash(json.dumps({'token': token, 'message': message, 'type': category, 'seconds': UNDO_SECONDS}), 'undo')
    return token


def offer_batch_undo(stash_tokens: list, message: str, category: str = 'success') -> str | None:
    """One Undo for several records moved to the trash together (a bulk delete). Commits the deletes
    and their Undo together."""
    if not stash_tokens:
        db.session.commit()
        flash(message, category)
        return None
    if len(stash_tokens) == 1:
        db.session.commit()
        flash(json.dumps({'token': stash_tokens[0], 'message': message, 'type': category, 'seconds': UNDO_SECONDS}), 'undo')
        return stash_tokens[0]
    return offer_undo('trash_batch', {'tokens': list(stash_tokens)}, message, category)


SQLITE_MAX_ID = 2 ** 63 - 1


def valid_ids(values) -> list:
    """The ids among ``values`` (numbers, or text of digits) that can be a row id, in order, without repeats.

    Anything else, such as a number too big for SQLite, is left out before a bulk change starts, so it
    can never stop one half way.
    """
    ids = []
    for value in values or []:
        if isinstance(value, bool):
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value.isdigit():
                continue
            value = int(value)
        if isinstance(value, int) and 0 < value <= SQLITE_MAX_ID and value not in ids:
            ids.append(value)
    return ids


def delete_exactly(row) -> bool:
    """Delete ``row`` in one statement; False when another request deleted it first (nothing matched)."""
    model = type(row)
    taken = db.session.query(model).filter(model.id == row.id).delete(synchronize_session=False)
    if row in db.session:
        db.session.expunge(row)
    return taken == 1


def trash_row(kind: str, row, trash: dict, message: str | None = None, extra: dict | None = None,
              commit: bool = True) -> str | None:
    """Delete ``row`` and keep a copy in the trash as {'row': ..., **extra}. Commits unless ``commit`` is False.

    With ``message`` it is flashed with an Undo button (web pages); without, only the token is returned.
    The copy is kept only when this delete removed the row: if another request got there first, there is
    nothing to keep and None is returned (two copies of one record would let it be restored twice).
    """
    payload = {'row': row_to_dict(row), **(extra or {})}
    if not delete_exactly(row):
        if commit:
            db.session.rollback()
            if message is not None:
                flash(_('That was already deleted.'), 'info')
        return None
    if message is not None:
        return offer_undo(kind, payload, message, trash=trash)
    return stash_undo(kind, payload, trash, commit=commit)


def row_restorer(kind: str, model):
    """Register the restorer for a trash entry made by trash_row from a record that stands alone."""
    @restorer(kind)
    def restore(payload: dict) -> None:
        restore_row(model, payload['row'])
    return restore


class RestoreConflict(Exception):
    """A restore that cannot be done. user_message tells the person why (and what to do instead);
    it is the only part of the exception shown to them (as FormError in expenses)."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


def claim_stash(stash: UndoStash) -> bool:
    """Remove exactly this stash in one statement (its id and its own token, which is never reused).

    False when it is no longer there: restored, deleted or replaced by another request since it was read.
    Does not commit; the caller's transaction keeps the claim and what follows together.
    """
    taken = (UndoStash.query.filter(UndoStash.id == stash.id, UndoStash.token == stash.token)
             .delete(synchronize_session=False))
    return taken == 1


# Who a restore is done for, so a restorer that would bring back another trash entry with it (an
# expense day and its recurring rule) can check that person may restore that one too.
# UNRESTRICTED: the server's own command line (flask trash restore), which acts as the admin.
UNRESTRICTED = object()
_INHERIT = object()
_restoring_for = ContextVar('homehub_restoring_for', default=UNRESTRICTED)


def may_restore(owner) -> bool:
    """True when the person this restore is for may change records owned by ``owner`` (owner or admin)."""
    user = _restoring_for.get()
    if user is UNRESTRICTED:
        return True
    user = (user or '').strip()
    return bool(user) and can_modify(user, owner or '')


def restore_stash(stash: UndoStash, as_user=_INHERIT) -> str | None:
    """Put back what ``stash`` holds and delete it. Returns a note for the person, if the restorer has one.

    ``as_user`` is who the restore is for (see may_restore); a restore inside another one keeps its person.
    Raises RestoreConflict when it cannot be done. Does not commit.
    """
    if as_user is not _INHERIT:
        reset = _restoring_for.set(as_user)
        try:
            return restore_stash(stash)
        finally:
            _restoring_for.reset(reset)
    restore = _restorers.get(stash.kind)
    if restore is None:
        raise RestoreConflict(_('It is too late to undo that.'))
    kind, payload = stash.kind, stash.payload
    # Recurring generation takes this lock, then writes. A restore may generate (an expense rule) too, so it
    # takes the lock before its first write (the claim), never the other way round, or each waits on the other.
    with generation_lock:
        if not claim_stash(stash):
            raise RestoreConflict(_('That is no longer in the trash.'))
        db.session.expunge(stash)
        note = restore(json.loads(payload))
    return note if isinstance(note, str) else None


@restorer('trash_batch')
def _restore_batch(payload: dict):
    notes = []
    for token in payload.get('tokens') or []:
        stash = UndoStash.query.filter_by(token=token).first()
        # Already restored or deleted for good from the Trash page
        if stash is None or stash.expires_at < utcnow():
            continue
        # Each was deleted by whoever holds this Undo, so it is restored for them
        note = restore_stash(stash, as_user=stash.deleted_by)
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
    # The Undo button only reaches whoever made the change; a deleted record is restored for that person
    as_user = stash.deleted_by if stash.module else UNRESTRICTED
    try:
        note = restore_stash(stash, as_user=as_user)
        purge_expired()
        db.session.commit()
    except RestoreConflict as exc:
        db.session.rollback()
        return jsonify({'ok': False, 'error': exc.user_message}), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Undo failed for %s', kind)
        return jsonify({'ok': False, 'error': _('Could not undo that change.')}), 500
    flash(_('Change undone.') + (' ' + note if note else ''), 'success')
    return jsonify({'ok': True})
