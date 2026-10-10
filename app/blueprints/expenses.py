from flask import render_template, request, redirect, url_for, flash, jsonify, current_app
from datetime import datetime, date, timedelta
import calendar as _calendar
import json
import math
from ..models import db, RecurringExpense, ExpenseEntry, parse_split
from ..security import sanitize_text
from ..blueprints import main_bp
from ..admin import is_admin, can_modify
from ..recurrence import serialized
from ..undo import offer_undo, restore_row, restorer, row_to_dict, same_record, stamp
import bleach
from flask_babel import gettext as _, ngettext


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


def _rule_next_date(r: RecurringExpense, d: date, today: date) -> date:
    if r.frequency == 'daily':
        return d + timedelta(days=1)
    if r.frequency == 'weekly':
        return d + timedelta(weeks=1)
    ny = d.year + (1 if d.month == 12 else 0)
    nm = 1 if d.month == 12 else d.month + 1
    mode = getattr(r, 'monthly_mode', 'day_of_month') or 'day_of_month'
    if mode == 'calendar':
        return date(ny, nm, 1)
    base_day = (r.start_date or today).day
    last_dom = _calendar.monthrange(ny, nm)[1]
    return date(ny, nm, min(base_day, last_dom))


def _rule_first_date(r: RecurringExpense, today: date) -> date:
    start = r.start_date or today
    if r.frequency == 'monthly':
        mode = getattr(r, 'monthly_mode', 'day_of_month') or 'day_of_month'
        if mode == 'calendar' and start.day != 1:
            ny = start.year + (1 if start.month == 12 else 0)
            nm = 1 if start.month == 12 else start.month + 1
            return date(ny, nm, 1)
    return start


def _rule_occurrences(r: RecurringExpense, until: date, today: date | None = None):
    """Yield every scheduled date of a rule from its start up to `until` (inclusive)."""
    today = today or date.today()
    d = _rule_first_date(r, today)
    while d <= until and (not r.end_date or d <= r.end_date):
        yield d
        d = _rule_next_date(r, d, today)


def _rule_occurs_on(r: RecurringExpense, target: date) -> bool:
    return any(d == target for d in _rule_occurrences(r, target))


@serialized
def _generate_recurring_entries_until(today: date | None = None) -> None:
    today = today or date.today()
    recs = RecurringExpense.query.all()
    for r in recs:
        # Generate from rule start date; don't clamp to effective_from so rule owns entire range
        start = r.start_date or today
        last = r.last_generated_date
        if last is None or (last and last < start):
            d = _rule_first_date(r, today)
        else:
            d = _rule_next_date(r, last, today)

        while d <= today and (not r.end_date or d <= r.end_date):
            exists = ExpenseEntry.query.filter_by(date=d, recurring_id=r.id).first()
            if not exists:
                db.session.add(_entry_from_rule(r, d))
            r.last_generated_date = d
            d = _rule_next_date(r, d, today)
    db.session.commit()


def _entry_from_rule(r: RecurringExpense, d: date) -> ExpenseEntry:
    qty = r.default_quantity or 1.0
    return ExpenseEntry(
        date=d,
        title=r.title,
        category=getattr(r, 'category', None),
        unit_price=r.unit_price,
        quantity=qty,
        amount=(r.unit_price or 0.0) * qty,
        payer=r.creator,
        recurring_id=r.id,
        split_with=getattr(r, 'split_with', None),
    )


def _skipped_dates(rule_id: int, after: date) -> set:
    """Dates of skipped entries for a rule on or after `after`, so rebuilds can keep them skipped."""
    rows = ExpenseEntry.query.filter(
        ExpenseEntry.recurring_id == rule_id,
        ExpenseEntry.date >= after,
        ExpenseEntry.skipped.is_(True),
    ).all()
    return {e.date for e in rows}


def _reapply_skips(rule_id: int, dates: set) -> None:
    if not dates:
        return
    ExpenseEntry.query.filter(
        ExpenseEntry.recurring_id == rule_id,
        ExpenseEntry.date.in_(list(dates)),
    ).update({ExpenseEntry.skipped: True}, synchronize_session=False)
    db.session.commit()


def _parse_split(raw) -> list:
    return list(parse_split(raw)[1])


def _split_shares(amount: float, weights: dict, precision: int = 2, payer: str = '') -> dict:
    """Each member's part of `amount`, in proportion to their weight.

    Parts are whole currency units (per `precision`) that add up to `amount` exactly,
    so settling the displayed figures never leaves a stray 0.01 behind. The payer
    absorbs the rounding difference when they share the expense.
    """
    total = sum(weights.values())
    if total <= 0:
        return {}
    factor = 10 ** precision
    units = {m: round(amount * factor * w / total) for m, w in weights.items()}
    diff = round(amount * factor) - sum(units.values())
    if diff and payer in units:
        units[payer] += diff
    elif diff:
        step = 1 if diff > 0 else -1
        for m in list(units)[:abs(diff)]:
            units[m] += step
    return {m: u / factor for m, u in units.items()}


