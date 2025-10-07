from flask import Blueprint, render_template, request, redirect, url_for, flash, send_from_directory, jsonify, current_app, session
from .config import load_config
from threading import Thread
from .models import db, Note, File, Media, PDF, ShoppingItem, GroceryHistory, HomeStatus, Chore, Recipe, ExpiryItem, ShortURL, QRCode, Notice, Reminder, MemberStatus, RecurringExpense, ExpenseEntry
from .utils import generate_short_code
import os
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta
import calendar as _calendar
import bleach
import json

main_bp = Blueprint('main', __name__)

ALLOWED_TAGS = ['b', 'i', 'u', 'a']
ALLOWED_ATTRIBUTES = {'a': ['href', 'title']}

@main_bp.before_app_request
def reload_config_and_auth():
    # Always reload config to reflect changes to config.yml without rebuilding
    try:
        current_app.config['HOMEHUB_CONFIG'] = load_config()
    except Exception:
        pass
    cfg = current_app.config.get('HOMEHUB_CONFIG', {})
    # Enforce password if a password is configured
    endpoint = request.endpoint or ''
    if cfg.get('password_hash'):
        if not session.get('authed') and not endpoint.startswith('static') and endpoint not in ('main.login',):
            return redirect(url_for('main.login'))
    else:
        # If no password is configured, /login should redirect to home
        if endpoint == 'main.login':
            return redirect(url_for('main.index'))

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
MEDIA_FOLDER = os.path.join(BASE_DIR, 'media')
PDF_FOLDER = os.path.join(BASE_DIR, 'pdfs')

@main_bp.route('/')
def index():
    config = current_app.config['HOMEHUB_CONFIG']
    notice = Notice.query.order_by(Notice.updated_at.desc()).first()
    # Calendar: gather reminders grouped by date
    # Use with_entities to avoid passing ORM models around accidentally
    try:
        # Include time and category so initial month cache has full data
        rows = Reminder.query.with_entities(
            Reminder.id,
            Reminder.title,
            Reminder.description,
            Reminder.creator,
            Reminder.date,
            Reminder.time,
            Reminder.category
        ).all()
    except Exception:
        rows = []
    by_date = {}
    for rid, title, description, creator, rdate, rtime, rcat in rows:
        try:
            key = rdate.strftime('%Y-%m-%d')
        except Exception:
            # Fallback if rdate is already a string or None
            key = str(rdate) if rdate else ''
        by_date.setdefault(key, []).append({
            'id': int(rid),
            'title': title or '',
            'description': description or '',
            'creator': creator or '',
            'time': rtime or None,
            'category': rcat or None,
        })
    # Serialize once server-side to avoid Jinja tojson on ORM-related objects
    import json
    try:
        reminders_json = json.dumps(by_date)
    except Exception:
        reminders_json = '{}'
    # Who is Home summary
    family = list(dict.fromkeys(config.get('family_members', [])))
    who_statuses = {s.name: s.status for s in HomeStatus.query.all() if s.name in family}
    member_statuses = {ms.name: ms.text for ms in MemberStatus.query.all() if ms.name in family and (ms.text or '').strip()}
    # Extract reminder categories (config structure: reminders: { categories: [ {key,label,color}, ... ] })
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
                    'color': entry.get('color') or None
                })
    except Exception:
        reminder_categories = []
    return render_template('index.html', config=config, notice=notice, reminders_json=reminders_json, who_statuses=who_statuses, member_statuses=member_statuses, reminder_categories=reminder_categories)

# ---------------------- API (Phase 2) Reminders ----------------------

def serialize_reminder(r: Reminder):
    return {
        'id': r.id,
        'date': r.date.strftime('%Y-%m-%d') if r.date else None,
        'time': getattr(r, 'time', None) or None,
        'title': r.title,
        'description': r.description or '',
        'creator': r.creator or '',
        'category': getattr(r, 'category', None),
        'color': getattr(r, 'color', None),
        'timestamp': r.timestamp.isoformat() if r.timestamp else None,
        'updated_at': getattr(r, 'updated_at', None).isoformat() if getattr(r, 'updated_at', None) else None,
    }

def parse_date_param(value, default=None):
    if not value:
        return default
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except Exception:
        return default

@main_bp.route('/api/reminders')
def api_reminders_list():
    """List reminders by scope (day|week|month). Default day of supplied date or today."""
    scope = request.args.get('scope', 'day').lower()
    date_s = request.args.get('date')
    base_date = parse_date_param(date_s, date.today())
    q = Reminder.query
    # For month scope fetch whole month
    if scope == 'month':
        start = base_date.replace(day=1)
        # naive month end
        if start.month == 12:
            next_month = start.replace(year=start.year+1, month=1, day=1)
        else:
            next_month = start.replace(month=start.month+1, day=1)
        end = next_month - timedelta(days=1)
        q = q.filter(Reminder.date >= start, Reminder.date <= end)
    elif scope == 'week':
        # ISO week start (Monday)
        start = base_date - timedelta(days=base_date.weekday())
        end = start + timedelta(days=6)
        q = q.filter(Reminder.date >= start, Reminder.date <= end)
    else:  # day
        q = q.filter(Reminder.date == base_date)
    # Order by date then time (placing NULL/blank times last) then id
    try:
        from sqlalchemy import case
        rows = q.order_by(
            Reminder.date.asc(),
            case((Reminder.time.is_(None), 1), (Reminder.time == '', 1), else_=0).asc(),  # noqa: E711
            Reminder.time.asc(),
            Reminder.id.asc()
        ).all()
    except Exception:
        rows = q.order_by(Reminder.date.asc(), Reminder.id.asc()).all()
    data = [serialize_reminder(r) for r in rows]
    # Aggregates for month scope (counts per day + per-category counts)
    counts = {}
    categories_counts = {}
    if scope == 'month':
        for r in rows:
            k = r.date.strftime('%Y-%m-%d')
            counts[k] = counts.get(k, 0) + 1
            cat = getattr(r, 'category', None) or '_uncategorized'
            if k not in categories_counts:
                categories_counts[k] = {}
            categories_counts[k][cat] = categories_counts[k].get(cat, 0) + 1
    return jsonify({
        'ok': True,
        'scope': scope,
        'date': base_date.strftime('%Y-%m-%d'),
        'reminders': data,
        'counts': counts,
        'categories_counts': categories_counts
    })

