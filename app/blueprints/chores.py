from flask import render_template, request, redirect, url_for, current_app, jsonify, flash, session
from datetime import datetime, date, timedelta
from ..models import db, Chore, RecurringChore
from ..blueprints import main_bp
from ..security import sanitize_text
import json


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except Exception:
        return None


def _add_months(dt: date, months: int) -> date:
    y = dt.year + (dt.month - 1 + months) // 12
    m = (dt.month - 1 + months) % 12 + 1
    last = (date(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1) - timedelta(days=1)).day
    d = min(dt.day, last)
    return date(y, m, d)


def _add_years(dt: date, years: int) -> date:
    try:
        return date(dt.year + years, dt.month, dt.day)
    except Exception:
        if dt.month == 2 and dt.day == 29:
            return date(dt.year + years, 2, 28)
        return _add_months(dt, years * 12)


def _next_occurrence(rule: RecurringChore, d: date) -> date:
    interval = max(1, int(getattr(rule, 'interval', 1) or 1))
    unit = (getattr(rule, 'unit', 'day') or 'day').lower()
    if unit == 'day':
        return d + timedelta(days=interval)
    if unit == 'week':
        return d + timedelta(weeks=interval)
    if unit == 'month':
        return _add_months(d, interval)
    if unit == 'year':
        return _add_years(d, interval)
    return d + timedelta(days=interval)


def _next_due_on_or_after(rule: RecurringChore, target: date) -> date | None:
    d = rule.start_date or target
    if rule.end_date and d > rule.end_date:
        return None
    if d >= target:
        return d
    while d < target:
        nd = _next_occurrence(rule, d)
        if nd == d:
            break
        d = nd
        if rule.end_date and d > rule.end_date:
            return None
    return d if (not rule.end_date or d <= rule.end_date) else None


def _ensure_current_recurring_chores(today: date | None = None):
    today = today or date.today()
    rules = RecurringChore.query.all()
    active_rows = (
        Chore.query
        .filter(Chore.recurring_id.isnot(None))
        .order_by(Chore.recurring_id.asc(), Chore.due_date.asc(), Chore.timestamp.desc())
        .all()
    )
    active_by_rule: dict[int, Chore] = {}
    for row in active_rows:
        if row.recurring_id is None or row.recurring_id in active_by_rule:
            continue
        active_by_rule[row.recurring_id] = row
    changed = False
    for rule in rules:
        next_due = _next_due_on_or_after(rule, today)
        active = active_by_rule.get(rule.id)
        if next_due is None:
            if active and not active.done:
                active.done = True
                changed = True
            continue
        if active:
            if active.description != rule.description:
                active.description = rule.description
                changed = True
            if active.creator != rule.creator:
                active.creator = rule.creator
                changed = True
            rule_tags = rule.tags or '[]'
            if active.tags != rule_tags:
                active.tags = rule_tags
                changed = True
            if active.due_date != next_due:
                active.due_date = next_due
                changed = True
            if active.done:
                active.done = False
                changed = True
        else:
            db.session.add(Chore(
                description=rule.description,
                creator=rule.creator,
                tags=rule.tags or '[]',
                due_date=next_due,
                recurring_id=rule.id,
                done=False,
            ))
            changed = True
        if rule.last_generated_date != next_due:
            rule.last_generated_date = next_due
            changed = True
    if changed:
        db.session.commit()


def _admin_aliases() -> set[str]:
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    return {admin_name, 'Administrator', 'admin'}


def _request_user() -> str:
    return sanitize_text(request.form.get('user', ''))


def _ensure_app_setting_table():
    db.session.execute(db.text("CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY, value TEXT)"))
    db.session.commit()


def _get_show_chores_on_homepage() -> bool:
    try:
        _ensure_app_setting_table()
        row = db.session.execute(db.text("SELECT value FROM app_setting WHERE key='show_chores_on_homepage'"))
        value = row.scalar()
        if value is None:
            return bool((current_app.config.get('HOMEHUB_CONFIG', {}).get('feature_toggles') or {}).get('show_chores_on_homepage', False))
        return str(value).strip().lower() in ('1', 'true', 'yes', 'on')
    except Exception:
        return False