def _split_people(config) -> list:
    """Everyone who can pay or share: family members plus the admin."""
    people = list(config.get('family_members') or [])
    admin = config.get('admin_name')
    if people and admin and admin not in people:
        people.append(admin)
    return people


def _all_finite(*values) -> bool:
    """False when any given number is NaN or infinite (None is allowed)."""
    return all(v is None or math.isfinite(v) for v in values)


def _amount_problem(amount, quantity=None, unit_price=None, check_amount=True) -> str | None:
    """Why these numbers cannot be saved, or None. Older rows may hold zeros; they are fixed when next saved."""
    if unit_price is not None and unit_price <= 0:
        return _('Unit price must be greater than zero.')
    if quantity is not None and quantity <= 0:
        return _('Quantity must be greater than zero.')
    if check_amount and (amount is None or amount <= 0):
        return _('Amount must be greater than zero.')
    return None


class SplitError(ValueError):
    """A submitted split that breaks its mode's rules."""


def _split_from_form(form, total: float | None = None):
    """Members ticked under "Split with", as stored JSON (None when not shared).

    Uneven splits must add up the way their mode says (percentages to 100, amounts
    to `total`); anything else raises SplitError rather than being reshaped silently.
    """
    members = {}
    for raw in form.getlist('split_with'):
        name = sanitize_text(raw or '').strip()
        if name and name not in members:
            members[name] = raw
    if not members:
        return None
    mode = form.get('split_mode') or 'equal'
    if mode not in ('shares', 'percent', 'amount'):
        return json.dumps(list(members))
    weights = {}
    for name, raw in members.items():
        try:
            w = float(form.get(f'split_weight__{raw}') or 0)
        except (TypeError, ValueError):
            raise SplitError(_('Invalid split value for %(name)s.', name=name))
        if not math.isfinite(w) or w < 0:
            raise SplitError(_('Invalid split value for %(name)s.', name=name))
        if w > 0:
            weights[name] = w
    if not weights:
        raise SplitError(_('Enter a split value for at least one person.'))
    entered = sum(weights.values())
    if mode == 'percent' and abs(entered - 100) > 0.01:
        raise SplitError(_('Percentages add up to %(entered)s%%, not 100%%.', entered=f'{entered:g}'))
    if mode == 'amount':
        precision = _load_expense_settings().get('fraction_precision', 2)
        if total is None or not math.isfinite(total) or abs(entered - total) > 0.5 / (10 ** precision):
            raise SplitError(_('Split amounts must add up to the expense amount.'))
    return json.dumps({'mode': mode, 'weights': weights})


def _compute_balances(precision: int = 2) -> dict:
    """All-time net balance per member and a short list of payments that settles everyone up.

    Shared expenses credit the payer and charge each member their part of the split.
    Settlements credit the payer and debit the recipient (split_with[0]).
    """
    net: dict[str, float] = {}
    rows = ExpenseEntry.query.filter(
        (ExpenseEntry.skipped.is_(None)) | (ExpenseEntry.skipped.is_(False)),
        ExpenseEntry.split_with.isnot(None),
    ).all()
    for e in rows:
        weights = parse_split(e.split_with)[1]
        members = list(weights)
        amount = float(e.amount or 0)
        payer = e.payer or ''
        if not members or not payer or amount == 0:
            continue
        net[payer] = net.get(payer, 0.0) + amount
        if e.is_settlement:
            net[members[0]] = net.get(members[0], 0.0) - amount
        else:
            for m, share in _split_shares(amount, weights, precision, payer).items():
                net[m] = net.get(m, 0.0) - share

    eps = 0.5 / (10 ** precision)
    creditors = sorted(((v, k) for k, v in net.items() if v > eps), reverse=True)
    debtors = sorted(((-v, k) for k, v in net.items() if v < -eps), reverse=True)
    creditors = [[v, k] for v, k in creditors]
    debtors = [[v, k] for v, k in debtors]
    settlements = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        pay = min(debtors[i][0], creditors[j][0])
        settlements.append({'from': debtors[i][1], 'to': creditors[j][1], 'amount': round(pay, precision)})
        debtors[i][0] -= pay
        creditors[j][0] -= pay
        if debtors[i][0] <= eps:
            i += 1
        if creditors[j][0] <= eps:
            j += 1
    return {
        'net': {k: round(v, precision) for k, v in sorted(net.items()) if abs(v) > eps},
        'settlements': settlements,
    }


def _missing_recurring_days(month_start: date, month_end: date, today: date) -> dict:
    """Scheduled recurring days in the month whose entry was deleted, so they can be added back."""
    missing: dict[str, list] = {}
    for r in RecurringExpense.query.all():
        lower = max(month_start, r.effective_from or r.start_date or month_start)
        upper = min(month_end, today)
        if r.last_generated_date:
            upper = min(upper, r.last_generated_date)
        if upper < lower:
            continue
        existing = {
            e.date for e in ExpenseEntry.query.filter(
                ExpenseEntry.recurring_id == r.id,
                ExpenseEntry.date >= lower,
                ExpenseEntry.date <= upper,
            ).all()
        }
        for d in _rule_occurrences(r, upper, today):
            if d >= lower and d not in existing:
                missing.setdefault(d.strftime('%Y-%m-%d'), []).append({'rule_id': r.id, 'title': r.title, 'creator': r.creator or ''})
    return missing


