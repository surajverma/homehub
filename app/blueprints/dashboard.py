from flask import render_template, request, redirect, url_for, flash, jsonify, current_app
from datetime import datetime, date, timedelta
from ..models import db, HomeStatus, MemberStatus, Notice, Reminder, RecurringReminder, Chore
from ..blueprints import main_bp
from ..clock import utcnow
from ..admin import is_admin, can_modify
from ..security import sanitize_html, sanitize_text
from ..recurrence import first_on_or_after, occurrences_between, rule_interval_unit
from flask_babel import gettext as _, ngettext
from ..undo import restore_row, restorer, row_to_dict, stamp, stash_undo


def _parse_date_param(value, default=None):
    if not value:
        return default
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except Exception:
        return default


def _parse_time_param(value):
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if len(raw) == 5 and raw[2] == ':':
        hh, mm = raw.split(':', 1)
        if hh.isdigit() and mm.isdigit():
            hhi, mmi = int(hh), int(mm)
            if 0 <= hhi < 24 and 0 <= mmi < 60:
                return f"{hhi:02d}:{mmi:02d}"
    return None


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _datetime_from_parts(dv, tv, *, is_end=False):
    if not dv:
        return None
    if tv:
        try:
            hh, mm = tv.split(':', 1)
            return datetime(dv.year, dv.month, dv.day, int(hh), int(mm))
        except Exception:
            return None
    if is_end:
        return datetime(dv.year, dv.month, dv.day, 23, 59, 59)
    return datetime(dv.year, dv.month, dv.day, 0, 0, 0)


def _validate_range(start_date, start_time, end_date, end_time):
    if not end_date and not end_time:
        return True, None
    if end_time and not end_date:
        return False, _('End date is required when end time is provided')
    sdt = _datetime_from_parts(start_date, start_time, is_end=False)
    edt = _datetime_from_parts(end_date, end_time, is_end=True)
    if not sdt or not edt:
        return False, _('Invalid start or end date/time')
    if edt < sdt:
        return False, _('End date/time must be the same as or after start date/time')
    return True, None


def _reminder_start_date(r: Reminder):
    return getattr(r, 'start_date', None) or r.date


def _reminder_end_date(r: Reminder):
    return getattr(r, 'end_date', None) or _reminder_start_date(r)


def _show_chores_on_homepage() -> bool:
    try:
        row = db.session.execute(db.text("SELECT value FROM app_setting WHERE key='show_chores_on_homepage'"))
        val = row.scalar()
        if val is None:
            cfg = current_app.config.get('HOMEHUB_CONFIG', {})
            return bool((cfg.get('feature_toggles') or {}).get('show_chores_on_homepage', False))
        return str(val).strip().lower() in ('1', 'true', 'yes', 'on')
    except Exception:
        return False


@main_bp.route('/')
def index():
    return _render_dashboard(calendar_only=False)


@main_bp.route('/calendar')
def calendar_page():
    return _render_dashboard(calendar_only=True)


def _render_dashboard(calendar_only=False):
    config = current_app.config['HOMEHUB_CONFIG']
    notice = Notice.query.order_by(Notice.updated_at.desc()).first()
    show_chores_on_homepage = _show_chores_on_homepage()
    # Who is Home summary
    family = list(dict.fromkeys(config.get('family_members', [])))
    who_statuses = {s.name: s.status for s in HomeStatus.query.all() if s.name in family}
    member_statuses = {ms.name: ms.text for ms in MemberStatus.query.all() if ms.name in family and (ms.text or '').strip()}
    # Extract reminder categories
    reminder_categories = []
    try:
        rcfg = (config.get('reminders') or {}).get('categories') or []
        if isinstance(rcfg, list):
            for entry in rcfg:
                if not isinstance(entry, dict):
                    continue
                key = entry.get('key')
                if not key:
                    continue
                reminder_categories.append({
                    'key': key,
                    'label': entry.get('label') or key,
                    'color': entry.get('color') or None,
                })
    except Exception:
        reminder_categories = []
    home_chores = []
    if show_chores_on_homepage and config.get('feature_toggles', {}).get('chores', True):
        try:
            home_chores = (
                Chore.query
                .filter(Chore.done == False)  # noqa: E712
                .order_by(Chore.due_date.asc(), Chore.timestamp.desc())
                .limit(8)
                .all()
            )
        except Exception:
            home_chores = []
    return render_template(
        'index.html',
        config=config,
        calendar_only=calendar_only,
        notice=notice,
        who_statuses=who_statuses,
        member_statuses=member_statuses,
        reminder_categories=reminder_categories,
        home_chores=home_chores,
        show_chores_on_homepage=show_chores_on_homepage,
    )


