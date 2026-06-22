from flask import render_template, request, redirect, url_for, flash, jsonify, current_app
from datetime import datetime, date, timedelta
import calendar as _calendar
import json
import os
from ..models import db, RecurringExpense, ExpenseEntry
from ..security import sanitize_text
from ..blueprints import main_bp
from ..utils import handle_expense_attachment
import bleach


def _fraction_factor_precision(value) -> int:
    try:
        factor = int(value)
    except Exception:
        return 2
    if factor <= 1:
        return 0
    precision = 0
    while factor % 10 == 0:
        factor //= 10
        precision += 1
    return precision if factor == 1 else 2


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
                    is_paid=False,
                    payer=r.creator,
                    recurring_id=r.id
                ))
            r.last_generated_date = d
            d = next_date(d)
    db.session.commit()


def _load_expense_settings() -> dict:
    # Fallback default configuration
    settings = {'currency': 'Rp', 'categories': [], 'fraction_factor': 1, 'fraction_precision': 0}
    try:
        rows = db.session.execute(db.text("SELECT key, value FROM app_setting WHERE key IN ('currency','categories','fraction_factor')"))
        data = {k: v for k, v in rows}
        if data.get('currency'):
            settings['currency'] = data['currency']
        if data.get('categories'):
            settings['categories'] = [c.strip() for c in data['categories'].split(',') if c.strip()]
        if data.get('fraction_factor'):
            try:
                settings['fraction_factor'] = max(1, int(data['fraction_factor']))
            except Exception:
                settings['fraction_factor'] = 100
    except Exception:
        # Best-effort: fall back to default settings but log for diagnostics
        current_app.logger.exception('Failed to load expense settings; using defaults')
    settings['fraction_precision'] = _fraction_factor_precision(settings.get('fraction_factor'))
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
        amt = float(e.amount or 0)
        is_paid = bool(getattr(e, 'is_paid', True))
        if is_paid:
            by_date[ds]['total'] += amt
            total += amt
            per_payer[e.payer or ''] = per_payer.get(e.payer or '', 0.0) + amt
            if e.category:
                per_category[e.category] = per_category.get(e.category, 0.0) + amt
        by_date[ds]['entries'].append({
            'id': e.id,
            'title': e.title,
            'category': e.category,
            'unit_price': float(e.unit_price) if e.unit_price is not None else None,
            'amount': amt,
            'quantity': float(e.quantity or 0) if e.quantity is not None else None,
            'recurring_id': e.recurring_id,
            'is_paid': is_paid,
            'payer': e.payer or '',
            'attachment_path': e.attachment_path
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
    try:
        y = int(request.args.get('y') or today.year)
        m = int(request.args.get('m') or today.month)
    except Exception:
        y, m = today.year, today.month
        
    # Ensure recurring entries are generated up to the end of the viewed month
    import calendar
    last_day = calendar.monthrange(y, m)[1]
    target_date = date(y, m, last_day)
    _generate_recurring_entries_until(max(today, target_date))

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
            
            # Handle attachment
            attachment = request.files.get('attachment')
            upload_dir = os.path.abspath(os.path.join(current_app.root_path, '..', 'uploads'))
            attachment_path = handle_expense_attachment(attachment, upload_dir) if attachment else None
            
            db.session.add(RecurringExpense(title=title, unit_price=unit_price, default_quantity=default_quantity, frequency=frequency, monthly_mode=monthly_mode, category=category, start_date=sd, end_date=ed, creator=creator, effective_from=sd, attachment_path=attachment_path))
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
            
            # Handle attachment
            attachment = request.files.get('attachment')
            upload_dir = os.path.abspath(os.path.join(current_app.root_path, '..', 'uploads'))
            attachment_path = handle_expense_attachment(attachment, upload_dir) if attachment else None
            
            is_paid = request.form.get('is_paid') == 'on'
            db.session.add(ExpenseEntry(date=d, title=title, category=category, unit_price=up, quantity=q, amount=amount, payer=payer, attachment_path=attachment_path, is_paid=is_paid))
            db.session.commit()
            flash('Expense added.', 'success')
            y = request.args.get('y') or d.year
            m = request.args.get('m') or d.month
            sel = request.args.get('sel') or d.strftime('%Y-%m-%d')
            return redirect(url_for('main.expenses', y=y, m=m, sel=sel))

    payload = _build_month_payload(y, m)
    rules = RecurringExpense.query.order_by(RecurringExpense.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('expenses.html', rules=rules, config=config, expenses_json=json.dumps(payload), expense_settings=payload.get('settings') or {})


@main_bp.route('/expenses/recurring/edit/<int:rid>', methods=['POST'])
def edit_recurring_expense(rid):
    r = RecurringExpense.query.get_or_404(rid)
    user = sanitize_text(request.form.get('user', ''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if not (user in admin_aliases or user == (r.creator or '')):
        flash('Not allowed to edit rule.', 'error')
        return redirect(url_for('main.expenses'))
    def _parse_date(value, fallback):
        if value in (None, ''):
            return fallback
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except Exception:
            return fallback

    today = date.today()
    strategy = bleach.clean(request.form.get('edit_strategy', 'apply_from') or 'apply_from')
    if strategy not in {'apply_from', 'split_rule', 'rewrite_all'}:
        strategy = 'apply_from'

    new_title = bleach.clean(request.form.get('title', r.title))
    cat_raw = request.form.get('category')
    new_category = r.category
    if cat_raw is not None:
        cat_val = bleach.clean(cat_raw or '')
        new_category = cat_val or None
    up = request.form.get('unit_price')
    dq = request.form.get('default_quantity')
    new_unit_price = float(up) if up not in (None, '') else r.unit_price
    new_default_quantity = float(dq) if dq not in (None, '') else r.default_quantity
    new_frequency = bleach.clean(request.form.get('frequency', r.frequency) or r.frequency)
    if new_frequency not in {'daily', 'weekly', 'monthly'}:
        new_frequency = r.frequency
    new_monthly_mode = bleach.clean(request.form.get('monthly_mode', getattr(r, 'monthly_mode', 'day_of_month')) or 'day_of_month')
    if new_monthly_mode not in {'day_of_month', 'calendar'}:
        new_monthly_mode = getattr(r, 'monthly_mode', 'day_of_month') or 'day_of_month'
    new_start_date = _parse_date(request.form.get('start_date'), r.start_date)
    new_end_date = _parse_date(request.form.get('end_date'), r.end_date)

    # Handle attachment update
    attachment = request.files.get('attachment')
    if attachment and attachment.filename:
        # SQLAlchemy before_update will delete the old file automatically
        upload_dir = os.path.abspath(os.path.join(current_app.root_path, '..', 'uploads'))
        new_path = handle_expense_attachment(attachment, upload_dir)
        if new_path:
            r.attachment_path = new_path

    effective_from = _parse_date(request.form.get('effective_from'), today)
    # Effective date is meaningful only for apply/split strategies and should stay
    # inside the rule's active window.
    if strategy in {'apply_from', 'split_rule'}:
        lower_bound = new_start_date or r.start_date
        upper_bound = new_end_date or r.end_date
        if lower_bound and effective_from < lower_bound:
            effective_from = lower_bound
        if upper_bound and effective_from > upper_bound:
            effective_from = upper_bound

    if strategy == 'rewrite_all':
        r.title = new_title
        r.category = new_category
        r.unit_price = new_unit_price
        r.default_quantity = new_default_quantity
        r.frequency = new_frequency
        r.monthly_mode = new_monthly_mode
        r.start_date = new_start_date
        r.end_date = new_end_date
        r.effective_from = new_start_date

        deleted = 0
        if r.start_date:
            deleted += ExpenseEntry.query.filter(
                ExpenseEntry.recurring_id == r.id,
                ExpenseEntry.date < r.start_date
            ).delete(synchronize_session=False)
        if r.end_date:
            deleted += ExpenseEntry.query.filter(
                ExpenseEntry.recurring_id == r.id,
                ExpenseEntry.date > r.end_date
            ).delete(synchronize_session=False)
        updated = 0
        entries = ExpenseEntry.query.filter(ExpenseEntry.recurring_id == r.id).all()
        for e in entries:
            e.title = r.title
            e.category = r.category
            e.unit_price = r.unit_price
            e.quantity = r.default_quantity
            qty = r.default_quantity if r.default_quantity is not None else 1.0
            e.amount = (r.unit_price or 0.0) * qty
            updated += 1
        db.session.commit()
        flash(f'Recurring rule fully rewritten. Updated {updated} entry(ies), removed {deleted} outside rule range.', 'warning')
    elif strategy == 'split_rule':
        split_start = effective_from
        if r.start_date and split_start < r.start_date:
            split_start = r.start_date
        if r.start_date and split_start <= r.start_date:
            # Splitting at/before the current start creates an empty old window,
            # so safely fallback to apply-from behavior on the same rule.
            effective_from = r.start_date

            r.title = new_title
            r.category = new_category
            r.unit_price = new_unit_price
            r.default_quantity = new_default_quantity
            r.frequency = new_frequency
            r.monthly_mode = new_monthly_mode
            r.start_date = new_start_date
            r.end_date = new_end_date
            r.effective_from = effective_from

            ExpenseEntry.query.filter(
                ExpenseEntry.recurring_id == r.id,
                ExpenseEntry.date >= effective_from
            ).delete()
            r.last_generated_date = effective_from - timedelta(days=1)
            db.session.commit()
            _generate_recurring_entries_until(today)
            flash(
                f'Split at {split_start} would create an empty old rule window. Applied changes from {effective_from} on the same rule instead.',
                'info'
            )
            return redirect(url_for('main.recurring_expenses_page', tab='recurring-rules'))

        old_end = split_start - timedelta(days=1)
        if r.end_date and old_end > r.end_date:
            old_end = r.end_date
        r.end_date = old_end

        removed_from_old = ExpenseEntry.query.filter(ExpenseEntry.recurring_id == r.id, ExpenseEntry.date > old_end).count()
        ExpenseEntry.query.filter(ExpenseEntry.recurring_id == r.id, ExpenseEntry.date > old_end).delete()

        new_rule = RecurringExpense(
            title=new_title,
            category=new_category,
            unit_price=new_unit_price,
            default_quantity=new_default_quantity,
            frequency=new_frequency,
            monthly_mode=new_monthly_mode,
            start_date=max(new_start_date or split_start, split_start),
            end_date=new_end_date,
            last_generated_date=None,
            effective_from=split_start,
            creator=r.creator,
            attachment_path=r.attachment_path
        )
        db.session.add(new_rule)
        db.session.commit()
        _generate_recurring_entries_until(today)
        flash(f'Rule split from {split_start}. Old rule preserved; removed {removed_from_old} future old-rule entry(ies).', 'success')
    else:
        if new_start_date and effective_from < new_start_date:
            effective_from = new_start_date
        if new_end_date and effective_from > new_end_date:
            effective_from = new_end_date

        historical_kept = ExpenseEntry.query.filter(
            ExpenseEntry.recurring_id == r.id,
            ExpenseEntry.date < effective_from
        ).count()

        r.title = new_title
        r.category = new_category
        r.unit_price = new_unit_price
        r.default_quantity = new_default_quantity
        r.frequency = new_frequency
        r.monthly_mode = new_monthly_mode
        r.start_date = new_start_date
        r.end_date = new_end_date
        r.effective_from = effective_from

        removed_for_rebuild = ExpenseEntry.query.filter(
            ExpenseEntry.recurring_id == r.id,
            ExpenseEntry.date >= effective_from
        ).count()
        ExpenseEntry.query.filter(
            ExpenseEntry.recurring_id == r.id,
            ExpenseEntry.date >= effective_from
        ).delete()
        r.last_generated_date = effective_from - timedelta(days=1)
        db.session.commit()

        _generate_recurring_entries_until(today)
        regenerated = ExpenseEntry.query.filter(
            ExpenseEntry.recurring_id == r.id,
            ExpenseEntry.date >= effective_from
        ).count()
        flash(
            f'Rule updated from {effective_from}. Kept {historical_kept} historical entry(ies), rebuilt {regenerated} from that date.',
            'success'
        )

    return redirect(url_for('main.recurring_expenses_page', tab='recurring-rules'))


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
    if delete_entries:
        flash('Recurring rule deleted. Linked generated entries deleted.', 'success')
    else:
        flash('Recurring rule deleted. Linked generated entries kept as history.', 'success')
    return redirect(url_for('main.recurring_expenses_page', tab='recurring-rules'))



@main_bp.route('/expenses/settings', methods=['POST'])
def expenses_settings():
    user = sanitize_text(request.form.get('user', ''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    if user != admin_name:
        flash('Only admin can update settings.', 'error')
        return redirect(url_for('main.expenses'))
    currency = sanitize_text(request.form.get('currency', ''))
    categories = sanitize_text(request.form.get('categories', ''))
    fraction_factor_raw = sanitize_text(request.form.get('fraction_factor', '100'))
    try:
        fraction_factor = max(1, int(fraction_factor_raw or '100'))
    except Exception:
        fraction_factor = 100
    try:
        db.session.execute(db.text("INSERT INTO app_setting(key,value) VALUES('currency', :v) ON CONFLICT(key) DO UPDATE SET value=excluded.value"), {"v": currency})
        db.session.execute(db.text("INSERT INTO app_setting(key,value) VALUES('categories', :v) ON CONFLICT(key) DO UPDATE SET value=excluded.value"), {"v": categories})
        db.session.execute(db.text("INSERT INTO app_setting(key,value) VALUES('fraction_factor', :v) ON CONFLICT(key) DO UPDATE SET value=excluded.value"), {"v": str(fraction_factor)})
        db.session.commit()
        flash('Settings saved.', 'success')
    except Exception:
        flash('Failed to save settings.', 'error')
    today = date.today()
    tab = request.args.get('tab', 'general-settings')
    return redirect(url_for('main.recurring_expenses_page', tab=tab))



@main_bp.route('/expenses/delete/<int:entry_id>', methods=['POST'])
def delete_expense_entry(entry_id):
    entry = ExpenseEntry.query.get_or_404(entry_id)
    user = sanitize_text(request.form.get('user', ''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    family = current_app.config['HOMEHUB_CONFIG'].get('family_members', [])
    valid_users = admin_aliases | set(family)
    if user not in valid_users:
        flash('Not allowed to delete entry.', 'error')
        return redirect(url_for('main.expenses'))
    db.session.delete(entry)
    db.session.commit()
    flash('Expense deleted.', 'success')
    # Preserve view
    today = date.today()
    y = request.args.get('y') or today.year
    m = request.args.get('m') or today.month
    sel = request.args.get('sel')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))


@main_bp.route('/expenses/edit/<int:entry_id>', methods=['POST'])
def edit_expense_entry(entry_id):
    entry = ExpenseEntry.query.get_or_404(entry_id)
    user = sanitize_text(request.form.get('user', ''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    family = current_app.config['HOMEHUB_CONFIG'].get('family_members', [])
    valid_users = admin_aliases | set(family)
    if user not in valid_users:
        flash('Not allowed to edit entry.', 'error')
        return redirect(url_for('main.expenses'))
    # Update fields
    entry.title = bleach.clean(request.form.get('title', entry.title))
    date_s = request.form.get('date')
    if date_s:
        entry.date = datetime.strptime(date_s, '%Y-%m-%d').date()
    cat_raw = request.form.get('category')
    if cat_raw is not None:
        entry.category = bleach.clean(cat_raw or '') or None
    entry.payer = bleach.clean(request.form.get('payer', entry.payer or ''))
    up = request.form.get('unit_price')
    q = request.form.get('quantity')
    amt = request.form.get('amount')
    entry.unit_price = float(up) if up not in (None, '') else entry.unit_price
    entry.quantity = float(q) if q not in (None, '') else entry.quantity
    entry.amount = float(amt) if amt not in (None, '') else entry.amount
    entry.is_paid = request.form.get('is_paid') == 'on'
    
    # Handle attachment update
    attachment = request.files.get('attachment')
    if attachment and attachment.filename:
        # SQLAlchemy before_update will delete the old file automatically
        upload_dir = os.path.abspath(os.path.join(current_app.root_path, '..', 'uploads'))
        new_path = handle_expense_attachment(attachment, upload_dir)
        if new_path:
            entry.attachment_path = new_path
            
    db.session.commit()
    flash('Expense updated.', 'success')
    # Preserve view
    today = date.today()
    y = request.args.get('y') or entry.date.year
    m = request.args.get('m') or entry.date.month
    sel = request.args.get('sel') or entry.date.strftime('%Y-%m-%d')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))


@main_bp.route('/expenses/bulk-delete', methods=['POST'])
def bulk_delete_expenses():
    user = sanitize_text(request.form.get('user', ''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    family = current_app.config['HOMEHUB_CONFIG'].get('family_members', [])
    valid_users = admin_aliases | set(family)
    ids = request.form.getlist('ids')
    if not ids:
        flash('No entries selected.', 'warning')
        return redirect(url_for('main.expenses'))
    deleted = 0
    for entry_id in ids:
        try:
            entry = ExpenseEntry.query.get(int(entry_id))
            if entry and user in valid_users:
                db.session.delete(entry)
                deleted += 1
        except Exception:
            continue
    db.session.commit()
    flash(f'{deleted} expense(s) deleted.', 'success')
    # Preserve view
    today = date.today()
    y = request.args.get('y') or today.year
    m = request.args.get('m') or today.month
    sel = request.args.get('sel')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))


@main_bp.route('/api/expenses/month', methods=['GET'])
def api_expenses_month():
    today = date.today()
    # Parse query params
    y, m = today.year, today.month
    year_q = request.args.get('year')
    month_q = request.args.get('month')
    try:
        if year_q is not None:
            y = int(year_q)
        if month_q is not None:
            m = int(month_q)
    except (ValueError, TypeError):
        current_app.logger.warning('Invalid year/month query params', extra={'year': year_q, 'month': month_q})
        # Keep defaults y, m
    # Validate month range; return helpful 400 if invalid
    if m < 1 or m > 12:
        return jsonify({
            'error': 'Invalid month parameter. Must be an integer between 1 and 12.',
            'year': year_q if year_q is not None else y,
            'month': month_q if month_q is not None else m
        }), 400

    # Generate recurring entries up to the end of the requested month
    import calendar
    last_day = calendar.monthrange(y, m)[1]
    target_date = date(y, m, last_day)
    _generate_recurring_entries_until(max(today, target_date))

    payload = _build_month_payload(y, m)
    return jsonify(payload)


@main_bp.route('/expenses/recurring', methods=['GET'])
def recurring_expenses_page():
    """Dedicated page for managing recurring expense rules and settings."""
    today = date.today()
    _generate_recurring_entries_until(today)
    
    rules = RecurringExpense.query.order_by(RecurringExpense.timestamp.desc()).all()
    expense_settings = _load_expense_settings()
    config = current_app.config['HOMEHUB_CONFIG']
    
    # If query param open=tab is provided, pass tab selection to template
    active_tab = request.args.get('tab', 'recurring-rules')
    if active_tab not in {'recurring-rules', 'general-settings'}:
        active_tab = 'recurring-rules'
    
    return render_template(
        'expenses_recurring.html',
        rules=rules,
        config=config,
        expense_settings=expense_settings,
        active_tab=active_tab
    )