def _load_expense_settings() -> dict:
    settings = {'currency': '\u20b9', 'categories': [], 'fraction_factor': 100, 'fraction_precision': 2}
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
    # What each person actually bears after splits (unshared expenses stay with the payer)
    per_share: dict[str, float] = {}
    settings = _load_expense_settings()
    precision = settings.get('fraction_precision', 2)
    for e in q_entries:
        ds = e.date.strftime('%Y-%m-%d')
        by_date.setdefault(ds, {'total': 0.0, 'entries': []})
        split_mode, split_weights = parse_split(e.split_with)
        shares = {} if e.is_settlement else _split_shares(float(e.amount or 0), split_weights, precision, e.payer or '')
        # Skipped days and settlements are shown but never count as spending
        if not e.skipped and not e.is_settlement:
            amt = float(e.amount or 0)
            by_date[ds]['total'] += amt
            total += amt
            per_payer[e.payer or ''] = per_payer.get(e.payer or '', 0.0) + amt
            for name, part in (shares or {e.payer or '': amt}).items():
                per_share[name] = per_share.get(name, 0.0) + part
            if e.category:
                per_category[e.category] = per_category.get(e.category, 0.0) + amt
        by_date[ds]['entries'].append({
            'id': e.id,
            'title': e.title,
            'category': e.category,
            'unit_price': float(e.unit_price) if e.unit_price is not None else None,
            'amount': float(e.amount or 0),
            'quantity': float(e.quantity or 0) if e.quantity is not None else None,
            'recurring_id': e.recurring_id,
            'payer': e.payer or '',
            'skipped': bool(e.skipped),
            'split_with': list(split_weights),
            'split_mode': split_mode,
            'split_weights': split_weights,
            'shares': {name: round(part, precision) for name, part in shares.items()},
            'is_settlement': bool(e.is_settlement),
        })

    top_category = None
    if per_category:
        top_category = max(per_category.items(), key=lambda kv: kv[1])[0]

    payload = {
        'by_date': by_date,
        'missing_recurring': _missing_recurring_days(month_start, month_end, date.today()),
        'balances': _compute_balances(precision),
        'summary': {
            'total_this_month': total,
            'per_payer': per_payer,
            'per_share': per_share,
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
            y = request.args.get('y') or today.year
            m = request.args.get('m') or today.month
            sel = request.args.get('sel')
            if not _all_finite(unit_price, default_quantity):
                flash(_('Invalid amount.'), 'error')
                return redirect(url_for('main.expenses', y=y, m=m, sel=sel))
            problem = _amount_problem(None, default_quantity, unit_price, check_amount=False)
            if problem:
                flash(problem, 'error')
                return redirect(url_for('main.recurring_expenses_page', tab='recurring-rules'))
            try:
                split_with = _split_from_form(request.form, unit_price * default_quantity)
            except SplitError as exc:
                flash(str(exc), 'error')
                return redirect(url_for('main.expenses', y=y, m=m, sel=sel))
            db.session.add(RecurringExpense(title=title, unit_price=unit_price, default_quantity=default_quantity, frequency=frequency, monthly_mode=monthly_mode, category=category, start_date=sd, end_date=ed, creator=creator, effective_from=sd, split_with=split_with))
            db.session.commit()
            flash(_('Recurring expense added.'), 'success')
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
            if not amount and up is not None:
                # A blank quantity means one unit, not zero
                amount = up * (q if q is not None else 1)
            y = request.args.get('y') or d.year
            m = request.args.get('m') or d.month
            sel = request.args.get('sel') or d.strftime('%Y-%m-%d')
            if not _all_finite(amount, up, q):
                flash(_('Invalid amount.'), 'error')
                return redirect(url_for('main.expenses', y=y, m=m, sel=sel))
            problem = _amount_problem(amount, q)
            if problem:
                flash(problem, 'error')
                return redirect(url_for('main.expenses', y=y, m=m, sel=sel))
            try:
                split_with = _split_from_form(request.form, amount)
            except SplitError as exc:
                flash(str(exc), 'error')
                return redirect(url_for('main.expenses', y=y, m=m, sel=sel))
            db.session.add(ExpenseEntry(date=d, title=title, category=category, unit_price=up, quantity=q, amount=amount, payer=payer, split_with=split_with))
            db.session.commit()
            flash(_('Expense added.'), 'success')
            return redirect(url_for('main.expenses', y=y, m=m, sel=sel))

    try:
        y = int(request.args.get('y') or today.year)
        m = int(request.args.get('m') or today.month)
    except Exception:
        y, m = today.year, today.month

    payload = _build_month_payload(y, m)
    rules = RecurringExpense.query.order_by(RecurringExpense.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('expenses.html', rules=rules, config=config, split_people=_split_people(config), expenses_json=json.dumps(payload), expense_settings=payload.get('settings') or {})


_RULE_FIELDS = ('title', 'category', 'unit_price', 'default_quantity', 'frequency', 'monthly_mode',
                'start_date', 'end_date', 'split_with')


def _parse_rule_edit(r: RecurringExpense, form, today: date) -> dict:
    """Read a rule edit form into its strategy, effective date and new values. Raises SplitError/ValueError."""
    def _parse_date(value, fallback):
        if value in (None, ''):
            return fallback
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except Exception:
            return fallback

    strategy = bleach.clean(form.get('edit_strategy', 'apply_from') or 'apply_from')
    if strategy not in {'apply_from', 'split_rule', 'rewrite_all'}:
        strategy = 'apply_from'

    new_title = bleach.clean(form.get('title', r.title))
    cat_raw = form.get('category')
    new_category = r.category
    if cat_raw is not None:
        cat_val = bleach.clean(cat_raw or '')
        new_category = cat_val or None
    up = form.get('unit_price')
    dq = form.get('default_quantity')
    new_unit_price = float(up) if up not in (None, '') else r.unit_price
    new_default_quantity = float(dq) if dq not in (None, '') else r.default_quantity
    new_frequency = bleach.clean(form.get('frequency', r.frequency) or r.frequency)
    if new_frequency not in {'daily', 'weekly', 'monthly'}:
        new_frequency = r.frequency
    new_monthly_mode = bleach.clean(form.get('monthly_mode', getattr(r, 'monthly_mode', 'day_of_month')) or 'day_of_month')
    if new_monthly_mode not in {'day_of_month', 'calendar'}:
        new_monthly_mode = getattr(r, 'monthly_mode', 'day_of_month') or 'day_of_month'
    new_start_date = _parse_date(form.get('start_date'), r.start_date)
    new_end_date = _parse_date(form.get('end_date'), r.end_date)

    if not _all_finite(new_unit_price, new_default_quantity):
        raise ValueError(_('Invalid amount.'))
    problem = _amount_problem(None, new_default_quantity, new_unit_price, check_amount=False)
    if problem:
        raise ValueError(problem)
    if form.get('split_present'):
        qty = new_default_quantity if new_default_quantity is not None else 1.0
        new_split_with = _split_from_form(form, (new_unit_price or 0.0) * qty)
    else:
        new_split_with = getattr(r, 'split_with', None)
    effective_from = _parse_date(form.get('effective_from'), today)
    # Effective date is meaningful only for apply/split strategies and should stay
    # inside the rule's active window.
    if strategy in {'apply_from', 'split_rule'}:
        lower_bound = new_start_date or r.start_date
        upper_bound = new_end_date or r.end_date
        if lower_bound and effective_from < lower_bound:
            effective_from = lower_bound
        if upper_bound and effective_from > upper_bound:
            effective_from = upper_bound

    split_fallback = False
    if strategy == 'split_rule' and r.start_date and effective_from <= r.start_date:
        # Splitting at/before the current start creates an empty old window,
        # so safely fallback to apply-from behavior on the same rule.
        split_fallback = True
        strategy = 'apply_from'
        effective_from = r.start_date
    elif strategy == 'apply_from':
        if new_start_date and effective_from < new_start_date:
            effective_from = new_start_date
        if new_end_date and effective_from > new_end_date:
            effective_from = new_end_date

    return {
        'strategy': strategy,
        'split_fallback': split_fallback,
        'effective_from': effective_from,
        'values': {
            'title': new_title,
            'category': new_category,
            'unit_price': new_unit_price,
            'default_quantity': new_default_quantity,
            'frequency': new_frequency,
            'monthly_mode': new_monthly_mode,
            'start_date': new_start_date,
            'end_date': new_end_date,
            'split_with': new_split_with,
        },
    }


def _last_occurrence_before(r: RecurringExpense, d: date, today: date) -> date | None:
    """The rule's last scheduled date before ``d``, so generation resumes on its own schedule."""
    last = None
    for occurrence in _rule_occurrences(r, d - timedelta(days=1), today):
        last = occurrence
    return last


def _same_number(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) < 1e-9


def _entry_differs(e: ExpenseEntry, rule: RecurringExpense, with_payer: bool = True) -> bool:
    """True when the entry is not what ``rule`` would generate for that day."""
    expected = _entry_from_rule(rule, e.date)
    if (e.title or '') != (expected.title or '') or (e.category or None) != (expected.category or None):
        return True
    if not (_same_number(e.unit_price, expected.unit_price) and _same_number(e.quantity, expected.quantity)
            and _same_number(e.amount, expected.amount)):
        return True
    if parse_split(e.split_with) != parse_split(expected.split_with):
        return True
    return with_payer and (e.payer or '') != (expected.payer or '')


def _is_hand_edited(e: ExpenseEntry, r: RecurringExpense) -> bool:
    """Entries since the rule's last edit that no longer match it were changed by hand."""
    since = r.effective_from or r.start_date
    if since and e.date < since:
        return False
    return _entry_differs(e, r)


def _preview_rule_edit(r: RecurringExpense, edit: dict, today: date) -> dict:
    """Count what saving ``edit`` would do to the entries this rule already generated."""
    values = edit['values']
    strategy = edit['strategy']
    effective_from = edit['effective_from']
    target = RecurringExpense(creator=r.creator, **values)
    entries = ExpenseEntry.query.filter(ExpenseEntry.recurring_id == r.id).order_by(ExpenseEntry.date.asc()).all()
    changed, removed, added = [], [], []
    if strategy == 'rewrite_all':
        for e in entries:
            if (target.start_date and e.date < target.start_date) or (target.end_date and e.date > target.end_date):
                removed.append(e)
            elif _entry_differs(e, target, with_payer=False):
                changed.append(e)
    else:
        if strategy == 'split_rule':
            target.start_date = max(target.start_date or effective_from, effective_from)
        scheduled = {d for d in _rule_occurrences(target, today, today) if d >= effective_from}
        existing_dates = set()
        for e in entries:
            if e.date < effective_from:
                continue
            existing_dates.add(e.date)
            if e.date not in scheduled:
                removed.append(e)
            elif _entry_differs(e, target):
                changed.append(e)
        added = sorted(scheduled - existing_dates)
    touched = changed + removed
    dates = [e.date for e in touched] + list(added)
    return {
        'strategy': strategy,
        'split_fallback': edit['split_fallback'],
        'effective_from': effective_from,
        'changed': len(changed),
        'removed': len(removed),
        'added': len(added),
        'hand_edited': sum(1 for e in touched if _is_hand_edited(e, r)),
        'past': sum(1 for d in dates if d < today),
        'first_date': min(dates) if dates else None,
    }


def _preview_messages(preview: dict) -> tuple[str, list]:
    total = preview['changed'] + preview['removed'] + preview['added']
    details = []
    if not total and preview['strategy'] == 'rewrite_all':
        summary = _('No existing entries will change.')
    elif not total:
        summary = _('These changes apply from %(date)s. Entries before that date stay as they are.',
                    date=preview['effective_from'])
    else:
        summary = ngettext('This will change %(num)s entry from %(date)s.',
                           'This will change %(num)s entries from %(date)s.',
                           total, date=preview['first_date'])
        if preview['removed']:
            details.append(ngettext('%(num)s entry will be removed.', '%(num)s entries will be removed.', preview['removed']))
        if preview['added']:
            details.append(ngettext('%(num)s entry will be added.', '%(num)s entries will be added.', preview['added']))
        if preview['hand_edited']:
            details.append(ngettext('%(num)s of them was edited by hand; that edit will be lost.',
                                    '%(num)s of them were edited by hand; those edits will be lost.',
                                    preview['hand_edited']))
    if preview['strategy'] == 'split_rule':
        details.append(_('The current rule will end on %(end)s and a new rule will start on %(start)s.',
                         end=preview['effective_from'] - timedelta(days=1), start=preview['effective_from']))
    return summary, details


def _rule_snapshot(r: RecurringExpense) -> dict:
    """The rule and every entry it generated, for undo."""
    entries = ExpenseEntry.query.filter(ExpenseEntry.recurring_id == r.id).all()
    return {'rule': row_to_dict(r), 'entries': [row_to_dict(e) for e in entries], 'new_rule_id': None}


@restorer('expense_rule')
@serialized
def _restore_rule_snapshot(payload: dict) -> None:
    rule_data = payload['rule']
    rid = rule_data['id']
    # If another rule has taken this id since the delete, its entries are not ours to remove
    current = db.session.get(RecurringExpense, rid)
    doomed = [rid] if current is None or same_record(current, rule_data) else []
    successor = db.session.get(RecurringExpense, payload['new_rule_id']) if payload.get('new_rule_id') else None
    if successor is not None and stamp(successor) == payload.get('new_rule_timestamp'):
        doomed.append(successor.id)
        db.session.delete(successor)
    if doomed:
        ExpenseEntry.query.filter(ExpenseEntry.recurring_id.in_(doomed)).delete(synchronize_session=False)
    db.session.flush()
    db.session.expire_all()
    rule = restore_row(RecurringExpense, rule_data)
    for data in payload['entries']:
        restore_row(ExpenseEntry, data, recurring_id=rule.id)


def _rule_edit_from_request(rid):
    """(rule, parsed edit, error message) shared by the edit route and its preview."""
    r = db.get_or_404(RecurringExpense, rid)
    try:
        return r, _parse_rule_edit(r, request.form, date.today()), None
    except (SplitError, ValueError) as exc:
        return r, None, str(exc)


@main_bp.route('/expenses/recurring/edit/<int:rid>/preview', methods=['POST'])
def preview_recurring_expense_edit(rid):
    """Dry run of a rule edit: what it would do to entries that already exist. Changes nothing."""
    today = date.today()
    _generate_recurring_entries_until(today)
    r, edit, error = _rule_edit_from_request(rid)
    if not can_modify(sanitize_text(request.form.get('user', '')), r.creator or ''):
        return jsonify({'ok': False, 'error': _('Not allowed to edit rule.')}), 403
    if error:
        return jsonify({'ok': False, 'error': error}), 400
    preview = _preview_rule_edit(r, edit, today)
    summary, details = _preview_messages(preview)
    preview.update(ok=True, summary=summary, details=details)
    for key in ('effective_from', 'first_date'):
        preview[key] = preview[key].strftime('%Y-%m-%d') if preview[key] else None
    return jsonify(preview)


@main_bp.route('/expenses/recurring/edit/<int:rid>', methods=['POST'])
def edit_recurring_expense(rid):
    r, edit, error = _rule_edit_from_request(rid)
    if not can_modify(sanitize_text(request.form.get('user', '')), r.creator or ''):
        flash(_('Not allowed to edit rule.'), 'error')
        return redirect(url_for('main.expenses'))
    if error:
        flash(error, 'error')
        return redirect(url_for('main.recurring_expenses_page', tab='recurring-rules'))

    today = date.today()
    strategy = edit['strategy']
    effective_from = edit['effective_from']
    values = edit['values']
    snapshot = _rule_snapshot(r)

    if strategy == 'rewrite_all':
        for name in _RULE_FIELDS:
            setattr(r, name, values[name])
        r.effective_from = r.start_date

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
            e.split_with = r.split_with
            updated += 1
        db.session.commit()
        message = ngettext('Recurring rule fully rewritten. Updated %(num)s entry, removed %(deleted)s outside rule range.',
                           'Recurring rule fully rewritten. Updated %(num)s entries, removed %(deleted)s outside rule range.',
                           updated, deleted=deleted)
        category = 'warning'
    elif strategy == 'split_rule':
        split_start = effective_from
        old_end = split_start - timedelta(days=1)
        if r.end_date and old_end > r.end_date:
            old_end = r.end_date
        r.end_date = old_end

        skipped = _skipped_dates(r.id, old_end + timedelta(days=1))
        removed_from_old = ExpenseEntry.query.filter(ExpenseEntry.recurring_id == r.id, ExpenseEntry.date > old_end).count()
        ExpenseEntry.query.filter(ExpenseEntry.recurring_id == r.id, ExpenseEntry.date > old_end).delete()

        new_rule = RecurringExpense(creator=r.creator, last_generated_date=None, effective_from=split_start, **values)
        new_rule.start_date = max(values['start_date'] or split_start, split_start)
        db.session.add(new_rule)
        db.session.commit()
        snapshot['new_rule_id'] = new_rule.id
        snapshot['new_rule_timestamp'] = stamp(new_rule)
        _generate_recurring_entries_until(today)
        _reapply_skips(new_rule.id, skipped)
        message = ngettext('Rule split from %(split_start)s. Old rule preserved; removed %(num)s future old-rule entry.',
                           'Rule split from %(split_start)s. Old rule preserved; removed %(num)s future old-rule entries.',
                           removed_from_old, split_start=split_start)
        category = 'success'
    else:
        historical_kept = ExpenseEntry.query.filter(
            ExpenseEntry.recurring_id == r.id,
            ExpenseEntry.date < effective_from
        ).count()

        for name in _RULE_FIELDS:
            setattr(r, name, values[name])
        r.effective_from = effective_from

        skipped = _skipped_dates(r.id, effective_from)
        ExpenseEntry.query.filter(
            ExpenseEntry.recurring_id == r.id,
            ExpenseEntry.date >= effective_from
        ).delete()
        # Resume on the rule's own schedule, so a weekly or monthly rule keeps its day
        resume_after = _last_occurrence_before(r, effective_from, today)
        r.last_generated_date = resume_after or effective_from - timedelta(days=1)
        db.session.commit()

        _generate_recurring_entries_until(today)
        _reapply_skips(r.id, skipped)
        if edit['split_fallback']:
            message = _('Split at %(split_start)s would create an empty old rule window. Applied changes from %(effective_from)s on the same rule instead.',
                        split_start=effective_from, effective_from=effective_from)
            category = 'info'
        else:
            regenerated = ExpenseEntry.query.filter(
                ExpenseEntry.recurring_id == r.id,
                ExpenseEntry.date >= effective_from
            ).count()
            message = ngettext('Rule updated from %(effective_from)s. Kept %(num)s historical entry, rebuilt %(rebuilt)s from that date.',
                               'Rule updated from %(effective_from)s. Kept %(num)s historical entries, rebuilt %(rebuilt)s from that date.',
                               historical_kept, effective_from=effective_from, rebuilt=regenerated)
            category = 'success'

    offer_undo('expense_rule', snapshot, message, category)
    return redirect(url_for('main.recurring_expenses_page', tab='recurring-rules'))


def _preview_rule_delete(r: RecurringExpense, delete_entries: bool) -> dict:
    entries = ExpenseEntry.query.filter(ExpenseEntry.recurring_id == r.id).order_by(ExpenseEntry.date.asc()).all()
    count = len(entries)
    hand_edited = sum(1 for e in entries if _is_hand_edited(e, r)) if delete_entries else 0
    details = []
    if not count:
        summary = _('It has no generated entries.')
    elif delete_entries:
        summary = ngettext('This also deletes its %(num)s generated entry (%(first)s to %(last)s).',
                           'This also deletes its %(num)s generated entries (%(first)s to %(last)s).',
                           count, first=entries[0].date, last=entries[-1].date)
        if hand_edited:
            details.append(ngettext('%(num)s of them was edited by hand.', '%(num)s of them were edited by hand.', hand_edited))
    else:
        summary = ngettext('Its %(num)s generated entry stays as history.',
                           'Its %(num)s generated entries stay as history.', count)
    return {
        'ok': True,
        'entries': count,
        'removed': count if delete_entries else 0,
        'hand_edited': hand_edited,
        'summary': summary,
        'details': details,
    }


@main_bp.route('/expenses/recurring/delete/<int:rid>/preview', methods=['POST'])
def preview_recurring_expense_delete(rid):
    """Dry run of a rule delete: how many generated entries go with it. Changes nothing."""
    r = db.get_or_404(RecurringExpense, rid)
    user = sanitize_text(request.form.get('user', ''))
    if not can_modify(user, r.creator or ''):
        return jsonify({'ok': False, 'error': _('Not allowed to delete rule.')}), 403
    delete_entries = request.form.get('delete_entries') in ('1', 'true', 'on', 'yes')
    return jsonify(_preview_rule_delete(r, delete_entries))


@main_bp.route('/expenses/recurring/delete/<int:rid>', methods=['POST'])
def delete_recurring_expense(rid):
    r = db.get_or_404(RecurringExpense, rid)
    user = sanitize_text(request.form.get('user', ''))
    if not can_modify(user, r.creator or ''):
        flash(_('Not allowed to delete rule.'), 'error')
        return redirect(url_for('main.expenses'))
    delete_entries = request.form.get('delete_entries') in ('1', 'true', 'on', 'yes')
    snapshot = _rule_snapshot(r)
    if delete_entries:
        try:
            ExpenseEntry.query.filter_by(recurring_id=r.id).delete()
        except Exception:
            pass
    else:
        # Kept as plain history: left pointing at this id, they would attach themselves to
        # whichever rule SQLite gives the id to next
        ExpenseEntry.query.filter_by(recurring_id=r.id).update({ExpenseEntry.recurring_id: None})
    db.session.delete(r)
    db.session.commit()
    if delete_entries:
        message = _('Recurring rule deleted. Linked generated entries deleted.')
    else:
        message = _('Recurring rule deleted. Linked generated entries kept as history.')
    offer_undo('expense_rule', snapshot, message)
    return redirect(url_for('main.recurring_expenses_page', tab='recurring-rules'))



@main_bp.route('/expenses/settings', methods=['POST'])
def expenses_settings():
    user = sanitize_text(request.form.get('user', ''))
    if not is_admin(user):
        flash(_('Only admin can update settings.'), 'error')
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
        flash(_('Settings saved.'), 'success')
    except Exception:
        flash(_('Failed to save settings.'), 'error')
    today = date.today()
    tab = request.args.get('tab', 'general-settings')
    return redirect(url_for('main.recurring_expenses_page', tab=tab))



@main_bp.route('/expenses/delete/<int:entry_id>', methods=['POST'])
def delete_expense_entry(entry_id):
    entry = db.get_or_404(ExpenseEntry, entry_id)
    user = sanitize_text(request.form.get('user', ''))
    if not can_modify(user, entry.payer or ''):
        flash(_('Not allowed to delete entry.'), 'error')
        return redirect(url_for('main.expenses'))
    db.session.delete(entry)
    db.session.commit()
    flash(_('Expense deleted.'), 'success')
    # Preserve view
    today = date.today()
    y = request.args.get('y') or today.year
    m = request.args.get('m') or today.month
    sel = request.args.get('sel')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))


@main_bp.route('/expenses/edit/<int:entry_id>', methods=['POST'])
def edit_expense_entry(entry_id):
    entry = db.get_or_404(ExpenseEntry, entry_id)
    user = sanitize_text(request.form.get('user', ''))
    if not can_modify(user, entry.payer or ''):
        flash(_('Not allowed to edit entry.'), 'error')
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
    if not entry.amount and entry.unit_price is not None:
        entry.amount = entry.unit_price * (entry.quantity if entry.quantity is not None else 1)
    if not _all_finite(entry.amount, entry.unit_price, entry.quantity):
        db.session.rollback()
        flash(_('Invalid amount.'), 'error')
        return _redirect_to_view(entry.date)
    problem = _amount_problem(entry.amount, entry.quantity)
    if problem:
        entry_date = entry.date
        db.session.rollback()
        flash(problem, 'error')
        return _redirect_to_view(entry_date)
    if request.form.get('split_present'):
        try:
            entry.split_with = _split_from_form(request.form, entry.amount)
        except SplitError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
            return _redirect_to_view(entry.date)
    db.session.commit()
    flash(_('Expense updated.'), 'success')
    # Preserve view
    today = date.today()
    y = request.args.get('y') or entry.date.year
    m = request.args.get('m') or entry.date.month
    sel = request.args.get('sel') or entry.date.strftime('%Y-%m-%d')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))


