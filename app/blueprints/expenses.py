from flask import render_template, request, redirect, url_for, flash, jsonify, current_app
from datetime import datetime, date, timedelta
import calendar as _calendar
import json
from ..models import db, RecurringExpense, ExpenseEntry
from ..security import sanitize_text
from ..blueprints import main_bp
import bleach


def _generate_recurring_entries_until(today: date | None = None) -> None:
    today = today or date.today()
    recs = RecurringExpense.query.all()
    for r in recs:
        start = r.start_date or today
        # Generate from rule start date; don't clamp to effective_from so rule owns entire range
        base_day = (r.start_date or today).day
        last = r.last_generated_date

        def next_date(d: date) -> date:
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
                ny = d.year + (1 if d.month == 12 else 0)
                nm = 1 if d.month == 12 else d.month + 1
                last_dom = _calendar.monthrange(ny, nm)[1]
                day = min(base_day, last_dom)
                return date(ny, nm, day)

        if last is None or (last and last < start):
            if r.frequency in ('daily', 'weekly'):
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
                db.session.add(ExpenseEntry(
                    date=d,
                    title=r.title,
                    category=getattr(r, 'category', None),
                    unit_price=r.unit_price,
                    quantity=qty,
                    amount=amt,
                    payer=r.creator,
                    recurring_id=r.id
                ))
            r.last_generated_date = d
            d = next_date(d)
    db.session.commit()


def _load_expense_settings() -> dict:
    settings = {'currency': '\u20b9', 'categories': []}
    try:
        rows = db.session.execute(db.text("SELECT key, value FROM app_setting WHERE key IN ('currency','categories')"))
        data = {k: v for k, v in rows}
        if data.get('currency'):
            settings['currency'] = data['currency']
        if data.get('categories'):
            settings['categories'] = [c.strip() for c in data['categories'].split(',') if c.strip()]
    except Exception:
        pass
    return settings


def _build_month_payload(y: int, m: int) -> dict:
    month_start = date(y, m, 1)
    last_day = _calendar.monthrange(y, m)[1]
    month_end = date(y, m, last_day)

    q_entries = (
        ExpenseEntry.query
        .filter(ExpenseEntry.date >= month_start, ExpenseEntry.date <= month_end)
        .order_by(ExpenseEntry.date.asc(), ExpenseEntry.timestamp.asc())
        .all()
    )
    by_date: dict[str, dict] = {}
    total = 0.0
    per_payer: dict[str, float] = {}
    per_category: dict[str, float] = {}
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

    settings = _load_expense_settings()
    payload = {
        'by_date': by_date,
        'summary': {
            'total_this_month': total,
            'per_payer': per_payer,
            'per_category': per_category,
            'top_category': top_category,
        },
        'year': y,
        'month': m,
        'settings': settings,
    }
    return payload