def _serialize_reminder(r: Reminder):
    start_date = getattr(r, 'start_date', None) or r.date
    start_time = getattr(r, 'start_time', None)
    if start_time is None:
        start_time = getattr(r, 'time', None)
    all_day = bool(getattr(r, 'all_day', False))
    if (start_time is None or start_time == '') and not getattr(r, 'end_date', None) and not getattr(r, 'end_time', None):
        all_day = True
    return {
        'id': r.id,
        'date': r.date.strftime('%Y-%m-%d') if r.date else None,
        'time': getattr(r, 'time', None) or None,
        'start_date': start_date.strftime('%Y-%m-%d') if start_date else None,
        'start_time': start_time or None,
        'end_date': getattr(r, 'end_date', None).strftime('%Y-%m-%d') if getattr(r, 'end_date', None) else None,
        'end_time': getattr(r, 'end_time', None) or None,
        'all_day': all_day,
        'title': r.title,
        'description': r.description or '',
        'creator': r.creator or '',
        'category': getattr(r, 'category', None),
        'color': getattr(r, 'color', None),
        'recurring_id': getattr(r, 'recurring_id', None),
        'timestamp': r.timestamp.isoformat() if r.timestamp else None,
        'updated_at': getattr(r, 'updated_at', None).isoformat() if getattr(r, 'updated_at', None) else None,
        'completed_at': getattr(r, 'completed_at', None).isoformat() if getattr(r, 'completed_at', None) else None,
        'deleted_at': getattr(r, 'deleted_at', None).isoformat() if getattr(r, 'deleted_at', None) else None,
    }

def _serialize_recurring_rule(rr: RecurringReminder):
    interval, unit = rule_interval_unit(rr)
    return {
        'id': rr.id,
        'title': rr.title,
        'description': rr.description or '',
        'creator': rr.creator or '',
        'interval': int(interval),
        'unit': unit,
        'time': rr.time,
        'category': rr.category,
        'color': rr.color,
        'start_date': rr.start_date.strftime('%Y-%m-%d') if rr.start_date else None,
        'end_date': rr.end_date.strftime('%Y-%m-%d') if rr.end_date else None,
    }


@main_bp.route('/api/reminders')
def api_reminders_list():
    scope = (request.args.get('scope', 'day') or 'day').lower()
    base_date = _parse_date_param(request.args.get('date'), date.today())
    q = Reminder.query.filter(Reminder.deleted_at.is_(None))
    if scope == 'month':
        start = base_date.replace(day=1)
        if start.month == 12:
            next_month = start.replace(year=start.year + 1, month=1, day=1)
        else:
            next_month = start.replace(month=start.month + 1, day=1)
        end = next_month - timedelta(days=1)
        q = q.filter(Reminder.date >= start, Reminder.date <= end)
    elif scope == 'week':
        start = base_date - timedelta(days=base_date.weekday())
        end = start + timedelta(days=6)
        q = q.filter(Reminder.date >= start, Reminder.date <= end)
    else:
        q = q.filter(Reminder.date == base_date)
    try:
        from sqlalchemy import case
        rows = q.order_by(
            Reminder.date.asc(),
            case((Reminder.time.is_(None), 1), (Reminder.time == '', 1), else_=0).asc(),
            Reminder.time.asc(),
            Reminder.id.asc(),
        ).all()
    except Exception:
        rows = q.order_by(Reminder.date.asc(), Reminder.id.asc()).all()
    # Generate from recurring rules within scope window (without altering past)
    try:
        rules = RecurringReminder.query.all()
    except Exception:
        rules = []
    gen_rows = []
    rule_dates = {}  # rr.id -> list of dates within window
    if scope == 'month':
        window_start = start
        window_end = end
    elif scope == 'week':
        window_start = start
        window_end = end
    else:
        window_start = base_date
        window_end = base_date
    for rr in rules:
        for d in occurrences_between(rr, window_start, window_end):
            # ensure not already present in DB rows for that date/title
            if not any((r.date == d and r.title == rr.title and r.recurring_id == rr.id) for r in rows):
                temp = Reminder(date=d, title=rr.title, description=rr.description or '', creator=rr.creator or '', time=rr.time, category=rr.category, color=rr.color)
                temp.id = -(1000000 + rr.id)  # ephemeral negative ID
                temp.recurring_id = rr.id
                gen_rows.append(temp)
            rule_dates.setdefault(rr.id, []).append(d)
    combined = rows + gen_rows
    # Sort combined
    try:
        combined.sort(key=lambda r: (r.date, (r.time is None or r.time == ''), r.time or '', r.id))
    except Exception:
        pass
    data = [_serialize_reminder(r) for r in combined]
    counts = {}
    categories_counts = {}
    if scope == 'month':
        # Include stored and synthesized rows in counts for calendar dots.
        # For one-time reminders with end_date, paint every day in the selected month span.
        for r in (rows + gen_rows):
            sd = _reminder_start_date(r)
            ed = _reminder_end_date(r)
            if not sd or not ed:
                continue
            if ed < sd:
                ed = sd
            dcur = sd if sd >= start else start
            dend = ed if ed <= end else end
            while dcur <= dend:
                k = dcur.strftime('%Y-%m-%d')
                counts[k] = counts.get(k, 0) + 1
                cat = getattr(r, 'category', None) or '_uncategorized'
                if k not in categories_counts:
                    categories_counts[k] = {}
                categories_counts[k][cat] = categories_counts[k].get(cat, 0) + 1
                dcur = dcur + timedelta(days=1)

    # Build recurring rules summary for UI compression
    recurring_rules = []
    for rr in rules:
        interval, unit = rule_interval_unit(rr)
        recurring_rules.append({
            'id': rr.id,
            'title': rr.title,
            'description': rr.description or '',
            'creator': rr.creator or '',
            'interval': int(interval),
            'unit': unit,
            'time': rr.time,
            'category': rr.category,
            'color': rr.color,
            'start_date': rr.start_date.strftime('%Y-%m-%d') if rr.start_date else None,
            'end_date': rr.end_date.strftime('%Y-%m-%d') if rr.end_date else None,
            'dates': [d.strftime('%Y-%m-%d') for d in rule_dates.get(rr.id, [])],
        })
    return jsonify({
        'ok': True,
        'scope': scope,
        'date': base_date.strftime('%Y-%m-%d'),
        'reminders': data,
        'counts': counts,
        'categories_counts': categories_counts,
        'recurring_rules': recurring_rules,
    })


