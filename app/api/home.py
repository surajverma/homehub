"""API operations for the Welcome page: who is home, personal status, the notice board and reminders."""
from datetime import date

from flask_babel import gettext as _

from .. import admin
from ..blueprints import dashboard
from ..clock import utcnow
from ..i18n import format_local_datetime
from ..models import Notice, Reminder, db
from ..security import sanitize_html, sanitize_text
from . import tokens
from .core import ApiError
from .registry import iso, operation

MAX_RANGE_DAYS = 62
STATUS_KEYS = {'home': 'Home', 'away': 'Away'}


def day_label(d: date) -> str:
    """A short date in the household language, such as "Sun 26 Oct"."""
    return format_local_datetime(d, '%a %d %b')


def _family_member(raw: str) -> str:
    """The configured spelling of a family member's name (case-insensitive), never the admin."""
    wanted = sanitize_text(raw or '').strip()
    for member in tokens.family_members():
        if member.casefold() == wanted.casefold() and not tokens.is_admin_name(member):
            return member
    raise ApiError(404, 'not_found', _('%(name)s is not a family member.', name=wanted))


def _require_act_as(ctx):
    if not ctx.token.act_as_members:
        raise ApiError(403, 'forbidden', _('This needs a token that can act as family members.'))


def _status_label(stored):
    labels = {'Home': _('Home'), 'Away': _('Away'), 'Out': _('Out'), 'Traveling': _('Traveling')}
    return labels.get(stored, stored)


# --- Who is home ---

def _presence_entry(name, stored):
    return {
        'name': name,
        # English keys, lower case: home, away (the web app can also set out and traveling)
        'status': stored.lower() if stored else None,
        'label': _status_label(stored) if stored else None,
    }


@operation('presence_list', tool='presence', level='read', method='GET', path='/presence',
           title='Who is home',
           description="Every family member's Who is home status: home, away (the web app can also set out or traveling), or null when not set.",
           idempotent=True)
def presence_list(ctx, args):
    family = [m for m in tokens.family_members() if not tokens.is_admin_name(m)]
    statuses = dashboard.who_statuses_for(family)
    return {'people': [_presence_entry(name, statuses.get(name)) for name in family]}


@operation('presence_set', tool='presence', level='write', method='PUT', path='/presence/<name>',
           title='Set who is home',
           description=('Set a family member Home or Away, for example from a Home Assistant zone automation. '
                        'The name is matched without regard to case. Needs a token that can act as family members.'),
           properties={
               'name': {'type': 'string', 'in': 'path', 'maxLength': 64, 'description': 'The family member, for example Priya.'},
               'status': {'type': 'string', 'in': 'body', 'enum': ['home', 'away'], 'x-casefold': True,
                          'description': 'home or away (any case).'},
           },
           required=('name', 'status'), idempotent=True)
def presence_set(ctx, args):
    _require_act_as(ctx)
    name = _family_member(args['name'])
    stored = STATUS_KEYS[args['status']]
    dashboard.set_home_status(name, stored)
    ctx.target = f'{name}: {stored}'
    summary = _('%(name)s is home.', name=name) if stored == 'Home' else _('%(name)s is away.', name=name)
    return {**_presence_entry(name, stored), 'summary': summary}


# --- Personal status ---

@operation('statuses_list', tool='statuses', level='read', method='GET', path='/statuses',
           title='Personal statuses',
           description="Every family member's personal status text (null when they have none).",
           idempotent=True)
def statuses_list(ctx, args):
    family = [m for m in tokens.family_members() if not tokens.is_admin_name(m)]
    texts = dashboard.member_statuses_for(family)
    return {'statuses': [{'name': name, 'text': texts.get(name)} for name in family]}


@operation('statuses_set', tool='statuses', level='write', method='PUT', path='/statuses/me',
           title='Set my status',
           description=('Set or clear the personal status of the family member named in "by" (required). '
                        'An empty text clears it.'),
           properties={'text': {'type': 'string', 'in': 'body', 'maxLength': 500,
                                'description': 'The status, for example "Working from home". Empty clears it.'}},
           required=('text', 'by'), idempotent=True)