def _set_show_chores_on_homepage(enabled: bool):
    _ensure_app_setting_table()
    db.session.execute(
        db.text("INSERT INTO app_setting(key,value) VALUES('show_chores_on_homepage', :v) ON CONFLICT(key) DO UPDATE SET value=excluded.value"),
        {'v': '1' if enabled else '0'}
    )
    db.session.commit()


def _render_chores_page(**form_state):
    _ensure_current_recurring_chores(date.today())
    filter_tags = request.args.get('tags')
    chores = Chore.query.order_by(Chore.done.asc(), Chore.due_date.asc(), Chore.timestamp.desc()).all()
    if filter_tags:
        try:
            selected = json.loads(filter_tags)
            if isinstance(selected, list) and selected:
                def match(item_tags):
                    try:
                        arr = json.loads(item_tags or '[]')
                    except Exception:
                        arr = []
                    return any(t in arr for t in selected)
                chores = [i for i in chores if match(i.tags)]
        except Exception:
            pass
    show_chores_on_homepage = _get_show_chores_on_homepage()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template(
        'chores.html',
        chores=chores,
        show_chores_on_homepage=show_chores_on_homepage,
        today_date=date.today(),
        **form_state,
        config=config,
    )


@main_bp.route('/chores', methods=['GET', 'POST'])
def chores():
    if request.method == 'POST':
        chore_id = request.form.get('chore_id')
        recurring_rule_id = request.form.get('recurring_rule_id')
        description = sanitize_text(request.form['description'])
        creator = sanitize_text(request.form['creator'])
        user = _request_user()
        admin_aliases = _admin_aliases()
        raw_tags = request.form.get('tags', '').strip()
        tags_list = []
        if raw_tags:
            try:
                tags_list = json.loads(raw_tags)
                if not isinstance(tags_list, list):
                    tags_list = []
            except Exception:
                tags_list = [t.strip() for t in raw_tags.split(',') if t.strip()]
        tags_list = [sanitize_text(t) for t in tags_list if isinstance(t, str) and t.strip()]
        is_recurring = request.form.get('is_recurring') in ('1', 'on', 'true', 'yes')
        if is_recurring:
            try:
                interval = max(1, int(request.form.get('rec_interval') or 1))
            except Exception:
                interval = 1
            unit = sanitize_text(request.form.get('rec_unit', 'day')).lower()
            if unit not in ('day', 'week', 'month', 'year'):
                unit = 'day'
            start_date = _parse_date(request.form.get('rec_start_date')) or date.today()
            end_date = _parse_date(request.form.get('rec_end_date'))
            if end_date and end_date < start_date:
                flash('Recurring chore end date cannot be before the start date.', 'error')
                return _render_chores_page(
                    form_description=description,
                    form_tags=json.dumps(tags_list),
                    form_is_recurring=True,
                    form_rec_interval=interval,
                    form_rec_unit=unit,
                    form_rec_start_date=start_date,
                    form_rec_end_date=end_date,
                    form_chore_id=chore_id or '',
                    form_recurring_rule_id=recurring_rule_id or '',
                )
            if end_date and end_date < date.today():
                flash('Recurring chore ends in the past. Choose a future end date to create an active chore.', 'error')
                return _render_chores_page(
                    form_description=description,
                    form_tags=json.dumps(tags_list),
                    form_is_recurring=True,
                    form_rec_interval=interval,
                    form_rec_unit=unit,
                    form_rec_start_date=start_date,
                    form_rec_end_date=end_date,
                    form_chore_id=chore_id or '',
                    form_recurring_rule_id=recurring_rule_id or '',
                )
            if recurring_rule_id:
                rule = RecurringChore.query.get_or_404(int(recurring_rule_id))
                if not (user in admin_aliases or user == (rule.creator or '')):
                    flash('Not allowed to update recurring rule.', 'error')
                    return redirect(url_for('main.chores'))
                rule.description = description
                rule.tags = json.dumps(tags_list)
                rule.interval = interval
                rule.unit = unit
                rule.start_date = start_date
                rule.end_date = end_date
                next_due = _next_due_on_or_after(rule, date.today())
                active = Chore.query.filter_by(recurring_id=rule.id).order_by(Chore.timestamp.desc()).first()
                if active and next_due is not None:
                    active.description = description
                    active.creator = rule.creator
                    active.tags = json.dumps(tags_list)
                    active.due_date = next_due
                    active.done = False
                rule.last_generated_date = next_due
                db.session.commit()
                flash('Recurring chore updated.', 'success')
            else:
                rule = RecurringChore(
                    description=description,
                    creator=creator,
                    tags=json.dumps(tags_list),
                    interval=interval,
                    unit=unit,
                    start_date=start_date,
                    end_date=end_date,
                )
                db.session.add(rule)
                db.session.commit()
                next_due = _next_due_on_or_after(rule, date.today())
                if next_due is not None:
                    db.session.add(Chore(
                        description=description,
                        creator=creator,
                        tags=json.dumps(tags_list),
                        due_date=next_due,
                        recurring_id=rule.id,
                        done=False,
                    ))
                    rule.last_generated_date = next_due
                    db.session.commit()
                flash('Recurring chore added.', 'success')
        else:
            if recurring_rule_id:
                rule = RecurringChore.query.get_or_404(int(recurring_rule_id))
                if not (user in admin_aliases or user == (rule.creator or '')):
                    flash('Not allowed to delete recurring rule.', 'error')
                    return redirect(url_for('main.chores'))
                Chore.query.filter_by(recurring_id=rule.id).delete()
                db.session.delete(rule)
                db.session.commit()
            if chore_id:
                chore = Chore.query.get_or_404(int(chore_id))
                if not (user in admin_aliases or user == (chore.creator or '')):
                    flash('Not allowed to update chore.', 'error')
                    return redirect(url_for('main.chores'))
                chore.description = description
                chore.tags = json.dumps(tags_list)
                db.session.commit()
                flash('Chore updated.', 'success')
            else:
                chore = Chore(description=description, creator=creator, tags=json.dumps(tags_list))
                db.session.add(chore)
                db.session.commit()
                flash('Chore added.', 'success')
        return redirect(url_for('main.chores'))
    return _render_chores_page()


