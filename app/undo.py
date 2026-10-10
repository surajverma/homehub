"""Short-lived undo for changes that rewrite or remove other rows.

A change stashes the rows it is about to replace as JSON and offers the token to the
browser, which shows an Undo toast for UNDO_SECONDS. Nothing is kept beyond that:
expired stashes are deleted the next time one is made or used.
"""
import json
import secrets
from datetime import date, datetime, timedelta

from flask import current_app, flash, jsonify
from flask_babel import gettext as _

from . import db
from .blueprints import main_bp
from .clock import utcnow
from .models import UndoStash

UNDO_SECONDS = 20
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


def purge_expired(now: datetime | None = None) -> None:
    UndoStash.query.filter(UndoStash.expires_at < (now or utcnow())).delete(synchronize_session=False)


def stash_undo(kind: str, payload: dict) -> str:
    """Stash ``payload`` and return its token, for JSON endpoints whose page shows the Undo toast itself. Commits."""
    purge_expired()
    token = secrets.token_hex(16)
    db.session.add(UndoStash(
        token=token,
        kind=kind,
        payload=json.dumps(payload),
        expires_at=utcnow() + timedelta(seconds=UNDO_SECONDS + _GRACE_SECONDS),
    ))
    db.session.commit()
    return token


def offer_undo(kind: str, payload: dict, message: str, category: str = 'success') -> str:
    """Stash ``payload`` and flash ``message`` with an Undo button. Commits."""
    token = stash_undo(kind, payload)
    flash(json.dumps({'token': token, 'message': message, 'type': category, 'seconds': UNDO_SECONDS}), 'undo')
    return token


@main_bp.route('/undo/<token>', methods=['POST'])
def undo_change(token):
    # Holding the token is the permission: it only ever reaches the browser that made the change
    stash = UndoStash.query.filter_by(token=token).first()
    if stash is None or stash.expires_at < utcnow() or stash.kind not in _restorers:
        purge_expired()
        db.session.commit()
        return jsonify({'ok': False, 'error': _('It is too late to undo that.')}), 410
    try:
        _restorers[stash.kind](json.loads(stash.payload))
        db.session.delete(stash)
        purge_expired()
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Undo failed for %s', stash.kind)
        return jsonify({'ok': False, 'error': _('Could not undo that change.')}), 500
    flash(_('Change undone.'), 'success')
    return jsonify({'ok': True})