@main_bp.route('/expenses', methods=['GET', 'POST'])
def expenses():
    today = date.today()
    # Ensure recurring entries are generated up to today for a consistent view
    _generate_recurring_entries_until(today)

    if request.method == 'POST':
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
            db.session.add(RecurringExpense(title=title, unit_price=unit_price, default_quantity=default_quantity, frequency=frequency, monthly_mode=monthly_mode, category=category, start_date=sd, end_date=ed, creator=creator, effective_from=sd))
            db.session.commit()
            flash('Recurring expense added.', 'success')
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
            y = request.args.get('y') or d.year
            m = request.args.get('m') or d.month
            sel = request.args.get('sel') or d.strftime('%Y-%m-%d')
            return redirect(url_for('main.expenses', y=y, m=m, sel=sel))

    try:
        y = int(request.args.get('y') or today.year)
        m = int(request.args.get('m') or today.month)
    except Exception:
        y, m = today.year, today.month

    payload = _build_month_payload(y, m)
    rules = RecurringExpense.query.order_by(RecurringExpense.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('expenses.html', rules=rules, config=config, expenses_json=json.dumps(payload))


@main_bp.route('/expenses/recurring/edit/<int:rid>', methods=['POST'])
def edit_recurring_expense(rid):
    r = RecurringExpense.query.get_or_404(rid)
    user = sanitize_text(request.form.get('user', ''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if not (user in admin_aliases or user == (r.creator or '')):
        flash('Not allowed to edit rule.', 'error')
        return redirect(url_for('main.expenses'))
    # Update fields
    r.title = bleach.clean(request.form.get('title', r.title))
    cat_raw = request.form.get('category')
    if cat_raw is not None:
        cat_val = bleach.clean(cat_raw or '')
        r.category = cat_val or None
    up = request.form.get('unit_price'); dq = request.form.get('default_quantity')
    r.unit_price = float(up) if up not in (None, '') else r.unit_price
    r.default_quantity = float(dq) if dq not in (None, '') else r.default_quantity
    freq = bleach.clean(request.form.get('frequency', r.frequency))
    mmode = bleach.clean(request.form.get('monthly_mode', getattr(r, 'monthly_mode', 'day_of_month')))
    r.frequency = freq
    r.monthly_mode = mmode
    sd = request.form.get('start_date'); ed = request.form.get('end_date')
    r.start_date = datetime.strptime(sd, '%Y-%m-%d').date() if sd else r.start_date
    r.end_date = datetime.strptime(ed, '%Y-%m-%d').date() if ed else r.end_date
    # Do not force effective_from to today; edits are authoritative for full rule window
    today = date.today()
    db.session.commit()
    # Authoritative pruning: remove entries outside [start_date, end_date]
    try:
        if r.start_date:
            ExpenseEntry.query.filter(ExpenseEntry.recurring_id==r.id, ExpenseEntry.date < r.start_date).delete()
        if r.end_date:
            ExpenseEntry.query.filter(ExpenseEntry.recurring_id==r.id, ExpenseEntry.date > r.end_date).delete()
        db.session.commit()
    except Exception:
        db.session.rollback()
    # Update all in-range entries to reflect new rule (title/category/prices/qty)
    try:
        entries = ExpenseEntry.query.filter(ExpenseEntry.recurring_id==r.id).all()
        for e in entries:
            e.title = r.title
            e.category = getattr(r, 'category', None)
            e.unit_price = r.unit_price
            e.quantity = r.default_quantity
            e.amount = (r.unit_price or 0.0) * (r.default_quantity or 1.0)
        db.session.commit()
    except Exception:
        db.session.rollback()
    flash('Recurring rule updated. Entries pruned/updated to match.', 'success')
    # Preserve view state
    y = request.args.get('y') or today.year
    m = request.args.get('m') or today.month
    sel = request.args.get('sel')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel, open='recurring'))


@main_bp.route('/expenses/recurring/delete/<int:rid>', methods=['POST'])
def delete_recurring_expense(rid):
    r = RecurringExpense.query.get_or_404(rid)
    user = sanitize_text(request.form.get('user', ''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if not (user in admin_aliases or user == (r.creator or '')):
        flash('Not allowed to delete rule.', 'error')
        return redirect(url_for('main.expenses'))
    delete_entries = request.form.get('delete_entries') in ('1', 'true', 'on', 'yes')
    if delete_entries:
        try:
            ExpenseEntry.query.filter_by(recurring_id=r.id).delete()
        except Exception:
            pass
    db.session.delete(r)
    db.session.commit()
    flash('Recurring rule deleted.' + (' Associated entries removed.' if delete_entries else ''), 'success')
    # Preserve view
    today = date.today()
    y = request.args.get('y') or today.year
    m = request.args.get('m') or today.month
    sel = request.args.get('sel')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel, open='recurring'))


@main_bp.route('/expenses/settings', methods=['POST'])
def expenses_settings():
    user = sanitize_text(request.form.get('user', ''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    if user != admin_name:
        flash('Only admin can update settings.', 'error')
        return redirect(url_for('main.expenses'))
    currency = sanitize_text(request.form.get('currency', ''))
    categories = sanitize_text(request.form.get('categories', ''))
    try:
        db.session.execute(db.text("INSERT INTO app_setting(key,value) VALUES('currency', :v) ON CONFLICT(key) DO UPDATE SET value=excluded.value"), {"v": currency})
        db.session.execute(db.text("INSERT INTO app_setting(key,value) VALUES('categories', :v) ON CONFLICT(key) DO UPDATE SET value=excluded.value"), {"v": categories})
        db.session.commit()
        flash('Settings saved.', 'success')
    except Exception:
        flash('Failed to save settings.', 'error')
    today = date.today()
    y = request.args.get('y') or today.year
    m = request.args.get('m') or today.month
    sel = request.args.get('sel')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel, open='recurring'))


@main_bp.route('/api/expenses/month', methods=['GET'])
def api_expenses_month():
    # Keep recurring data up-to-date before answering
    today = date.today()
    _generate_recurring_entries_until(today)
    try:
        y = int(request.args.get('year') or today.year)
        m = int(request.args.get('month') or today.month)
        # Basic clamp for month values
        if m < 1 or m > 12:
            raise ValueError('month out of range')
    except Exception:
        y, m = today.year, today.month
    payload = _build_month_payload(y, m)
    return jsonify(payload)