@main_bp.route('/api/reminders', methods=['POST'])
def api_reminders_create():
    payload = request.get_json(silent=True) or {}
    title = bleach.clean(payload.get('title', '')).strip()
    date_s = payload.get('date')
    creator = bleach.clean(payload.get('creator', '')).strip()
    description = bleach.clean(payload.get('description', ''), tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
    if not title:
        return jsonify({'ok': False, 'error': 'Title required'}), 400
    d = parse_date_param(date_s, None)
    if not d:
        return jsonify({'ok': False, 'error': 'Invalid date'}), 400
    time_raw = payload.get('time')
    tval = None
    if isinstance(time_raw, str) and len(time_raw) == 5 and time_raw[2] == ':':
        hh, mm = time_raw.split(':', 1)
        if hh.isdigit() and mm.isdigit():
            hhi, mmi = int(hh), int(mm)
            if 0 <= hhi < 24 and 0 <= mmi < 60:
                tval = f"{hhi:02d}:{mmi:02d}"
    r = Reminder(date=d, title=title, description=description, creator=creator, time=tval)
    # Optional future fields (category/color) accepted but ignored if not configured yet
    cat = payload.get('category'); col = payload.get('color')
    if hasattr(r, 'category'):
        r.category = bleach.clean(cat) if cat else None
    if hasattr(r, 'color'):
        r.color = bleach.clean(col) if col else None
    db.session.add(r)
    db.session.commit()
    return jsonify({'ok': True, 'reminder': serialize_reminder(r)})

@main_bp.route('/api/reminders/<int:rid>', methods=['PATCH'])
def api_reminders_update(rid):
    r = Reminder.query.get_or_404(rid)
    payload = request.get_json(silent=True) or {}
    user = bleach.clean(payload.get('creator', ''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user not in admin_aliases and user != (r.creator or ''):
        return jsonify({'ok': False, 'error': 'Not allowed'}), 403
    if 'title' in payload:
        title = bleach.clean(payload['title']).strip()
        if title:
            r.title = title
    if 'description' in payload:
        r.description = bleach.clean(payload['description'], tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
    if 'date' in payload:
        nd = parse_date_param(payload['date'], None)
        if nd:
            r.date = nd
    if hasattr(r, 'time') and 'time' in payload:
        time_raw = payload.get('time')
        if isinstance(time_raw, str) and len(time_raw) == 5 and time_raw[2] == ':':
            hh, mm = time_raw.split(':', 1)
            if hh.isdigit() and mm.isdigit():
                hhi, mmi = int(hh), int(mm)
                if 0 <= hhi < 24 and 0 <= mmi < 60:
                    r.time = f"{hhi:02d}:{mmi:02d}"
    if hasattr(r, 'category') and 'category' in payload:
        r.category = bleach.clean(payload.get('category')) if payload.get('category') else None
    if hasattr(r, 'color') and 'color' in payload:
        r.color = bleach.clean(payload.get('color')) if payload.get('color') else None
    db.session.commit()
    return jsonify({'ok': True, 'reminder': serialize_reminder(r)})

@main_bp.route('/api/reminders', methods=['DELETE'])
def api_reminders_delete_bulk():
    payload = request.get_json(silent=True) or {}
    ids = payload.get('ids') or []
    user = bleach.clean(payload.get('creator', ''))
    if not isinstance(ids, list) or not ids:
        return jsonify({'ok': False, 'error': 'No ids provided'}), 400
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    deleted = 0
    dates = set()
    for rid in ids:
        if not isinstance(rid, int):
            continue
        r = Reminder.query.get(rid)
        if not r:
            continue
        if user in admin_aliases or user == (r.creator or ''):
            if r.date:
                dates.add(r.date.strftime('%Y-%m-%d'))
            db.session.delete(r)
            deleted += 1
    if deleted:
        db.session.commit()
    return jsonify({'ok': True, 'deleted': deleted, 'dates': list(dates)})

@main_bp.route('/login', methods=['GET', 'POST'])
def login():
    config = current_app.config['HOMEHUB_CONFIG']
    # If no password configured, redirect to home
    if not config.get('password_hash'):
        return redirect(url_for('main.index'))
    if request.method == 'POST':
        supplied = bleach.clean(request.form.get('password', ''))
        import hashlib
        if hashlib.sha256(supplied.encode()).hexdigest() == config.get('password_hash'):
            session['authed'] = True
            flash('Logged in successfully.', 'success')
            return redirect(url_for('main.index'))
        flash('Invalid password', 'error')
    return render_template('login.html', config=config, hide_user_ui=True)

@main_bp.route('/logout')
def logout():
    session.pop('authed', None)
    flash('Logged out.', 'info')
    return redirect(url_for('main.login'))

# Shared Notes
@main_bp.route('/notes', methods=['GET', 'POST'])
def notes():
    if request.method == 'POST':
        note_id = request.form.get('note_id')
        content = bleach.clean(request.form['content'], tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
        creator = bleach.clean(request.form['creator'])
        if note_id:
            n = Note.query.get_or_404(int(note_id))
            # allow edit by admin or creator
            admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
            admin_aliases = {admin_name, 'Administrator', 'admin'}
            if creator in admin_aliases or creator == n.creator:
                n.content = content
                db.session.commit()
        else:
            note = Note(content=content, creator=creator)
            db.session.add(note)
            db.session.commit()
        return redirect(url_for('main.notes'))
    notes = Note.query.order_by(Note.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('notes.html', notes=notes, config=config)

@main_bp.route('/notes/delete/<int:note_id>', methods=['POST'])
def delete_note(note_id):
    note = Note.query.get_or_404(note_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == note.creator:
        db.session.delete(note)
        db.session.commit()
    return redirect(url_for('main.notes'))

# File Uploader
@main_bp.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        files = request.files.getlist('files') or ([request.files['file']] if 'file' in request.files else [])
        creator = bleach.clean(request.form['creator'])
        for file in files:
            if not file or not getattr(file, 'filename', ''):
                continue
            filename = secure_filename(file.filename)
            file.save(os.path.join(UPLOAD_FOLDER, filename))
            db_file = File(filename=filename, creator=creator)
            db.session.add(db_file)
        db.session.commit()
        return redirect(url_for('main.upload'))
    files = File.query.order_by(File.upload_time.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('upload.html', files=files, config=config)

@main_bp.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@main_bp.route('/upload/delete/<int:file_id>', methods=['POST'])
def delete_file(file_id):
    db_file = File.query.get_or_404(file_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == db_file.creator:
        try:
            os.remove(os.path.join(UPLOAD_FOLDER, db_file.filename))
        except Exception:
            pass
        db.session.delete(db_file)
        db.session.commit()
    return redirect(url_for('main.upload'))

# Shopping List
@main_bp.route('/shopping', methods=['GET', 'POST'])
def shopping():
    if request.method == 'POST':
        item = bleach.clean(request.form['item'])
        creator = bleach.clean(request.form['creator'])
        shopping_item = ShoppingItem(item=item, creator=creator)
        db.session.add(shopping_item)
        # Log to grocery history for suggestions
        db.session.add(GroceryHistory(item=item, creator=creator))
        db.session.commit()
        return redirect(url_for('main.shopping'))
    items = ShoppingItem.query.order_by(ShoppingItem.timestamp.desc()).all()
    # Suggestion logic: top 10 most frequent items in last 90 days not already on list
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=90)
    existing = {i.item.lower() for i in items}
    rows = db.session.execute(db.text("""
        SELECT item, COUNT(*) as cnt
        FROM grocery_history
        WHERE timestamp >= :cutoff
        GROUP BY item
        ORDER BY cnt DESC
        LIMIT 20
    """), {"cutoff": cutoff}).fetchall()
    suggestions = [r[0] for r in rows if r[0].lower() not in existing][:10]
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('shopping.html', items=items, suggestions=suggestions, config=config)

# Expense Tracker
@main_bp.route('/expenses', methods=['GET', 'POST'])
def expenses():
    # Generate recurring entries up to today
    today = date.today()
    recs = RecurringExpense.query.all()
    for r in recs:
        # Determine next date to generate
        start = r.start_date or today
        # For monthly same-day mode, base day should be from start_date to preserve intent
        base_day = (r.start_date or today).day
        last = r.last_generated_date  # may be None
        # Iterate dates based on frequency
        def next_date(d):
            if r.frequency == 'daily':
                return d + timedelta(days=1)
            if r.frequency == 'weekly':
                return d + timedelta(weeks=1)
            # monthly: honor monthly_mode
            mode = getattr(r, 'monthly_mode', 'day_of_month') or 'day_of_month'
            if mode == 'calendar':
                # jump to first day of next month
                ny = d.year + (1 if d.month == 12 else 0)
                nm = 1 if d.month == 12 else d.month + 1
                return date(ny, nm, 1)
            else:
                # same day-of-month next month (clamped to last day)
                ny = d.year + (1 if d.month == 12 else 0)
                nm = 1 if d.month == 12 else d.month + 1
                last_dom = _calendar.monthrange(ny, nm)[1]
                day = min(base_day, last_dom)
                return date(ny, nm, day)
        # Seed generation correctly for each frequency
        if last is None or (last and last < start):
            # Treat as fresh generation starting from start date
            if r.frequency == 'daily':
                d = start
            elif r.frequency == 'weekly':
                d = start
            else:  # monthly
                mode = getattr(r, 'monthly_mode', 'day_of_month') or 'day_of_month'
                if mode == 'calendar':
                    # If start is on the 1st, include it; otherwise begin on first day of next month
                    if start.day == 1:
                        d = start
                    else:
                        ny = start.year + (1 if start.month == 12 else 0)
                        nm = 1 if start.month == 12 else start.month + 1
                        d = date(ny, nm, 1)
                else:
                    d = start
        else:
            # Continue from last generated date
            d = next_date(last)
        while d <= today and (not r.end_date or d <= r.end_date):
            # only create if not already present
            exists = ExpenseEntry.query.filter_by(date=d, recurring_id=r.id).first()
            if not exists:
                qty = r.default_quantity or 1.0
                amt = (r.unit_price or 0.0) * qty
                db.session.add(ExpenseEntry(date=d, title=r.title, category=getattr(r, 'category', None), unit_price=r.unit_price, quantity=qty, amount=amt, payer=r.creator, recurring_id=r.id))
            r.last_generated_date = d
            d = next_date(d)
    db.session.commit()

    # Handle add entry
    if request.method == 'POST':
        # Two forms: new entry or new recurring
        form_type = request.form.get('form_type')
        if form_type == 'recurring':
            title = bleach.clean(request.form.get('title',''))
            unit_price = float(request.form.get('unit_price') or 0)
            default_quantity = float(request.form.get('default_quantity') or 1)
            frequency = bleach.clean(request.form.get('frequency','daily'))
            monthly_mode = bleach.clean(request.form.get('monthly_mode','day_of_month'))
            category = bleach.clean(request.form.get('category',''))
            start_date = request.form.get('start_date')
            end_date = request.form.get('end_date')
            creator = bleach.clean(request.form.get('creator',''))
            sd = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else date.today()
            ed = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else None
            db.session.add(RecurringExpense(title=title, unit_price=unit_price, default_quantity=default_quantity, frequency=frequency, monthly_mode=monthly_mode, category=category, start_date=sd, end_date=ed, creator=creator))
            db.session.commit()
            flash('Recurring expense added.', 'success')
            # Preserve view state if provided
            y = request.args.get('y') or today.year
            m = request.args.get('m') or today.month
            sel = request.args.get('sel')
            return redirect(url_for('main.expenses', y=y, m=m, sel=sel))
        else:
            title = bleach.clean(request.form.get('title',''))
            amount = float(request.form.get('amount') or 0)
            category = bleach.clean(request.form.get('category') or '')
            payer = bleach.clean(request.form.get('payer') or '')
            date_s = request.form.get('date')
            d = datetime.strptime(date_s, '%Y-%m-%d').date() if date_s else date.today()
            unit_price = request.form.get('unit_price'); quantity = request.form.get('quantity')
            up = float(unit_price) if unit_price else None
            q = float(quantity) if quantity else None
            db.session.add(ExpenseEntry(date=d, title=title, category=category, unit_price=up, quantity=q, amount=amount, payer=payer))
            db.session.commit()
            flash('Expense added.', 'success')
            # Preserve view state if provided, else default to the added date
            y = request.args.get('y') or d.year
            m = request.args.get('m') or d.month
            sel = request.args.get('sel') or d.strftime('%Y-%m-%d')
            return redirect(url_for('main.expenses', y=y, m=m, sel=sel))

    # Compute month to show (defaults to current month, allow query params)
    try:
        y = int(request.args.get('y') or today.year)
        m = int(request.args.get('m') or today.month)
    except Exception:
        y, m = today.year, today.month
    month_start = date(y, m, 1)
    last_day = _calendar.monthrange(y, m)[1]
    month_end = date(y, m, last_day)

    q_entries = ExpenseEntry.query.filter(ExpenseEntry.date >= month_start, ExpenseEntry.date <= month_end).order_by(ExpenseEntry.date.asc(), ExpenseEntry.timestamp.asc()).all()
    # Prepare data structure for client rendering
    by_date = {}
    total = 0.0
    per_payer = {}
    per_category = {}
    for e in q_entries:
        ds = e.date.strftime('%Y-%m-%d')
        by_date.setdefault(ds, {'total': 0.0, 'entries': []})
        by_date[ds]['total'] += float(e.amount or 0)
        total += float(e.amount or 0)
        per_payer[e.payer or ''] = per_payer.get(e.payer or '', 0.0) + float(e.amount or 0)
        if e.category:
            per_category[e.category] = per_category.get(e.category, 0.0) + float(e.amount or 0)
        by_date[ds]['entries'].append({
            'id': e.id,
            'title': e.title,
            'category': e.category,
            'unit_price': float(e.unit_price) if e.unit_price is not None else None,
            'amount': float(e.amount or 0),
            'quantity': float(e.quantity or 0) if e.quantity is not None else None,
            'recurring': bool(e.recurring_id),
            'payer': e.payer or ''
        })
    # Determine top category name
    top_category = None
    if per_category:
        top_category = max(per_category.items(), key=lambda kv: kv[1])[0]

    rules = RecurringExpense.query.order_by(RecurringExpense.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    import json
    # Settings
    settings = {'currency': '₹', 'categories': []}
    try:
        rows = db.session.execute(db.text("SELECT key, value FROM app_setting WHERE key IN ('currency','categories')"))
        data = {k: v for k, v in rows}
        if data.get('currency'): settings['currency'] = data['currency']
        if data.get('categories'): settings['categories'] = [c.strip() for c in data['categories'].split(',') if c.strip()]
    except Exception:
        pass
    payload = {
        'by_date': by_date,
        'summary': {
            'total_this_month': total,
            'per_payer': per_payer,
            'per_category': per_category,
            'top_category': top_category
        },
        'year': y,
        'month': m,
        'settings': settings
    }
    return render_template('expenses.html', rules=rules, config=config, expenses_json=json.dumps(payload))

@main_bp.route('/expenses/delete/<int:entry_id>', methods=['POST'])
def delete_expense(entry_id):
    e = ExpenseEntry.query.get_or_404(entry_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == e.payer:
        db.session.delete(e)
        db.session.commit()
    # Preserve view; if args missing, fall back to the deleted entry's date
    y = request.args.get('y') or (e.date.year if e.date else date.today().year)
    m = request.args.get('m') or (e.date.month if e.date else date.today().month)
    sel = request.args.get('sel') or (e.date.strftime('%Y-%m-%d') if e.date else None)
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))

@main_bp.route('/expenses/recurring/delete/<int:rec_id>', methods=['POST'])
def delete_recurring(rec_id):
    r = RecurringExpense.query.get_or_404(rec_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == r.creator:
        # If requested, delete all generated entries for this rule
        if request.form.get('delete_entries'):
            try:
                entries = ExpenseEntry.query.filter_by(recurring_id=r.id).all()
                for e in entries:
                    db.session.delete(e)
            except Exception:
                pass
        db.session.delete(r)
        db.session.commit()
    y = request.args.get('y') or date.today().year
    m = request.args.get('m') or date.today().month
    sel = request.args.get('sel')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))

# Edit Expense (admin or owner)
@main_bp.route('/expenses/edit/<int:entry_id>', methods=['POST'])
def edit_expense(entry_id):
    e = ExpenseEntry.query.get_or_404(entry_id)
    user = bleach.clean(request.form.get('user', ''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user not in admin_aliases and user != (e.payer or ''):
        flash('Not allowed to edit this expense.', 'error')
        return redirect(url_for('main.expenses'))
    # Update allowed fields
    date_s = request.form.get('date')
    if date_s:
        try:
            e.date = datetime.strptime(date_s, '%Y-%m-%d').date()
        except Exception:
            pass
    title = request.form.get('title'); category = request.form.get('category')
    unit_price = request.form.get('unit_price'); quantity = request.form.get('quantity'); amount = request.form.get('amount')
    payer = request.form.get('payer')
    if title is not None: e.title = bleach.clean(title)
    if category is not None: e.category = bleach.clean(category)
    if unit_price is not None and unit_price != '': e.unit_price = float(unit_price)
    if quantity is not None and quantity != '': e.quantity = float(quantity)
    if amount is not None and amount != '': e.amount = float(amount)
    if payer is not None: e.payer = bleach.clean(payer)
    db.session.commit()
    flash('Expense updated.', 'success')
    y = request.args.get('y') or e.date.year
    m = request.args.get('m') or e.date.month
    sel = request.args.get('sel') or e.date.strftime('%Y-%m-%d')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))

# JSON API for monthly expenses
@main_bp.route('/api/expenses/month', methods=['GET'])
def api_expenses_month():
    # Ensure recurring generation has run recently
    today = date.today()
    recs = RecurringExpense.query.all()
    for r in recs:
        start = r.start_date or today
        last = r.last_generated_date
        def next_date(d):
            if r.frequency == 'daily':
                return d + timedelta(days=1)
            if r.frequency == 'weekly':
                return d + timedelta(weeks=1)
            mode = getattr(r, 'monthly_mode', 'day_of_month') or 'day_of_month'
            if mode == 'calendar':
                ny = d.year + (1 if d.month == 12 else 0)
                nm = 1 if d.month == 12 else d.month + 1
                return date(ny, nm, 1)
            else:
                base_day = (r.start_date or today).day
                ny = d.year + (1 if d.month == 12 else 0)
                nm = 1 if d.month == 12 else d.month + 1
                last = _calendar.monthrange(ny, nm)[1]
                day = min(base_day, last)
                return date(ny, nm, day)
        # Seed similarly to page route
        if last is None or (last and last < start):
            if r.frequency == 'daily':
                d = start
            elif r.frequency == 'weekly':
                d = start
            else:
                mode = getattr(r, 'monthly_mode', 'day_of_month') or 'day_of_month'
                if mode == 'calendar':
                    if start.day == 1:
                        d = start
                    else:
                        ny = start.year + (1 if start.month == 12 else 0)
                        nm = 1 if start.month == 12 else start.month + 1
                        d = date(ny, nm, 1)
                else:
                    d = start
        else:
            d = next_date(last)
        while d <= today and (not r.end_date or d <= r.end_date):
            exists = ExpenseEntry.query.filter_by(date=d, recurring_id=r.id).first()
            if not exists:
                qty = r.default_quantity or 1.0
                amt = (r.unit_price or 0.0) * qty
                db.session.add(ExpenseEntry(date=d, title=r.title, category=getattr(r, 'category', None), unit_price=r.unit_price, quantity=qty, amount=amt, payer=r.creator, recurring_id=r.id))
            r.last_generated_date = d
            d = next_date(d)
    db.session.commit()

    try:
        y = int(request.args.get('year') or today.year)
        m = int(request.args.get('month') or today.month)
    except Exception:
        y, m = today.year, today.month
    month_start = date(y, m, 1)
    last_day = _calendar.monthrange(y, m)[1]
    month_end = date(y, m, last_day)

    q_entries = ExpenseEntry.query.filter(ExpenseEntry.date >= month_start, ExpenseEntry.date <= month_end).order_by(ExpenseEntry.date.asc(), ExpenseEntry.timestamp.asc()).all()
    by_date = {}
    total = 0.0
    per_payer = {}
    per_category = {}
    for e in q_entries:
        ds = e.date.strftime('%Y-%m-%d')
        by_date.setdefault(ds, {'total': 0.0, 'entries': []})
        by_date[ds]['total'] += float(e.amount or 0)
        total += float(e.amount or 0)
        per_payer[e.payer or ''] = per_payer.get(e.payer or '', 0.0) + float(e.amount or 0)
        if e.category:
            per_category[e.category] = per_category.get(e.category, 0.0) + float(e.amount or 0)
        by_date[ds]['entries'].append({
            'id': e.id,
            'title': e.title,
            'category': e.category,
            'unit_price': float(e.unit_price) if e.unit_price is not None else None,
            'amount': float(e.amount or 0),
            'quantity': float(e.quantity or 0) if e.quantity is not None else None,
            'recurring': bool(e.recurring_id),
            'payer': e.payer or ''
        })
    top_category = None
    if per_category:
        top_category = max(per_category.items(), key=lambda kv: kv[1])[0]
    # Settings
    settings = {'currency': '₹', 'categories': []}
    try:
        rows = db.session.execute(db.text("SELECT key, value FROM app_setting WHERE key IN ('currency','categories')"))
        data = {k: v for k, v in rows}
        if data.get('currency'): settings['currency'] = data['currency']
        if data.get('categories'): settings['categories'] = [c.strip() for c in data['categories'].split(',') if c.strip()]
    except Exception:
        pass
    return jsonify({
        'by_date': by_date,
        'summary': {
            'total_this_month': total,
            'per_payer': per_payer,
            'per_category': per_category,
            'top_category': top_category
        },
        'year': y,
        'month': m,
        'settings': settings
    })

# Bulk delete expenses (admin or owners for each)
@main_bp.route('/expenses/bulk-delete', methods=['POST'])
def bulk_delete_expenses():
    ids = request.form.getlist('ids')
    user = bleach.clean(request.form.get('user', ''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    deleted = 0
    for sid in ids:
        try:
            eid = int(sid)
        except Exception:
            continue
        e = ExpenseEntry.query.get(eid)
        if not e:
            continue
        if user in admin_aliases or user == (e.payer or ''):
            db.session.delete(e)
            deleted += 1
    if deleted:
        db.session.commit()
        flash(f'Deleted {deleted} expense(s).', 'success')
    else:
        flash('No expenses deleted (not allowed or invalid IDs).', 'error')
    y = request.args.get('y') or date.today().year
    m = request.args.get('m') or date.today().month
    sel = request.args.get('sel')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))

# Edit recurring rule (admin or creator)
@main_bp.route('/expenses/recurring/edit/<int:rec_id>', methods=['POST'])
def edit_recurring(rec_id):
    r = RecurringExpense.query.get_or_404(rec_id)
    user = bleach.clean(request.form.get('user',''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user not in admin_aliases and user != (r.creator or ''):
        flash('Not allowed to edit this rule.', 'error')
        return redirect(url_for('main.expenses'))
    # Update fields
    title = request.form.get('title'); unit_price = request.form.get('unit_price'); default_quantity = request.form.get('default_quantity')
    category = request.form.get('category')
    frequency = request.form.get('frequency'); monthly_mode = request.form.get('monthly_mode')
    start_date = request.form.get('start_date'); end_date = request.form.get('end_date')
    if title is not None: r.title = bleach.clean(title)
    if unit_price not in (None, ''): r.unit_price = float(unit_price)
    if default_quantity not in (None, ''): r.default_quantity = float(default_quantity)
    if category is not None: r.category = bleach.clean(category)
    if frequency is not None: r.frequency = bleach.clean(frequency)
    if monthly_mode is not None: r.monthly_mode = bleach.clean(monthly_mode)
    if start_date: r.start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
    if end_date: r.end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
    # Also update existing entries generated by this rule to reflect changed fields
    entries = ExpenseEntry.query.filter_by(recurring_id=r.id).all()
    for e in entries:
        # Keep date and payer/amount integrity, but recompute amount if unit price or quantity changed
        e.title = r.title
        e.category = getattr(r, 'category', None)
        # If the rule defines a unit_price/quantity, propagate and recompute amount; else preserve existing
        if r.unit_price is not None:
            e.unit_price = r.unit_price
        if r.default_quantity is not None:
            e.quantity = r.default_quantity
        if e.unit_price is not None and e.quantity is not None:
            try:
                e.amount = float(e.unit_price) * float(e.quantity)
            except Exception:
                pass
    # Align last_generated_date to latest existing generated entry after edits
    try:
        if entries:
            r.last_generated_date = max(e.date for e in entries if getattr(e, 'date', None))
        else:
            r.last_generated_date = None
    except Exception:
        pass
    db.session.commit()
    flash('Recurring rule updated.', 'success')
    y = request.args.get('y') or date.today().year
    m = request.args.get('m') or date.today().month
    sel = request.args.get('sel')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))

# Settings endpoints (currency, categories)
@main_bp.route('/expenses/settings', methods=['POST'])
def save_expense_settings():
    # Only admin can change app-wide settings
    user = bleach.clean(request.form.get('user',''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    if user != admin_name and user not in {'Administrator', 'admin'}:
        flash('Only admin can update settings.', 'error')
        return redirect(url_for('main.expenses'))
    currency = bleach.clean(request.form.get('currency','₹'))
    categories = bleach.clean(request.form.get('categories',''))  # comma-separated
    from sqlalchemy import text as _text
    db.session.execute(_text("REPLACE INTO app_setting(key, value) VALUES('currency', :v)"), { 'v': currency })
    db.session.execute(_text("REPLACE INTO app_setting(key, value) VALUES('categories', :v)"), { 'v': categories })
    db.session.commit()
    flash('Settings saved.', 'success')
    y = request.args.get('y') or date.today().year
    m = request.args.get('m') or date.today().month
    sel = request.args.get('sel')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))

@main_bp.route('/shopping/check/<int:item_id>', methods=['POST'])
def check_shopping(item_id):
    item = ShoppingItem.query.get_or_404(item_id)
    item.checked = not item.checked
    db.session.commit()
    return redirect(url_for('main.shopping'))

@main_bp.route('/shopping/delete/<int:item_id>', methods=['POST'])
def delete_shopping(item_id):
    item = ShoppingItem.query.get_or_404(item_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == item.creator:
        db.session.delete(item)
        db.session.commit()
    return redirect(url_for('main.shopping'))

# Deprecated: Dedicated Who is Home page has been removed in favor of dashboard controls.

# To-Do/Chore List
@main_bp.route('/chores', methods=['GET', 'POST'])
def chores():
    if request.method == 'POST':
        description = bleach.clean(request.form['description'])
        creator = bleach.clean(request.form['creator'])
        chore = Chore(description=description, creator=creator)
        db.session.add(chore)
        db.session.commit()
        return redirect(url_for('main.chores'))
    chores = Chore.query.order_by(Chore.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('chores.html', chores=chores, config=config)

@main_bp.route('/chores/toggle/<int:chore_id>', methods=['POST'])
def toggle_chore(chore_id):
    chore = Chore.query.get_or_404(chore_id)
    chore.done = not getattr(chore, 'done', False)
    db.session.commit()
    return redirect(url_for('main.chores'))

@main_bp.route('/chores/delete/<int:chore_id>', methods=['POST'])
def delete_chore(chore_id):
    chore = Chore.query.get_or_404(chore_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == chore.creator:
        db.session.delete(chore)
        db.session.commit()
    return redirect(url_for('main.chores'))

# Recipe Book
@main_bp.route('/recipes', methods=['GET', 'POST'])
def recipes():
    if request.method == 'POST':
        title = bleach.clean(request.form['title'])
        link = bleach.clean(request.form.get('link'))
        ingredients = bleach.clean(request.form.get('ingredients'), tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
        instructions = bleach.clean(request.form.get('instructions'), tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
        creator = bleach.clean(request.form['creator'])
        if not (ingredients and ingredients.strip()) and not (instructions and instructions.strip()):
            flash('Please add ingredients or instructions (or both).', 'error')
            # render page without losing title/link fields
            recipes = Recipe.query.order_by(Recipe.timestamp.desc()).all()
            config = current_app.config['HOMEHUB_CONFIG']
            return render_template('recipes.html', recipes=recipes, config=config, form_title=title, form_link=link, form_ingredients=ingredients or '', form_instructions=instructions or '')
        recipe = Recipe(title=title, link=link, ingredients=ingredients, instructions=instructions, creator=creator)
        db.session.add(recipe)
        db.session.commit()
        return redirect(url_for('main.recipes'))
    recipes = Recipe.query.order_by(Recipe.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('recipes.html', recipes=recipes, config=config)

@main_bp.route('/recipes/delete/<int:recipe_id>', methods=['POST'])
def delete_recipe(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == recipe.creator:
        db.session.delete(recipe)
        db.session.commit()
    return redirect(url_for('main.recipes'))

# Expiry Tracker
@main_bp.route('/expiry', methods=['GET', 'POST'])
def expiry():
    if request.method == 'POST':
        name = bleach.clean(request.form['name'])
        expiry_date = request.form['expiry_date']
        creator = bleach.clean(request.form['creator'])
        expiry_item = ExpiryItem(name=name, expiry_date=datetime.strptime(expiry_date, '%Y-%m-%d').date(), creator=creator)
        db.session.add(expiry_item)
        db.session.commit()
        return redirect(url_for('main.expiry'))
    items = ExpiryItem.query.order_by(ExpiryItem.expiry_date.asc()).all()
    today = date.today()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('expiry.html', items=items, today=today, config=config)

@main_bp.route('/expiry/delete/<int:item_id>', methods=['POST'])
def delete_expiry(item_id):
    it = ExpiryItem.query.get_or_404(item_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == it.creator:
        db.session.delete(it)
        db.session.commit()
    return redirect(url_for('main.expiry'))

# URL Shortener
@main_bp.route('/shorten', methods=['GET', 'POST'])
def shorten():
    if request.method == 'POST':
        original_url = bleach.clean(request.form['original_url'])
        creator = bleach.clean(request.form['creator'])
        short_code = generate_short_code()
        while ShortURL.query.filter_by(short_code=short_code).first():
            short_code = generate_short_code()
        short_url = ShortURL(original_url=original_url, short_code=short_code, creator=creator)
        db.session.add(short_url)
        db.session.commit()
        return redirect(url_for('main.shorten'))
    urls = ShortURL.query.order_by(ShortURL.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('shorten.html', urls=urls, config=config)

@main_bp.route('/s/<short_code>')
def redirect_short(short_code):
    short_url = ShortURL.query.filter_by(short_code=short_code).first_or_404()
    return redirect(short_url.original_url)

@main_bp.route('/shorten/delete/<int:url_id>', methods=['POST'])
def delete_short(url_id):
    su = ShortURL.query.get_or_404(url_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == su.creator:
        db.session.delete(su)
        db.session.commit()
    return redirect(url_for('main.shorten'))


# Media Downloader (yt-dlp integration)
@main_bp.route('/media', methods=['GET', 'POST'])
def media():
    import subprocess, re
    if request.method == 'POST':
        url = bleach.clean(request.form['url'])
        creator = bleach.clean(request.form['creator'])
        fmt = bleach.clean(request.form.get('format', 'mp4'))
        quality = bleach.clean(request.form.get('quality', 'best'))
        # Create a placeholder record marked pending
        base = f"media_{int(datetime.utcnow().timestamp())}"
        # Let yt-dlp append extension automatically
        output_tmpl = os.path.join(MEDIA_FOLDER, base + ".%(ext)s")
        media_obj = Media(title=url, url=url, creator=creator, filepath='', status='pending')
        db.session.add(media_obj)
        db.session.commit()
        flash('Download queued. You can switch tabs; refresh to check status.', 'info')
        # Build yt-dlp command
        cmd = ["yt-dlp", "-o", output_tmpl]
        if fmt == 'mp3':
            cmd += ["-x", "--audio-format", "mp3"]
        else:
            # Prefer bestvideo+bestaudio with mp4 fallback, honoring selected quality
            # Provide a sane default ladder if user selected shorthand like 'best'
            selected = quality or 'best'
            if selected == 'best':
                # best available up to original, prefer mp4 mux else fallback
                fmt_string = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
            else:
                # Use user-provided filter, but still add fallbacks and prefer mp4 container when possible
                fmt_string = f"{selected}/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best"
            cmd += ["-f", fmt_string]
            # Merge into mp4 when possible without re-encoding
            cmd += ["--merge-output-format", "mp4"]
        cmd += [url]

        # Capture the real app object now to use inside the background thread
        app_obj = current_app._get_current_object()

        def worker(app, mid: int, base_prefix: str, command: list):
            # Use the app's context explicitly inside the thread
            with app.app_context():
                m = Media.query.get(mid)
                try:
                    # Stream output to capture progress lines
                    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                    last_percent = -1
                    for line in proc.stdout:
                        # Parse percent like: "[download]  12.3% of ..."
                        try:
                            m = Media.query.get(mid)
                            if not m:
                                continue
                            match = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", line)
                            if match:
                                p = int(float(match.group(1)))
                                if p != last_percent and p % 5 == 0:
                                    m.progress = f"{p}%"
                                    db.session.commit()
                                    last_percent = p
                        except Exception:
                            pass
                    ret = proc.wait()
                    if ret != 0:
                        raise RuntimeError(f"yt-dlp exited with {ret}")
                    saved = None
                    for fname in os.listdir(MEDIA_FOLDER):
                        if fname.startswith(base_prefix):
                            saved = fname
                            break
                    m.filepath = saved or ''
                    m.status = 'done'
                except Exception:
                    m.status = 'error'
                finally:
                    m.progress = None
                    db.session.commit()

        Thread(target=worker, args=(app_obj, media_obj.id, base, cmd), daemon=True).start()
        return redirect(url_for('main.media'))
    media_list = Media.query.order_by(Media.download_time.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('media.html', media_list=media_list, config=config)

@main_bp.route('/media/status/<int:media_id>')
def media_status(media_id):
    m = Media.query.get_or_404(media_id)
    return jsonify({
        'status': m.status,
        'progress': m.progress,
        'filepath': m.filepath,
    })

# Calendar/Reminders
@main_bp.route('/calendar/add', methods=['POST'])
def add_reminder():
    date_s = bleach.clean(request.form.get('date'))
    title = bleach.clean(request.form.get('title'))
    description = bleach.clean(request.form.get('description'), tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
    creator = bleach.clean(request.form.get('creator'))
    if not (date_s and title):
        flash('Date and title are required for reminders.', 'error')
        return redirect(url_for('main.index'))
    try:
        d = datetime.strptime(date_s, '%Y-%m-%d').date()
    except Exception:
        flash('Invalid date.', 'error')
        return redirect(url_for('main.index'))
    r = Reminder(date=d, title=title, description=description, creator=creator)
    db.session.add(r)
    db.session.commit()
    flash('Reminder added.', 'success')
    # Preserve the selected date in query so UI can stay on that month
    return redirect(url_for('main.index', date=date_s))

@main_bp.route('/calendar/delete/<int:reminder_id>', methods=['POST'])
def delete_reminder(reminder_id):
    r = Reminder.query.get_or_404(reminder_id)
    user = bleach.clean(request.form.get('user'))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == r.creator:
        db.session.delete(r)
        db.session.commit()
        flash('Reminder deleted.', 'success')
    else:
        flash('Not allowed to delete this reminder.', 'error')
    # After deletion, try to stay on the same date (day of deleted reminder)
    date_s = None
    try:
        if r.date:
            date_s = r.date.strftime('%Y-%m-%d')
    except Exception:
        date_s = None
    return redirect(url_for('main.index', date=date_s) if date_s else url_for('main.index'))

@main_bp.route('/calendar/delete_bulk', methods=['POST'])
def delete_reminders_bulk():
    """Delete multiple reminders in one action.
    Expects form field 'ids' as comma-separated reminder ids and 'user'."""
    ids_raw = bleach.clean(request.form.get('ids', ''))
    user = bleach.clean(request.form.get('user', ''))
    if not ids_raw:
        return redirect(url_for('main.index'))
    id_list = []
    for part in ids_raw.split(','):
        part = part.strip()
        if part.isdigit():
            id_list.append(int(part))
    if not id_list:
        return redirect(url_for('main.index'))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    kept_date = None
    deleted = 0
    for rid in id_list:
        r = Reminder.query.get(rid)
        if not r:
            continue
        if kept_date is None and getattr(r, 'date', None):
            try:
                kept_date = r.date.strftime('%Y-%m-%d')
            except Exception:
                kept_date = None
        if user in admin_aliases or user == r.creator:
            db.session.delete(r)
            deleted += 1
    if deleted:
        db.session.commit()
        flash(f'Deleted {deleted} reminder(s).', 'success')
    else:
        flash('No reminders deleted (permission?).', 'error')
    return redirect(url_for('main.index', date=kept_date) if kept_date else url_for('main.index'))

@main_bp.route('/media/<filename>')
def serve_media(filename):
    return send_from_directory(MEDIA_FOLDER, filename)

@main_bp.route('/media/delete/<int:media_id>', methods=['POST'])
def delete_media(media_id):
    m = Media.query.get_or_404(media_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == m.creator:
        # remove files that match base prefix
        try:
            if m.filepath:
                base = m.filepath.rsplit('.', 1)[0]
                for fname in os.listdir(MEDIA_FOLDER):
                    if fname.startswith(base):
                        os.remove(os.path.join(MEDIA_FOLDER, fname))
        except Exception:
            pass
        db.session.delete(m)
        db.session.commit()
    return redirect(url_for('main.media'))

# PDF Compressor
@main_bp.route('/pdfs', methods=['GET', 'POST'])
def pdfs():
    import shutil, subprocess
    if request.method == 'POST':
        pdf_file = request.files['pdf']
        creator = bleach.clean(request.form['creator'])
        mode = bleach.clean(request.form.get('mode', 'fast'))
        filename = secure_filename(pdf_file.filename)
        input_path = os.path.join(PDF_FOLDER, filename)
        pdf_file.save(input_path)
        # Compress PDF using Ghostscript only
        compressed_path = f"compressed_{filename}"
        output_path = os.path.join(PDF_FOLDER, compressed_path)
        try:
            gs_cmd = [
                'gs', '-sDEVICE=pdfwrite', '-dCompatibilityLevel=1.4',
                '-dPDFSETTINGS=/ebook', '-dNOPAUSE', '-dQUIET', '-dBATCH',
                f'-sOutputFile={output_path}', input_path
            ]
            subprocess.run(gs_cmd, check=True)
        except Exception:
            # As a minimal fallback just copy the file
            shutil.copy(input_path, output_path)
        # Save record
        pdf_obj = PDF(filename=filename, creator=creator, compressed_path=compressed_path)
        db.session.add(pdf_obj)
        db.session.commit()
        return redirect(url_for('main.pdfs'))
    pdfs = PDF.query.order_by(PDF.upload_time.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('pdfs.html', pdfs=pdfs, config=config)

@main_bp.route('/pdfs/<filename>')
def serve_pdf(filename):
    return send_from_directory(PDF_FOLDER, filename)

@main_bp.route('/pdfs/delete/<int:pdf_id>', methods=['POST'])
def delete_pdf(pdf_id):
    p = PDF.query.get_or_404(pdf_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == p.creator:
        try:
            if p.compressed_path:
                os.remove(os.path.join(PDF_FOLDER, p.compressed_path))
        except Exception:
            pass
        db.session.delete(p)
        db.session.commit()
    return redirect(url_for('main.pdfs'))

# QR Code Generator
@main_bp.route('/qr', methods=['GET', 'POST'])
def qr():
    import qrcode
    import base64
    qr_img = None
    if request.method == 'POST':
        qrtext = bleach.clean(request.form['qrtext'])
        creator = bleach.clean(request.form['creator'])
        qr_code = qrcode.make(qrtext)
        from io import BytesIO
        buf = BytesIO()
        qr_code.save(buf, format='PNG')
        qr_img = base64.b64encode(buf.getvalue()).decode('utf-8')
        # Save to disk and record history
        filename = f"qr_{int(datetime.utcnow().timestamp())}.png"
        path = os.path.join(BASE_DIR, 'static', filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(buf.getvalue())
        db.session.add(QRCode(text=qrtext, filename=filename, creator=creator))
        db.session.commit()
    history = QRCode.query.order_by(QRCode.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('qr.html', qr_img=qr_img, history=history, config=config)

@main_bp.route('/qr/delete/<int:qr_id>', methods=['POST'])
def delete_qr(qr_id):
    q = QRCode.query.get_or_404(qr_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == q.creator:
        try:
            os.remove(os.path.join(BASE_DIR, 'static', q.filename))
        except Exception:
            pass
        db.session.delete(q)
        db.session.commit()
    return redirect(url_for('main.qr'))

# Notice Board APIs
@main_bp.route('/notice', methods=['POST'])
def update_notice():
    content = bleach.clean(request.form.get('content', ''), tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
    user = bleach.clean(request.form.get('user', ''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    if user != admin_name:
        flash('Only admin can update the notice.', 'error')
        return redirect(url_for('main.index'))
    n = Notice.query.order_by(Notice.updated_at.desc()).first()
    now = datetime.utcnow()
    if n:
        n.content = content
        n.updated_by = user
        n.updated_at = now
    else:
        db.session.add(Notice(content=content, updated_by=user, updated_at=now))
    db.session.commit()
    flash('Notice updated.', 'success')
    return redirect(url_for('main.index'))

# Lightweight endpoints for updating/clearing current user's home status from the dashboard
@main_bp.route('/whoishome', methods=['POST'])
def who_is_home_action():
    """Unified endpoint for updating or clearing a home status.
    Expects hidden field 'action' = update|clear."""
    action = bleach.clean(request.form.get('action', 'update'))
    config = current_app.config['HOMEHUB_CONFIG']
    family = set(config.get('family_members', []))
    name = bleach.clean(request.form.get('name', ''))
    if not name or name not in family:
        if request.headers.get('X-Requested-With') != 'fetch':
            flash('Invalid user for status.', 'error')
        # For AJAX we just return a JSON error payload
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'Invalid user'}), 400
        return redirect(url_for('main.index'))
    result = None
    if action == 'clear':
        hs = HomeStatus.query.filter_by(name=name).first()
        if hs:
            db.session.delete(hs)
            db.session.commit()
            result = 'cleared'
            if request.headers.get('X-Requested-With') != 'fetch':
                flash('Status cleared.', 'success')
        else:
            result = 'none'
            if request.headers.get('X-Requested-With') != 'fetch':
                flash('No status to clear.', 'info')
    else:  # update
        status = bleach.clean(request.form.get('status', '')) or 'Away'
        hs = HomeStatus.query.filter_by(name=name).first()
        if hs:
            hs.status = status
        else:
            db.session.add(HomeStatus(name=name, status=status))
        db.session.commit()
        result = 'updated'
        if request.headers.get('X-Requested-With') != 'fetch':
            flash('Status updated.', 'success')
    # AJAX (fetch) support
    if request.headers.get('X-Requested-With') == 'fetch':
        who_statuses = {s.name: s.status for s in HomeStatus.query.all() if s.name in family}
        member_statuses = {ms.name: ms.text for ms in MemberStatus.query.all() if ms.name in family and (ms.text or '').strip()}
        # Ensure result has a value
        result = result or 'updated'
        return jsonify({'ok': True, 'who_statuses': who_statuses, 'member_statuses': member_statuses, 'result': result})
    # Preserve date if present (calendar context)
    date_q = request.args.get('date') or request.form.get('date')
    return redirect(url_for('main.index', date=date_q) if date_q else url_for('main.index'))

# Member personal status (text) updates under notice board
@main_bp.route('/status/update', methods=['POST'])
def member_status_update():
    config = current_app.config['HOMEHUB_CONFIG']
    family = set(config.get('family_members', []))
    name = bleach.clean(request.form.get('name', ''))
    raw_text = request.form.get('text', '') or ''
    text = bleach.clean(raw_text).strip()
    if not name or name not in family:
        if request.headers.get('X-Requested-With') != 'fetch':
            flash('Invalid user for status.', 'error')
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'Invalid user'}), 400
        return redirect(url_for('main.index'))
    # Do not allow blank/whitespace-only personal statuses
    if not text:
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'Empty status'}), 400
        else:
            flash('Status cannot be empty.', 'error')
            return redirect(url_for('main.index'))
    ms = MemberStatus.query.filter_by(name=name).first()
    now = datetime.utcnow()
    if ms:
        ms.text = text
        ms.updated_at = now
    else:
        db.session.add(MemberStatus(name=name, text=text, updated_at=now))
    db.session.commit()
    if request.headers.get('X-Requested-With') != 'fetch':
        flash('Status saved.', 'success')
    if request.headers.get('X-Requested-With') == 'fetch':
        who_statuses = {s.name: s.status for s in HomeStatus.query.all() if s.name in family}
        member_statuses = {ms.name: ms.text for ms in MemberStatus.query.all() if ms.name in family and (ms.text or '').strip()}
        return jsonify({'ok': True, 'who_statuses': who_statuses, 'member_statuses': member_statuses, 'result': 'saved'})
    return redirect(url_for('main.index'))

@main_bp.route('/status/delete', methods=['POST'])
def member_status_delete():
    config = current_app.config['HOMEHUB_CONFIG']
    family = set(config.get('family_members', []))
    name = bleach.clean(request.form.get('name', ''))
    if not name or name not in family:
        if request.headers.get('X-Requested-With') != 'fetch':
            flash('Invalid user for status removal.', 'error')
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'Invalid user'}), 400
        return redirect(url_for('main.index'))
    ms = MemberStatus.query.filter_by(name=name).first()
    removed = False
    if ms:
        db.session.delete(ms)
        db.session.commit()
        removed = True
        if request.headers.get('X-Requested-With') != 'fetch':
            flash('Status removed.', 'success')
    if request.headers.get('X-Requested-With') == 'fetch':
        who_statuses = {s.name: s.status for s in HomeStatus.query.all() if s.name in family}
        member_statuses = {ms.name: ms.text for ms in MemberStatus.query.all() if ms.name in family and (ms.text or '').strip()}
        return jsonify({'ok': True, 'who_statuses': who_statuses, 'member_statuses': member_statuses, 'result': 'removed' if removed else 'none'})
    return redirect(url_for('main.index'))