@main_bp.route('/chores/edit/<int:chore_id>')
def edit_chore(chore_id):
    chore = Chore.query.get_or_404(chore_id)
    form_state = {
        'form_description': chore.description,
        'form_tags': chore.tags or '[]',
        'form_is_recurring': False,
        'form_rec_interval': 1,
        'form_rec_unit': 'day',
        'form_rec_start_date': None,
        'form_rec_end_date': None,
        'form_chore_id': chore.id,
        'form_recurring_rule_id': '',
    }
    if chore.recurring_id:
        rule = RecurringChore.query.get(chore.recurring_id)
        if rule:
            form_state.update({
                'form_description': rule.description,
                'form_tags': rule.tags or '[]',
                'form_is_recurring': True,
                'form_rec_interval': rule.interval or 1,
                'form_rec_unit': rule.unit or 'day',
                'form_rec_start_date': rule.start_date,
                'form_rec_end_date': rule.end_date,
                'form_recurring_rule_id': rule.id,
            })
    return _render_chores_page(**form_state)


@main_bp.route('/chores/settings', methods=['POST'])
def chores_settings():
    if current_app.config['HOMEHUB_CONFIG'].get('password_hash') and not session.get('authed'):
        flash('Only admin can update chore settings.', 'error')
        return redirect(url_for('main.chores'))
    enabled = request.form.get('show_chores_on_homepage') in ('1', 'on', 'true', 'yes')
    _set_show_chores_on_homepage(enabled)
    flash('Chore settings updated.', 'success')
    return redirect(url_for('main.chores'))


@main_bp.route('/chores/recurring/delete/<int:rule_id>', methods=['POST'])
def delete_recurring_chore(rule_id):
    rule = RecurringChore.query.get_or_404(rule_id)
    user = _request_user()
    admin_aliases = _admin_aliases()
    if not (user in admin_aliases or user == (rule.creator or '')):
        flash('Not allowed to delete recurring rule.', 'error')
        return redirect(url_for('main.chores'))
    Chore.query.filter_by(recurring_id=rule.id).delete()
    db.session.delete(rule)
    db.session.commit()
    flash('Recurring chore rule deleted.', 'success')
    return redirect(url_for('main.chores'))