def statuses_set(ctx, args):
    _require_act_as(ctx)
    name = ctx.require_member()
    text = sanitize_text(args['text'])
    if text:
        dashboard.save_member_status(name, text)
        summary = _('Status for %(name)s: %(text)s', name=name, text=text)
    else:
        dashboard.clear_member_status(name)
        summary = _('Status for %(name)s cleared.', name=name)
    ctx.target = f'{name}: {text}' if text else name
    return {'name': name, 'text': text or None, 'summary': summary}


# --- Notice board ---

@operation('notice_get', tool='notice', level='read', method='GET', path='/notice',
           title='Notice board',
           description='The notice on the Welcome page (simple HTML), who last changed it and when. Only the admin edits it, in HomeHub.',
           idempotent=True)
def notice_get(ctx, args):
    n = Notice.query.order_by(Notice.updated_at.desc()).first()
    return {
        'content': (n.content or '') if n else '',
        'updated_by': (n.updated_by or None) if n else None,
        'updated_at': iso(n.updated_at) if n else None,
    }


# --- Reminders ---

def _reminder(r: Reminder) -> dict:
    data = dashboard._serialize_reminder(r)
    return {
        'id': data['id'],
        'date': data['start_date'] or data['date'],
        'time': data['start_time'],
        'end_date': data['end_date'],
        'end_time': data['end_time'],
        'all_day': data['all_day'],
        'title': data['title'],
        'description': data['description'],
        'category': data['category'],
        'creator': data['creator'],
        'recurring_id': data['recurring_id'],
        'done': bool(r.completed_at),
        'completed_at': data['completed_at'],
    }


def _categories() -> dict:
    cats = ((tokens._config().get('reminders') or {}).get('categories')) or []
    return {str(c.get('key')): c for c in cats if isinstance(c, dict) and c.get('key')}


def _reminder_window(args) -> tuple:
    scope = args.get('scope')
    start, end = args.get('from'), args.get('to')
    if scope and (start or end):
        raise ApiError(400, 'bad_request', _('Use either scope or from and to, not both.'))
    if start or end:
        if not (start and end):
            raise ApiError(400, 'bad_request', _('Send both from and to.'))
        if end < start:
            raise ApiError(400, 'bad_request', _('to must be on or after from.'))
        if (end - start).days + 1 > MAX_RANGE_DAYS:
            raise ApiError(400, 'bad_request', _('The range can be at most %(num)s days.', num=MAX_RANGE_DAYS))
        return start, end
    today = date.today()
    if scope == 'week':
        return dashboard.week_bounds(today)
    return today, today


@operation('reminders_list', tool='reminders', level='read', method='GET', path='/reminders',
           title='Reminders',
           description=('Reminders for today (the default), this week (Monday to Sunday, as the calendar shows it), '
                        'or a range of at most 62 days. Dates of recurring reminders are included (they have a '
                        'negative id and a recurring_id).'),
           properties={
               'scope': {'type': 'string', 'in': 'query', 'enum': ['today', 'week'], 'x-casefold': True,
                         'description': 'today or week. Leave out when sending from and to.'},
               'from': {'type': 'string', 'format': 'date', 'in': 'query', 'description': 'First day, YYYY-MM-DD.'},
               'to': {'type': 'string', 'format': 'date', 'in': 'query', 'description': 'Last day, YYYY-MM-DD.'},
           },
           idempotent=True)
def reminders_list(ctx, args):
    start, end = _reminder_window(args)
    _rows, _gen, _rules, _dates, combined = dashboard.reminders_in_window(start, end)
    return {'from': start.isoformat(), 'to': end.isoformat(), 'reminders': [_reminder(r) for r in combined]}