def _redirect_to_view(d: date | None = None):
    today = date.today()
    y = request.args.get('y') or (d or today).year
    m = request.args.get('m') or (d or today).month
    sel = request.args.get('sel') or (d.strftime('%Y-%m-%d') if d else None)
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))


@main_bp.route('/expenses/skip/<int:entry_id>', methods=['POST'])
def toggle_skip_expense_entry(entry_id):
    """Skip a day (e.g. no newspaper) without deleting it, or restore a skipped day."""
    entry = db.get_or_404(ExpenseEntry, entry_id)
    user = sanitize_text(request.form.get('user', ''))
    if not can_modify(user, entry.payer or ''):
        flash(_('Not allowed to change entry.'), 'error')
        return _redirect_to_view(entry.date)
    # Only recurring days can be skipped; a skipped day can always be restored
    if not entry.skipped and (entry.recurring_id is None or entry.is_settlement):
        flash(_('Only recurring days can be skipped.'), 'error')
        return _redirect_to_view(entry.date)
    entry.skipped = not bool(entry.skipped)
    db.session.commit()
    flash(_('Day skipped.') if entry.skipped else _('Day restored.'), 'success')
    return _redirect_to_view(entry.date)


@main_bp.route('/expenses/recurring/<int:rid>/restore', methods=['POST'])
def restore_recurring_day(rid):
    """Add back a scheduled recurring day whose entry was deleted earlier."""
    r = db.get_or_404(RecurringExpense, rid)
    user = sanitize_text(request.form.get('user', ''))
    try:
        d = datetime.strptime(request.form.get('date', ''), '%Y-%m-%d').date()
    except Exception:
        flash(_('Invalid date.'), 'error')
        return _redirect_to_view()
    if not can_modify(user, r.creator or ''):
        flash(_('Not allowed to restore this day.'), 'error')
        return _redirect_to_view(d)
    # Days before the rule's last apply-from edit followed settings that are no longer stored
    earliest = r.effective_from or r.start_date
    if d > date.today() or (earliest and d < earliest) or not _rule_occurs_on(r, d):
        flash(_('That day is not part of this recurring rule.'), 'error')
        return _redirect_to_view(d)
    if ExpenseEntry.query.filter_by(recurring_id=r.id, date=d).first():
        flash(_('That day is already there.'), 'info')
        return _redirect_to_view(d)
    db.session.add(_entry_from_rule(r, d))
    db.session.commit()
    flash(_('%(title)s added back for %(date)s.', title=r.title, date=d), 'success')
    return _redirect_to_view(d)


