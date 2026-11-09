"""The shared trash: records deleted in the last 7 days, from every module, on one page.

Deleting moves a copy of the record into the undo store (app/undo.py) as a trash entry and removes
the record from its table. The Undo toast and this page both restore from that copy. Who may restore
or delete an entry for good follows the module's own rule (admin.can_modify against the record's owner).
"""
import math
from dataclasses import dataclass

import click
from flask import current_app, flash, redirect, render_template, request, url_for
from flask.cli import AppGroup
from flask_babel import gettext as _

from .admin import can_modify
from .blueprints import main_bp
from .clock import utcnow
from .models import UndoStash, db
from .security import sanitize_text
from .undo import RestoreConflict, purge_expired, restore_stash


def N_(text):
    """Marks a string for translation (pybabel extracts N_); it is translated when shown."""
    return text


@dataclass(frozen=True)
class Module:
    key: str
    label_text: str
    icon: str
    # Key under feature_toggles and what a missing line means (as the sidebar in base.html)
    feature: str
    default_on: bool
    endpoint: str

    @property
    def label(self) -> str:
        return _(self.label_text)


# In the sidebar's order; the labels are the sidebar's
MODULES = (
    Module('notes', N_('Shared Notes'), 'fa-note-sticky', 'notes', False, 'main.notes'),
    Module('shopping', N_('Shopping List'), 'fa-cart-shopping', 'shopping_list', False, 'main.shopping'),
    Module('chores', N_('To-Do/Chores'), 'fa-list-check', 'chores', False, 'main.chores'),
    Module('recipes', N_('Recipe Book'), 'fa-utensils', 'recipes', False, 'main.recipes'),
    Module('expiry', N_('Expiry Tracker'), 'fa-hourglass-half', 'expiry_tracker', False, 'main.expiry'),
    Module('expenses', N_('Expense Tracker'), 'fa-wallet', 'expense_tracker', True, 'main.expenses'),
)
MODULES_BY_KEY = {m.key: m for m in MODULES}


def module_enabled(module: Module, config: dict) -> bool:
    toggles = config.get('feature_toggles') or {}
    return bool(toggles.get(module.feature, module.default_on))


def entries(module: str | None = None) -> list:
    """Trash entries still in time, newest first; only ``module``'s when given."""
    query = UndoStash.query.filter(UndoStash.module.isnot(None), UndoStash.expires_at >= utcnow())
    if module:
        query = query.filter(UndoStash.module == module)
    return query.order_by(UndoStash.deleted_at.desc(), UndoStash.id.desc()).all()


def days_left(entry: UndoStash, now=None) -> int:
    """Whole days until the entry is deleted for good, rounded up (so never 0 while it is still there)."""
    seconds = (entry.expires_at - (now or utcnow())).total_seconds()
    return max(1, math.ceil(seconds / 86400))


def get_entry(entry_id: int) -> UndoStash | None:
    entry = db.session.get(UndoStash, entry_id)
    if entry is None or entry.module is None or entry.expires_at < utcnow():
        return None
    return entry


def restore(entry: UndoStash) -> str | None:
    """Put the entry's record back and remove it from the trash. Commits; raises RestoreConflict."""
    try:
        note = restore_stash(entry)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return note


def delete_for_good(entry: UndoStash) -> None:
    db.session.delete(entry)
    db.session.commit()


# --- Page ---

def _back(module: str | None):
    return redirect(url_for('main.trash_page', module=module) if module else url_for('main.trash_page'))


def _form_module() -> str | None:
    module = request.form.get('module') or None
    return module if module in MODULES_BY_KEY else None


@main_bp.route('/trash', methods=['GET'])
def trash_page():
    config = current_app.config['HOMEHUB_CONFIG']
    purge_expired()
    db.session.commit()
    module = request.args.get('module') or None
    if module not in MODULES_BY_KEY:
        module = None
    now = utcnow()
    rows = [{
        'entry': e,
        'module': MODULES_BY_KEY.get(e.module),
        'days_left': days_left(e, now),
    } for e in entries(module)]
    shown = [m for m in MODULES if module_enabled(m, config) or m.key == module]
    return render_template('trash.html', config=config, rows=rows, modules=shown, module=module,
                           current=MODULES_BY_KEY.get(module) if module else None)


@main_bp.route('/trash/<int:entry_id>/restore', methods=['POST'])
def trash_restore(entry_id):
    user = sanitize_text(request.form.get('user', ''))
    module = _form_module()
    entry = get_entry(entry_id)
    if entry is None:
        flash(_('That is no longer in the trash.'), 'error')
        return _back(module)
    if not can_modify(user, entry.owner):
        flash(_('Not allowed to restore %(name)s.', name=entry.label), 'error')
        return _back(module)
    label = entry.label
    try:
        note = restore(entry)
    except RestoreConflict as exc:
        flash(str(exc), 'error')
        return _back(module)
    except Exception:
        current_app.logger.exception('Restoring trash entry %s failed', entry_id)
        flash(_('Could not restore %(name)s.', name=label), 'error')
        return _back(module)
    flash(_('%(name)s restored.', name=label) + (' ' + note if note else ''), 'success')
    return _back(module)


@main_bp.route('/trash/<int:entry_id>/delete', methods=['POST'])
def trash_delete(entry_id):
    user = sanitize_text(request.form.get('user', ''))
    module = _form_module()
    entry = get_entry(entry_id)
    if entry is None:
        flash(_('That is no longer in the trash.'), 'error')
        return _back(module)
    if not can_modify(user, entry.owner):
        flash(_('Not allowed to delete %(name)s.', name=entry.label), 'error')
        return _back(module)
    label = entry.label
    delete_for_good(entry)
    flash(_('%(name)s deleted for good.', name=label), 'success')
    return _back(module)


# --- flask trash ---

trash_cli = AppGroup('trash', help='List and restore what was deleted in the last 7 days (the Trash page).')


@trash_cli.command('list')
@click.option('--module', type=click.Choice([m.key for m in MODULES]), help='Only this module.')
def list_command(module):
    """Everything in the trash, newest first, with the ID to restore it by."""
    purge_expired()
    db.session.commit()
    rows = entries(module)
    if not rows:
        click.echo('The trash is empty.')
        return
    now = utcnow()
    for e in rows:
        left = days_left(e, now)
        click.echo(f'{e.id}  [{e.module}]  {e.label}  - deleted by {e.deleted_by or "?"} on '
                   f'{e.deleted_at:%Y-%m-%d %H:%M} UTC; {left} day{"" if left == 1 else "s"} left')


@trash_cli.command('restore')
@click.argument('entry_id', type=int)
def restore_command(entry_id):
    """Restore trash entry ENTRY_ID (from flask trash list), whoever deleted it."""
    entry = get_entry(entry_id)
    if entry is None:
        raise click.ClickException(f'Nothing in the trash has ID {entry_id}. See: flask trash list')
    label, module = entry.label, entry.module
    try:
        note = restore(entry)
    except RestoreConflict as exc:
        raise click.ClickException(str(exc))
    click.echo(f'Restored {label} ({module}).' + (f' {note}' if note else ''))


def init_app(app):
    app.cli.add_command(trash_cli)