_RULE_COPY_FIELDS = ('title', 'description', 'creator', 'frequency', 'monthly_mode', 'interval', 'unit',
                     'time', 'category', 'color', 'start_date', 'end_date')


def _stash_reminder_rule(snapshot: dict, successor: RecurringReminder | None = None) -> str:
    return stash_undo('reminder_rule', {
        'rule': snapshot,
        'successor_id': successor.id if successor else None,
        'successor_timestamp': stamp(successor),
    })


@restorer('reminder_rule')
def _restore_reminder_rule(payload: dict) -> None:
    # The rule an edit continued as, unless its id belongs to another rule by now
    successor = db.session.get(RecurringReminder, payload['successor_id']) if payload.get('successor_id') else None
    if successor is not None and stamp(successor) == payload.get('successor_timestamp'):
        db.session.delete(successor)
        db.session.flush()
    restore_row(RecurringReminder, payload['rule'])


@main_bp.route('/api/recurring_rules/<int:rid>', methods=['PATCH', 'DELETE'])
def api_recurring_rules_update_delete(rid):
    """Edit or delete a recurring reminder without rewriting the past.

    Occurrences are worked out from the rule each time, so changing or removing the rule
    itself would also change every earlier date. A rule that has already started is
    ended yesterday instead, and an edit continues as a new rule from today.
    """
    rr = db.get_or_404(RecurringReminder, rid)
    payload = request.get_json(silent=True) or {}
    user = sanitize_text(payload.get('creator', ''))
    if not can_modify(user, rr.creator or ''):
        return jsonify({'ok': False, 'error': _('Not allowed')}), 403
    today = date.today()
    yesterday = today - timedelta(days=1)
    has_past = bool(rr.start_date and rr.start_date < today)
    # Every change below hands back a token the page offers as Undo for about a minute
    before = row_to_dict(rr)
    if request.method == 'DELETE':
        # scope 'all' removes the rule and with it every date, past ones included
        if payload.get('scope') == 'all' or not has_past:
            db.session.delete(rr)
            db.session.commit()
            return jsonify({'ok': True, 'ended': False, 'undo_token': _stash_reminder_rule(before)})
        if not rr.end_date or rr.end_date > yesterday:
            rr.end_date = yesterday
            db.session.commit()
        return jsonify({'ok': True, 'ended': True, 'undo_token': _stash_reminder_rule(before)})
    # PATCH
    current = {name: getattr(rr, name) for name in _RULE_COPY_FIELDS}
    current['interval'], current['unit'] = rule_interval_unit(rr)
    new = dict(current)
    if 'title' in payload: new['title'] = sanitize_text(payload.get('title') or rr.title)
    if 'description' in payload: new['description'] = sanitize_html(payload.get('description') or '')
    if 'time' in payload: new['time'] = _parse_time_param(payload.get('time'))
    if 'category' in payload: new['category'] = sanitize_text(payload.get('category')) or None
    if 'color' in payload: new['color'] = sanitize_text(payload.get('color')) or None
    interval = payload.get('interval')
    try:
        interval = int(interval) if interval is not None else None
    except Exception:
        interval = None
    if interval and interval >= 1: new['interval'] = interval
    unit = (payload.get('unit') or '').lower()
    if unit in {'day','week','month','year'}: new['unit'] = unit
    def _pd(s):
        try:
            return datetime.strptime(s, '%Y-%m-%d').date() if s else None
        except Exception:
            return None
    if 'start_date' in payload:
        sd = _pd(payload.get('start_date'))
        if sd: new['start_date'] = sd
    if 'end_date' in payload:
        new['end_date'] = _pd(payload.get('end_date'))

    changed = {name for name in _RULE_COPY_FIELDS if (new[name] or None) != (current[name] or None)}
    if not changed:
        return jsonify({'ok': True, 'rule': _serialize_recurring_rule(rr), 'split': False})

    if not has_past:
        # Nothing has happened yet, so the rule itself can change
        for name in changed:
            setattr(rr, name, new[name])
        db.session.commit()
        return jsonify({'ok': True, 'rule': _serialize_recurring_rule(rr), 'split': False,
                        'undo_token': _stash_reminder_rule(before)})

    ended = bool(rr.end_date and rr.end_date < today)
    already_ended = jsonify({'ok': False, 'error': _('This recurring reminder has already ended, and its past dates are kept as they were. Set a later end date to continue it.')}), 400
    if changed == {'end_date'} and (not ended or (new['end_date'] and new['end_date'] < rr.end_date)):
        # A rule still running takes whatever end it is given. One that has ended can only end
        # earlier here: a later end must not bring back the days since it stopped.
        rr.end_date = new['end_date']
        db.session.commit()
        return jsonify({'ok': True, 'rule': _serialize_recurring_rule(rr), 'split': False,
                        'undo_token': _stash_reminder_rule(before)})

    if new['end_date'] and new['end_date'] < today:
        return already_ended
    if ended and 'end_date' not in changed:
        # Its end is still in the past, so the edit could only change dates that are history
        return already_ended

    # The new settings continue from today on their own schedule; the old rule keeps the past
    successor = RecurringReminder(**new)
    successor.start_date = first_on_or_after(successor, today)
    if successor.start_date is None:
        return jsonify({'ok': False, 'error': _('With these settings there are no dates left from today. Choose a later end date.')}), 400
    successor.effective_from = successor.start_date
    if not rr.end_date or rr.end_date > yesterday:
        rr.end_date = yesterday
    db.session.add(successor)
    db.session.commit()
    return jsonify({'ok': True, 'rule': _serialize_recurring_rule(successor), 'split': True,
                    'undo_token': _stash_reminder_rule(before, successor)})