@main_bp.route('/expenses/settle', methods=['POST'])
def settle_up():
    """Record that one member paid another back."""
    user = sanitize_text(request.form.get('user', ''))
    from_member = sanitize_text(request.form.get('from_member', '')).strip()
    to_member = sanitize_text(request.form.get('to_member', '')).strip()
    try:
        amount = float(request.form.get('amount') or 0)
    except (TypeError, ValueError):
        amount = 0
    if not from_member or not to_member or from_member == to_member or not math.isfinite(amount) or amount <= 0:
        flash(_('Invalid settlement.'), 'error')
        return _redirect_to_view()
    if not (can_modify(user, from_member) or can_modify(user, to_member)):
        flash(_('Only the people involved can record a settlement.'), 'error')
        return _redirect_to_view()
    today = date.today()
    db.session.add(ExpenseEntry(
        date=today,
        title=f'Settlement: {from_member} → {to_member}',
        amount=amount,
        payer=from_member,
        split_with=json.dumps([to_member]),
        is_settlement=True,
    ))
    db.session.commit()
    flash(_('Settlement recorded.'), 'success')
    return _redirect_to_view()


@main_bp.route('/expenses/bulk-delete', methods=['POST'])
def bulk_delete_expenses():
    user = sanitize_text(request.form.get('user', ''))
    ids = request.form.getlist('ids')
    if not ids:
        flash(_('No entries selected.'), 'warning')
        return redirect(url_for('main.expenses'))
    deleted = 0
    for entry_id in ids:
        try:
            entry = db.session.get(ExpenseEntry, int(entry_id))
            if entry and can_modify(user, entry.payer or ''):
                db.session.delete(entry)
                deleted += 1
        except Exception:
            continue
    db.session.commit()
    flash(ngettext('%(num)s expense deleted.', '%(num)s expenses deleted.', deleted), 'success')
    # Preserve view
    today = date.today()
    y = request.args.get('y') or today.year
    m = request.args.get('m') or today.month
    sel = request.args.get('sel')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))


@main_bp.route('/api/expenses/month', methods=['GET'])
def api_expenses_month():
    # Keep recurring data up-to-date before answering
    _generate_recurring_entries_until(date.today())
    today = date.today()
    # Parse query params with clear validation and logging
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
            'error': _('Invalid month parameter. Must be an integer between 1 and 12.'),
            'year': year_q if year_q is not None else y,
            'month': month_q if month_q is not None else m,
        }), 400
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
        split_people=_split_people(config),
        expense_settings=expense_settings,
        active_tab=active_tab,
        today=today,
    )