@main_bp.route('/chores/toggle/<int:chore_id>', methods=['POST'])
def toggle_chore(chore_id):
    chore = Chore.query.get_or_404(chore_id)
    if getattr(chore, 'recurring_id', None):
        rule = RecurringChore.query.get(getattr(chore, 'recurring_id', None))
        if rule and chore.due_date:
            next_due = _next_occurrence(rule, chore.due_date)
            if rule.end_date and next_due > rule.end_date:
                chore.done = True
            else:
                chore.due_date = next_due
                chore.done = False
                rule.last_generated_date = next_due
        else:
            chore.done = not getattr(chore, 'done', False)
    else:
        chore.done = not getattr(chore, 'done', False)
    db.session.commit()
    return redirect(url_for('main.chores'))


@main_bp.route('/chores/delete/<int:chore_id>', methods=['POST'])
def delete_chore(chore_id):
    chore = Chore.query.get_or_404(chore_id)
    user = sanitize_text(request.form.get('user', ''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if getattr(chore, 'recurring_id', None):
        rule = RecurringChore.query.get(getattr(chore, 'recurring_id', None))
        rule_creator = (rule.creator if rule else chore.creator) or ''
        if user in admin_aliases or user == rule_creator:
            if rule:
                Chore.query.filter_by(recurring_id=rule.id).delete()
                db.session.delete(rule)
                flash('Recurring chore rule deleted.', 'success')
            else:
                db.session.delete(chore)
            db.session.commit()
        else:
            flash('Not allowed to delete recurring rule.', 'error')
        return redirect(url_for('main.chores'))
    if user in admin_aliases or user == chore.creator:
        db.session.delete(chore)
        db.session.commit()
        flash('Chore deleted.', 'success')
    else:
        flash('Not allowed to delete chore.', 'error')
    return redirect(url_for('main.chores'))


@main_bp.route('/api/chores/<int:chore_id>/tags', methods=['POST'])
def update_chore_tags(chore_id):
    chore = Chore.query.get_or_404(chore_id)
    try:
        data = request.get_json(force=True) or {}
        tags = data.get('tags', [])
        if not isinstance(tags, list):
            tags = []
        cleaned = []
        for t in tags:
            if isinstance(t, str):
                cleaned.append(sanitize_text(t))
        chore.tags = json.dumps(cleaned)
        db.session.commit()
        return jsonify({"ok": True, "id": chore.id, "tags": cleaned})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@main_bp.route('/api/chores', methods=['GET'])
def api_get_chores():
    _ensure_current_recurring_chores(date.today())
    tags = request.args.get('tags')
    items = Chore.query.order_by(Chore.done.asc(), Chore.due_date.desc(), Chore.timestamp.desc()).all()
    if tags:
        try:
            sel = json.loads(tags)
            if isinstance(sel, list) and sel:
                def match(item_tags):
                    try:
                        arr = json.loads(item_tags or '[]')
                    except Exception:
                        arr = []
                    return any(t in arr for t in sel)
                items = [i for i in items if match(i.tags)]
        except Exception:
            pass
    def to_dict(i):
        try:
            tg = json.loads(i.tags or '[]')
        except Exception:
            tg = []
        return {
            "id": i.id,
            "description": i.description,
            "done": i.done,
            "creator": i.creator,
            "timestamp": i.timestamp.isoformat(),
            "due_date": i.due_date.strftime('%Y-%m-%d') if i.due_date else None,
            "recurring_id": i.recurring_id,
            "tags": tg,
        }
    return jsonify([to_dict(i) for i in items])


@main_bp.route('/api/chores/<int:chore_id>', methods=['PUT'])
def api_update_chore(chore_id):
    chore = Chore.query.get_or_404(chore_id)
    try:
        data = request.get_json(force=True) or {}
        desc = data.get('description')
        raw_tags = data.get('tags', [])
        if isinstance(desc, str):
            chore.description = sanitize_text(desc)
        tags = []
        if isinstance(raw_tags, list):
            for t in raw_tags:
                if isinstance(t, str):
                    tags.append(sanitize_text(t))
        chore.tags = json.dumps(tags)
        db.session.commit()
        return jsonify({"ok": True, "item": {"id": chore.id, "description": chore.description, "tags": tags}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