@main_bp.route('/api/reminders', methods=['POST'])
def api_reminders_create():
    payload = request.get_json(silent=True) or {}
    title = sanitize_text(payload.get('title', ''))
    creator = sanitize_text(payload.get('creator', ''))
    description = sanitize_html(payload.get('description', ''))
    if not title:
        return jsonify({'ok': False, 'error': _('Title required')}), 400
    start_d = _parse_date_param(payload.get('start_date'), None) or _parse_date_param(payload.get('date'), None)
    if not start_d:
        return jsonify({'ok': False, 'error': _('Invalid date')}), 400
    all_day = False
    start_t = _parse_time_param(payload.get('start_time'))
    if start_t is None:
        start_t = _parse_time_param(payload.get('time'))
    end_d = _parse_date_param(payload.get('end_date'), None)
    end_t = _parse_time_param(payload.get('end_time'))
    if (start_t is None or start_t == '') and not end_d and not end_t:
        all_day = True
    ok, err = _validate_range(start_d, start_t, end_d, end_t)
    if not ok:
        return jsonify({'ok': False, 'error': err}), 400
    # Recurring support (optional)
    recur = payload.get('recurring')
    if recur and isinstance(recur, dict):
        # New shape: interval+unit; support legacy 'frequency' for compatibility
        interval = recur.get('interval')
        try:
            interval = int(interval)
        except Exception:
            interval = None
        if not interval or interval < 1:
            interval = 1
        unit = (recur.get('unit') or '').lower()
        if unit not in {'day','week','month','year'}:
            # legacy path
            freq = sanitize_text(recur.get('frequency') or 'daily')
            unit = 'day' if freq == 'daily' else ('week' if freq == 'weekly' else 'month')
        end_s = recur.get('end_date'); end_d = _parse_date_param(end_s, None)
        rr = RecurringReminder(title=title, description=description, creator=creator,
                               interval=interval, unit=unit,
                               frequency=None, monthly_mode=None,
                               time=start_t, category=payload.get('category'), color=payload.get('color'),
                               start_date=start_d, end_date=end_d, effective_from=start_d)
        db.session.add(rr)
        db.session.commit()
        return jsonify({'ok': True, 'recurring_id': rr.id})
    r = Reminder(
        date=start_d,
        time=start_t,
        start_date=start_d,
        start_time=start_t,
        end_date=end_d,
        end_time=end_t,
        all_day=all_day,
        title=title,
        description=description,
        creator=creator,
    )
    cat = payload.get('category'); col = payload.get('color')
    if hasattr(r, 'category'):
        r.category = sanitize_text(cat) if cat else None
    if hasattr(r, 'color'):
        r.color = sanitize_text(col) if col else None
    db.session.add(r)
    db.session.commit()
    return jsonify({'ok': True, 'reminder': _serialize_reminder(r)})