@operation('reminders_add', tool='reminders', level='write', method='POST', path='/reminders',
           title='Add a reminder',
           description='Add a one-off reminder (recurring reminders are set up in HomeHub).',
           properties={
               'title': {'type': 'string', 'in': 'body', 'minLength': 1, 'maxLength': 256, 'description': 'What to remember.'},
               'date': {'type': 'string', 'format': 'date', 'in': 'body', 'description': 'The day, YYYY-MM-DD.'},
               'time': {'type': 'string', 'format': 'time', 'in': 'body', 'description': 'Optional time, HH:MM (24-hour). Without it the reminder lasts all day.'},
               'description': {'type': 'string', 'in': 'body', 'maxLength': 5000, 'description': 'Optional details.'},
               'category': {'type': 'string', 'in': 'body', 'maxLength': 64,
                            'description': 'Optional category key from config.yml (reminders > categories), for example health.'},
           },
           required=('title', 'date'), success_status=201)
def reminders_add(ctx, args):
    title = sanitize_text(args['title'])
    if not title:
        raise ApiError(400, 'bad_request', _('Title required'))
    category = args.get('category')
    if category is not None:
        category = category.strip()
        if category and category not in _categories():
            raise ApiError(400, 'bad_request', _('Unknown category: %(name)s. Use a key from config.yml: %(keys)s.',
                                                 name=category, keys=', '.join(_categories()) or '-'))
    start_t = args.get('time')
    r = dashboard.save_one_off_reminder(args['date'], start_t, None, None, not start_t, title,
                                        sanitize_html(args.get('description', '')), ctx.user, category or None, None)
    ctx.target = f'#{r.id} {title}'
    when = day_label(r.date) + (f' {start_t}' if start_t else '')
    return {**_reminder(r), 'summary': _('Reminder added: %(title)s, %(when)s.', title=title, when=when)}


def _stored_reminder(ctx, reminder_id: int) -> Reminder:
    if reminder_id < 0:
        raise ApiError(400, 'bad_request', _('This is a date of a recurring reminder. Recurring reminders are changed in HomeHub.'))
    r = db.session.get(Reminder, reminder_id)
    # deleted_at: still in the calendar's old trash, until the upgrade moves it to the shared Trash
    if r is None or r.deleted_at is not None:
        raise ApiError(404, 'not_found', _('That reminder was not found.'))
    if not admin.can_modify(ctx.user, r.creator or ''):
        raise ApiError(403, 'forbidden', _('%(name)s cannot change this reminder.', name=ctx.user))
    return r


ID_PROPERTY = {'type': 'integer', 'in': 'path', 'description': 'The reminder id from the list.'}


@operation('reminders_set_done', tool='reminders', level='write', method='PUT', path='/reminders/<int(signed=True):reminder_id>/done',
           title='Mark a reminder done or not done',
           description=('Set a reminder done (true) or not done (false). Setting the same value again changes nothing. '
                        'Only whoever added the reminder may change it.'),
           properties={'reminder_id': ID_PROPERTY,
                       'done': {'type': 'boolean', 'in': 'body', 'description': 'true for done, false for not done.'}},
           required=('reminder_id', 'done'), idempotent=True)
def reminders_set_done(ctx, args):
    r = _stored_reminder(ctx, args['reminder_id'])
    if args['done'] and not r.completed_at:
        r.completed_at = utcnow()
    elif not args['done']:
        r.completed_at = None
    db.session.commit()
    ctx.target = f'#{r.id} {r.title}'
    summary = (_('%(title)s is done.', title=r.title) if args['done']
               else _('%(title)s is not done.', title=r.title))
    return {**_reminder(r), 'summary': summary}


@operation('reminders_trash', tool='reminders', level='write', method='DELETE', path='/reminders/<int(signed=True):reminder_id>',
           title='Move a reminder to the trash',
           description=('Move a reminder to the trash, where it can be restored from the Trash page for 7 days. Needs a '
                        'token with Allow deleting. Only whoever added the reminder may do this.'),
           properties={'reminder_id': ID_PROPERTY},
           required=('reminder_id',), idempotent=True, destructive=True)
