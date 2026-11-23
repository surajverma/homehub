"""API operations for the shared Trash: what was deleted in the last 7 days, and restoring it.

The same entries as the Trash page and ``flask trash list/restore``, named by the same ``t-`` references
(never by the entry's own token, which is the Undo secret). A token sees the entries of the tools it can
read. Restoring needs what moving to the trash needs: Read & write on the tool and Allow deleting covering
it, so no existing token can do more than before. Only whoever added a record, or the admin, may restore
it, as on the Trash page; the API never acts as the admin.
"""
from flask_babel import gettext as _

from .. import trash
from ..clock import utcnow
from ..models import db
from ..undo import RestoreConflict, purge_expired
from . import tokens
from .core import ApiError
from .registry import iso, operation

MODULE_KEYS = [m.key for m in trash.MODULES]


def _entry(entry, now) -> dict:
    return {
        'ref': trash.entry_ref(entry),
        'module': entry.module,
        'label': entry.label or '',
        'owner': entry.owner or None,
        'deleted_by': entry.deleted_by or None,
        'deleted_at': iso(entry.deleted_at),
        'expires_at': iso(entry.expires_at),
        'days_left': trash.days_left(entry, now),
    }


def _not_found(ref: str):
    return ApiError(404, 'not_found', _('Nothing in the trash has the reference %(ref)s.', ref=ref))


@operation('trash_list', tool=None, level='read', method='GET', path='/trash', tag='Trash',
           title='List the trash',
           description=('Everything deleted in HomeHub in the last 7 days, newest first, from the tools this token can '
                        'read: what it was, which tool it came from, who added it, who deleted it and how many days '
                        'are left before it is gone for good. Restore an entry with trash_restore and its ref.'),
           properties={'module': {'type': 'string', 'in': 'query', 'enum': MODULE_KEYS, 'x-casefold': True,
                                  'description': 'Only entries from this tool.'}},
           any_tool=tokens.DELETABLE, idempotent=True)
def trash_list(ctx, args):
    purge_expired()
    db.session.commit()
    readable = tokens.effective_permissions(ctx.token)
    module = args.get('module')
    if module and module not in readable:
        label = str(tokens.TOOLS_BY_KEY[module].label)
        raise ApiError(403, 'forbidden', _('This token has no access to %(tool)s.', tool=label))
    now = utcnow()
    rows = [_entry(e, now) for e in trash.entries(module) if e.module in readable]
    return {'entries': rows, 'count': len(rows)}


@operation('trash_restore', tool=None, level='write', method='POST', path='/trash/<ref>/restore', tag='Trash',
           title='Restore from the trash',
           description=('Put back something from the trash, by the ref that trash_list gives it (such as t-0a1b2c3d4e). '
                        'Needs a token with Allow deleting and Read & write on the tool it came from. Only whoever '
                        'added it may restore it.'),
           properties={'ref': {'type': 'string', 'in': 'path', 'maxLength': 32,
                               'description': 'The reference from trash_list, such as t-0a1b2c3d4e.'}},
           required=('ref',), any_tool=tokens.DELETABLE, needs_delete=True)
def trash_restore(ctx, args):
    ref = args['ref'].strip().lower()
    try:
        entry = trash.entry_by_ref(ref)
    except trash.AmbiguousRef:
        raise ApiError(409, 'conflict', _('More than one entry has the reference %(ref)s. Restore it on the Trash page instead.', ref=ref))
    # An entry from a tool the token cannot read is not there, as far as the token knows
    if entry is None or entry.module not in tokens.effective_permissions(ctx.token):
        raise _not_found(ref)
    label = entry.label or ''
    tool = str(tokens.TOOLS_BY_KEY[entry.module].label)
    if entry.module not in tokens.delete_tools(ctx.token):
        raise ApiError(403, 'forbidden', _('Restoring from %(tool)s needs a token with Allow deleting and Read & write on %(tool)s.', tool=tool))
    if not trash.may_change(ctx.user, entry):
        raise ApiError(403, 'forbidden', _('%(name)s cannot restore %(item)s.', name=ctx.user, item=label))
    module = entry.module
    try:
        note = trash.restore(entry, ctx.user)
    except RestoreConflict as exc:
        raise ApiError(409, 'conflict', exc.user_message)
    ctx.target = f'{ref} {label}'
    summary = _('%(name)s restored.', name=label)
    return {'ref': ref, 'module': module, 'label': label, 'restored': True,
            'summary': summary + (' ' + note if note else '')}