@main_bp.route('/api/reminders/<int:rid>', methods=['PATCH'])
def api_reminders_update(rid):
    r = db.get_or_404(Reminder, rid)
    payload = request.get_json(silent=True) or {}
    user = sanitize_text(payload.get('creator', ''))
    if not can_modify(user, r.creator or ''):
        return jsonify({'ok': False, 'error': _('Not allowed')}), 403
    if 'title' in payload:
        title = sanitize_text(payload['title'])
        if title:
            r.title = title
    if 'description' in payload:
        r.description = sanitize_html(payload['description'])
    new_start_d = getattr(r, 'start_date', None) or r.date
    if 'start_date' in payload:
        new_start_d = _parse_date_param(payload.get('start_date'), new_start_d)
    elif 'date' in payload:
        new_start_d = _parse_date_param(payload.get('date'), new_start_d)

    new_start_t = getattr(r, 'start_time', None)
    if new_start_t is None:
        new_start_t = getattr(r, 'time', None)
    if 'start_time' in payload:
        raw_start_t = payload.get('start_time')
        if raw_start_t is None:
            new_start_t = getattr(r, 'start_time', None) or getattr(r, 'time', None)
        else:
            parsed_start_t = _parse_time_param(raw_start_t)
            if parsed_start_t is not None:
                new_start_t = parsed_start_t
    elif 'time' in payload:
        raw_time = payload.get('time')
        if raw_time is None:
            new_start_t = getattr(r, 'start_time', None) or getattr(r, 'time', None)
        else:
            parsed_time = _parse_time_param(raw_time)
            if parsed_time is not None:
                new_start_t = parsed_time

    new_end_d = getattr(r, 'end_date', None)
    if 'end_date' in payload:
        raw_end_d = payload.get('end_date')
        if raw_end_d is None:
            new_end_d = getattr(r, 'end_date', None)
        else:
            parsed_end_d = _parse_date_param(raw_end_d, None)
            if parsed_end_d is not None:
                new_end_d = parsed_end_d
            else:
                new_end_d = getattr(r, 'end_date', None)

    new_end_t = getattr(r, 'end_time', None)
    if 'end_time' in payload:
        raw_end_t = payload.get('end_time')
        if raw_end_t is None:
            new_end_t = getattr(r, 'end_time', None)
        else:
            parsed_end_t = _parse_time_param(raw_end_t)
            if parsed_end_t is not None:
                new_end_t = parsed_end_t
            else:
                new_end_t = getattr(r, 'end_time', None)

    all_day = (new_start_t is None or new_start_t == '') and not new_end_d and not new_end_t

    ok, err = _validate_range(new_start_d, new_start_t, new_end_d, new_end_t)
    if not ok:
        return jsonify({'ok': False, 'error': err}), 400

    r.date = new_start_d
    if hasattr(r, 'time'):
        r.time = new_start_t
    if hasattr(r, 'start_date'):
        r.start_date = new_start_d
    if hasattr(r, 'start_time'):
        r.start_time = new_start_t
    if hasattr(r, 'end_date'):
        r.end_date = new_end_d
    if hasattr(r, 'end_time'):
        r.end_time = new_end_t
    if hasattr(r, 'all_day'):
        r.all_day = bool(all_day)
    if hasattr(r, 'category') and 'category' in payload:
        r.category = sanitize_text(payload.get('category')) if payload.get('category') else None
    if hasattr(r, 'color') and 'color' in payload:
        r.color = sanitize_text(payload.get('color')) if payload.get('color') else None
    db.session.commit()
    return jsonify({'ok': True, 'reminder': _serialize_reminder(r)})


@main_bp.route('/api/reminders/<int:rid>/done', methods=['POST'])
def api_reminder_mark_done(rid):
    r = db.get_or_404(Reminder, rid)
    payload = request.get_json(silent=True) or {}
    user = sanitize_text(payload.get('creator', ''))
    if not can_modify(user, r.creator or ''):
        return jsonify({'ok': False, 'error': _('Not allowed')}), 403
    r.completed_at = utcnow()
    db.session.commit()
    return jsonify({'ok': True})