def reminders_trash(ctx, args):
    r = _stored_reminder(ctx, args['reminder_id'])
    reminder_id, title = r.id, r.title
    if dashboard.trash_reminder(r, ctx.deleted_by) is None:
        raise ApiError(404, 'not_found', _('That reminder was not found.'))
    ctx.target = f'#{reminder_id} {title}'
    return {'id': reminder_id, 'title': title, 'trashed': True,
            'summary': _('%(title)s moved to the trash. It can be restored from the Trash page for 7 days.', title=title)}



def nothing_to_change():
    return ApiError(400, 'bad_request', _('Send at least one field to change.'))


@operation('reminders_edit', tool='reminders', level='write', method='PATCH', path='/reminders/<int(signed=True):reminder_id>',
           title='Edit a reminder',
           description=('Change a one-off reminder: any of title, date, time (null makes it all-day), description and category. '
                        'Only whoever added the reminder may change it. Recurring reminders are changed in HomeHub.'),
           properties={
               'reminder_id': ID_PROPERTY,
               'title': {'type': 'string', 'in': 'body', 'minLength': 1, 'maxLength': 256, 'description': 'New title.'},
               'date': {'type': 'string', 'format': 'date', 'in': 'body', 'description': 'New day, YYYY-MM-DD.'},
               'time': {'type': 'string', 'format': 'time', 'in': 'body', 'x-nullable': True,
                        'description': 'New time, HH:MM (24-hour), or null to make it all-day.'},
               'description': {'type': 'string', 'in': 'body', 'maxLength': 5000, 'description': 'New details (empty clears them).'},
               'category': {'type': 'string', 'in': 'body', 'maxLength': 64,
                            'description': 'Category key from config.yml (reminders > categories); empty clears it.'},
           },
           required=('reminder_id',), idempotent=True)
def reminders_edit(ctx, args):
    if args['reminder_id'] < 0:
        raise ApiError(409, 'conflict', _('This is a date of a recurring reminder. Recurring reminders are changed in HomeHub.'))
    editable = ('title', 'date', 'time', 'description', 'category')
    if not any(k in args for k in editable):
        raise nothing_to_change()
    r = db.session.get(Reminder, args['reminder_id'])
    if r is None or r.deleted_at is not None:
        raise ApiError(404, 'not_found', _('That reminder was not found.'))
    if r.recurring_id:
        raise ApiError(409, 'conflict', _('This reminder belongs to a recurring reminder. Recurring reminders are changed in HomeHub.'))
    if not admin.can_modify(ctx.user, r.creator or ''):
        raise ApiError(403, 'forbidden', _('%(name)s cannot change this reminder.', name=ctx.user))
    payload = {}
    if 'title' in args:
        title = sanitize_text(args['title'])
        if not title:
            raise ApiError(400, 'bad_request', _('Title required'))
        payload['title'] = title
    if 'description' in args:
        payload['description'] = args['description']
    if 'date' in args:
        payload['date'] = args['date'].isoformat()
    if args.get('time') is not None:
        payload['time'] = args['time']
    if 'category' in args:
        category = args['category'].strip()
        if category and category not in _categories():
            raise ApiError(400, 'bad_request', _('Unknown category: %(name)s. Use a key from config.yml: %(keys)s.',
                                                 name=category, keys=', '.join(_categories()) or '-'))
        payload['category'] = category or None
    # The calendar's own edit logic; it keeps a time sent as null, so clearing it is done below
    problem = dashboard.apply_reminder_changes(r, payload)
    if problem:
        raise ApiError(400, 'bad_request', problem)
    if 'time' in args and args['time'] is None:
        r.time = None
        r.start_time = None
        r.all_day = not r.end_date and not r.end_time
        ok, problem = dashboard._validate_range(r.start_date or r.date, None, r.end_date, r.end_time)
        if not ok:
            raise ApiError(400, 'bad_request', problem)
    db.session.commit()
    ctx.target = f'#{r.id} {r.title}'
    when = day_label(r.start_date or r.date) + (f' {r.start_time}' if r.start_time else '')
    return {**_reminder(r), 'summary': _('Reminder updated: %(title)s, %(when)s.', title=r.title, when=when)}