@main_bp.route('/api/reminders/<int:rid>/undo', methods=['POST'])
def api_reminder_mark_undo(rid):
    r = db.get_or_404(Reminder, rid)
    payload = request.get_json(silent=True) or {}
    user = sanitize_text(payload.get('creator', ''))
    if not can_modify(user, r.creator or ''):
        return jsonify({'ok': False, 'error': _('Not allowed')}), 403
    r.completed_at = None
    db.session.commit()
    return jsonify({'ok': True, 'reminder': _serialize_reminder(r)})


@main_bp.route('/api/reminders/<int:rid>/snooze', methods=['POST'])
def api_reminder_snooze(rid):
    r = db.get_or_404(Reminder, rid)
    payload = request.get_json(silent=True) or {}
    user = sanitize_text(payload.get('creator', ''))
    if not can_modify(user, r.creator or ''):
        return jsonify({'ok': False, 'error': _('Not allowed')}), 403

    minutes = payload.get('minutes')
    days = payload.get('days')
    try:
        minutes = int(minutes) if minutes is not None else 0
    except Exception:
        minutes = 0
    try:
        days = int(days) if days is not None else 0
    except Exception:
        days = 0
    if minutes <= 0 and days <= 0:
        days = 1
    delta = timedelta(days=days, minutes=minutes)

    sd = getattr(r, 'start_date', None) or r.date
    st = getattr(r, 'start_time', None)
    if st is None:
        st = getattr(r, 'time', None)
    ed = getattr(r, 'end_date', None)
    et = getattr(r, 'end_time', None)

    if st:
        sdt = _datetime_from_parts(sd, st)
        ndt = sdt + delta
        r.date = ndt.date()
        r.time = ndt.strftime('%H:%M')
        if hasattr(r, 'start_date'):
            r.start_date = ndt.date()
        if hasattr(r, 'start_time'):
            r.start_time = ndt.strftime('%H:%M')
        if ed:
            edt = _datetime_from_parts(ed, et, is_end=not bool(et))
            nedt = edt + delta
            if hasattr(r, 'end_date'):
                r.end_date = nedt.date()
            if hasattr(r, 'end_time'):
                r.end_time = et and nedt.strftime('%H:%M') or None
    else:
        # Date-only reminder snooze shifts by whole days.
        day_shift = delta.days if delta.days > 0 else 1
        r.date = sd + timedelta(days=day_shift)
        r.time = None
        if hasattr(r, 'start_date'):
            r.start_date = r.date
        if hasattr(r, 'start_time'):
            r.start_time = None
        if ed and hasattr(r, 'end_date'):
            r.end_date = ed + timedelta(days=day_shift)
        if hasattr(r, 'end_time'):
            r.end_time = et

    if hasattr(r, 'all_day'):
        r.all_day = (not getattr(r, 'start_time', None)) and (not getattr(r, 'end_date', None)) and (not getattr(r, 'end_time', None))
    db.session.commit()
    return jsonify({'ok': True, 'reminder': _serialize_reminder(r)})


@main_bp.route('/api/reminders', methods=['DELETE'])
def api_reminders_delete_bulk():
    """Soft-delete: moves reminders to trash (sets deleted_at)."""
    payload = request.get_json(silent=True) or {}
    ids = payload.get('ids') or []
    user = sanitize_text(payload.get('creator', ''))
    if not isinstance(ids, list) or not ids:
        return jsonify({'ok': False, 'error': _('No ids provided')}), 400
    soft_deleted = 0
    dates = set()
    now = utcnow()
    for rid in ids:
        if not isinstance(rid, int):
            continue
        r = db.session.get(Reminder, rid)
        if not r:
            continue
        if can_modify(user, r.creator or ''):
            if r.date:
                dates.add(r.date.strftime('%Y-%m-%d'))
            r.deleted_at = now
            soft_deleted += 1
    if soft_deleted:
        db.session.commit()
    return jsonify({'ok': True, 'deleted': soft_deleted, 'dates': list(dates)})


@main_bp.route('/api/reminders/trash', methods=['GET'])
def api_reminders_trash():
    """List soft-deleted (trashed) reminders, auto-purging entries older than 7 days."""
    cutoff = utcnow() - timedelta(days=7)
    try:
        Reminder.query.filter(Reminder.deleted_at < cutoff).delete(synchronize_session=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
    rows = Reminder.query.filter(Reminder.deleted_at.isnot(None)).order_by(Reminder.deleted_at.desc()).all()
    return jsonify({'ok': True, 'reminders': [_serialize_reminder(r) for r in rows]})


@main_bp.route('/api/reminders/<int:rid>/restore', methods=['POST'])
def api_reminder_restore(rid):
    r = db.get_or_404(Reminder, rid)
    payload = request.get_json(silent=True) or {}
    user = sanitize_text(payload.get('creator', ''))
    if not can_modify(user, r.creator or ''):
        return jsonify({'ok': False, 'error': _('Not allowed')}), 403
    if r.deleted_at is None:
        return jsonify({'ok': False, 'error': _('Reminder is not in trash')}), 400
    r.deleted_at = None
    db.session.commit()
    return jsonify({'ok': True, 'reminder': _serialize_reminder(r)})


@main_bp.route('/api/reminders/<int:rid>/purge', methods=['DELETE'])
def api_reminder_purge(rid):
    """Permanently delete a reminder from trash."""
    r = db.get_or_404(Reminder, rid)
    payload = request.get_json(silent=True) or {}
    user = sanitize_text(payload.get('creator', ''))
    if not can_modify(user, r.creator or ''):
        return jsonify({'ok': False, 'error': _('Not allowed')}), 403
    if r.deleted_at is None:
        return jsonify({'ok': False, 'error': _('Reminder is not in trash')}), 400
    db.session.delete(r)
    db.session.commit()
    return jsonify({'ok': True})


@main_bp.route('/calendar/add', methods=['POST'])
def add_reminder():
    date_s = sanitize_text(request.form.get('date'))
    title = sanitize_text(request.form.get('title'))
    description = sanitize_html(request.form.get('description'))
    creator = sanitize_text(request.form.get('creator'))
    if not (date_s and title):
        flash(_('Date and title are required for reminders.'), 'error')
        return redirect(url_for('main.index'))
    try:
        d = datetime.strptime(date_s, '%Y-%m-%d').date()
    except Exception:
        flash(_('Invalid date.'), 'error')
        return redirect(url_for('main.index'))
    r = Reminder(date=d, title=title, description=description, creator=creator)
    db.session.add(r)
    db.session.commit()
    flash(_('Reminder added.'), 'success')
    return redirect(url_for('main.index', date=date_s))


@main_bp.route('/calendar/delete/<int:reminder_id>', methods=['POST'])
def delete_reminder(reminder_id):
    r = db.get_or_404(Reminder, reminder_id)
    user = sanitize_text(request.form.get('user'))
    if can_modify(user, r.creator):
        r.deleted_at = utcnow()
        db.session.commit()
        flash(_('Reminder moved to trash.'), 'success')
    else:
        flash(_('Not allowed to delete this reminder.'), 'error')
    date_s = None
    try:
        if r.date:
            date_s = r.date.strftime('%Y-%m-%d')
    except Exception:
        date_s = None
    return redirect(url_for('main.index', date=date_s) if date_s else url_for('main.index'))


@main_bp.route('/calendar/delete_bulk', methods=['POST'])
def delete_reminders_bulk():
    ids_raw = sanitize_text(request.form.get('ids', ''))
    user = sanitize_text(request.form.get('user', ''))
    if not ids_raw:
        return redirect(url_for('main.index'))
    id_list = []
    for part in ids_raw.split(','):
        part = part.strip()
        if part.isdigit():
            id_list.append(int(part))
    if not id_list:
        return redirect(url_for('main.index'))
    kept_date = None
    deleted = 0
    now = utcnow()
    for rid in id_list:
        r = db.session.get(Reminder, rid)
        if not r:
            continue
        if kept_date is None and getattr(r, 'date', None):
            try:
                kept_date = r.date.strftime('%Y-%m-%d')
            except Exception:
                kept_date = None
        if can_modify(user, r.creator):
            r.deleted_at = now
            deleted += 1
    if deleted:
        db.session.commit()
        flash(ngettext('Moved %(num)s reminder to trash.', 'Moved %(num)s reminders to trash.', deleted), 'success')
    else:
        flash(_('No reminders deleted (permission?).'), 'error')
    return redirect(url_for('main.index', date=kept_date) if kept_date else url_for('main.index'))


@main_bp.route('/notice', methods=['POST'])
def update_notice():
    content = sanitize_html(request.form.get('content', ''))
    user = sanitize_text(request.form.get('user', ''))
    if not is_admin(user):
        flash(_('Only admin can update the notice.'), 'error')
        return redirect(url_for('main.index'))
    n = Notice.query.order_by(Notice.updated_at.desc()).first()
    now = utcnow()
    if n:
        n.content = content
        n.updated_by = user
        n.updated_at = now
    else:
        db.session.add(Notice(content=content, updated_by=user, updated_at=now))
    db.session.commit()
    flash(_('Notice updated.'), 'success')
    return redirect(url_for('main.index'))


@main_bp.route('/whoishome', methods=['POST'])
def who_is_home_action():
    action = sanitize_text(request.form.get('action', 'update'))
    config = current_app.config['HOMEHUB_CONFIG']
    family = set(config.get('family_members', []))
    name = sanitize_text(request.form.get('name', ''))
    if not name or name not in family:
        if request.headers.get('X-Requested-With') != 'fetch':
            flash(_('Invalid user for status.'), 'error')
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': _('Invalid user')}), 400
        return redirect(url_for('main.index'))
    result = None
    if action == 'clear':
        hs = HomeStatus.query.filter_by(name=name).first()
        if hs:
            db.session.delete(hs)
            db.session.commit()
            result = 'cleared'
            if request.headers.get('X-Requested-With') != 'fetch':
                flash(_('Status cleared.'), 'success')
        else:
            result = 'none'
            if request.headers.get('X-Requested-With') != 'fetch':
                flash(_('No status to clear.'), 'info')
    else:
        status = sanitize_text(request.form.get('status', '')) or 'Away'
        hs = HomeStatus.query.filter_by(name=name).first()
        if hs:
            hs.status = status
        else:
            db.session.add(HomeStatus(name=name, status=status))
        db.session.commit()
        result = 'updated'
        if request.headers.get('X-Requested-With') != 'fetch':
            flash(_('Status updated.'), 'success')
    if request.headers.get('X-Requested-With') == 'fetch':
        who_statuses = {s.name: s.status for s in HomeStatus.query.all() if s.name in family}
        member_statuses = {ms.name: ms.text for ms in MemberStatus.query.all() if ms.name in family and (ms.text or '').strip()}
        result = result or 'updated'
        return jsonify({'ok': True, 'who_statuses': who_statuses, 'member_statuses': member_statuses, 'result': result})
    date_q = request.args.get('date') or request.form.get('date')
    return redirect(url_for('main.index', date=date_q) if date_q else url_for('main.index'))


@main_bp.route('/status/update', methods=['POST'])
def member_status_update():
    config = current_app.config['HOMEHUB_CONFIG']
    family = set(config.get('family_members', []))
    name = sanitize_text(request.form.get('name', ''))
    raw_text = request.form.get('text', '') or ''
    text = sanitize_text(raw_text)
    if not name or name not in family:
        if request.headers.get('X-Requested-With') != 'fetch':
            flash(_('Invalid user for status.'), 'error')
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': _('Invalid user')}), 400
        return redirect(url_for('main.index'))
    if not text:
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'Empty status'}), 400
        else:
            flash(_('Status cannot be empty.'), 'error')
            return redirect(url_for('main.index'))
    ms = MemberStatus.query.filter_by(name=name).first()
    now = utcnow()
    if ms:
        ms.text = text
        ms.updated_at = now
    else:
        db.session.add(MemberStatus(name=name, text=text, updated_at=now))
    db.session.commit()
    if request.headers.get('X-Requested-With') != 'fetch':
        flash(_('Status saved.'), 'success')
    if request.headers.get('X-Requested-With') == 'fetch':
        who_statuses = {s.name: s.status for s in HomeStatus.query.all() if s.name in family}
        member_statuses = {ms.name: ms.text for ms in MemberStatus.query.all() if ms.name in family and (ms.text or '').strip()}
        return jsonify({'ok': True, 'who_statuses': who_statuses, 'member_statuses': member_statuses, 'result': 'saved'})
    return redirect(url_for('main.index'))


@main_bp.route('/status/delete', methods=['POST'])
def member_status_delete():
    config = current_app.config['HOMEHUB_CONFIG']
    family = set(config.get('family_members', []))
    name = sanitize_text(request.form.get('name', ''))
    if not name or name not in family:
        if request.headers.get('X-Requested-With') != 'fetch':
            flash(_('Invalid user for status removal.'), 'error')
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': _('Invalid user')}), 400
        return redirect(url_for('main.index'))
    ms = MemberStatus.query.filter_by(name=name).first()
    removed = False
    if ms:
        db.session.delete(ms)
        db.session.commit()
        removed = True
        if request.headers.get('X-Requested-With') != 'fetch':
            flash(_('Status removed.'), 'success')
    if request.headers.get('X-Requested-With') == 'fetch':
        who_statuses = {s.name: s.status for s in HomeStatus.query.all() if s.name in family}
        member_statuses = {ms.name: ms.text for ms in MemberStatus.query.all() if ms.name in family and (ms.text or '').strip()}
        return jsonify({'ok': True, 'who_statuses': who_statuses, 'member_statuses': member_statuses, 'result': 'removed' if removed else 'none'})
    return redirect(url_for('main.index'))
