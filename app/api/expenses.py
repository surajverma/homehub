"""API operations for the expense tracker: recurring rules, entries, skipping a recurring day,
changing a day's quantity and adding a one-off expense.

Recurring entries are generated up to today first, exactly as the expenses page does, so the
API sees the same days. Only today and past days can be changed.
"""
from datetime import date

from flask_babel import gettext as _

from .. import admin
from ..blueprints import expenses as web
from ..models import ExpenseEntry, RecurringExpense, db
from ..security import sanitize_text
from .core import ApiError
from .home import MAX_RANGE_DAYS, day_label
from .registry import iso, operation

MAX_NUMBER = 1e9


def _settings() -> dict:
    return web._load_expense_settings()


def money(amount: float, settings: dict | None = None) -> str:
    settings = settings or _settings()
    precision = settings.get('fraction_precision', 2)
    text = f'{amount:,.0f}' if float(amount).is_integer() else f'{amount:,.{precision}f}'
    return f"{settings.get('currency', '')}{text}"


def _number(value):
    return float(value) if value is not None else None


def _entry(e: ExpenseEntry) -> dict:
    return {
        'id': e.id,
        'date': iso(e.date),
        'title': e.title,
        'category': e.category or None,
        'quantity': _number(e.quantity),
        'unit_price': _number(e.unit_price),
        'amount': float(e.amount or 0),
        'payer': e.payer or None,
        'skipped': bool(e.skipped),
        'recurring_id': e.recurring_id,
        'is_settlement': bool(e.is_settlement),
        'created_by': e.created_by or None,
    }


def _rule(r: RecurringExpense) -> dict:
    return {
        'id': r.id,
        'title': r.title,
        'unit_price': _number(r.unit_price),
        'usual_quantity': _number(r.default_quantity),
        'frequency': r.frequency,
        'category': r.category or None,
        'start_date': iso(r.start_date),
        'end_date': iso(r.end_date),
        'creator': r.creator or None,
    }


def _bring_up_to_date() -> date:
    today = date.today()
    web._generate_recurring_entries_until(today)
    return today


def _summary(e: ExpenseEntry, settings=None) -> str:
    parts = [f'{e.title}, {day_label(e.date)}:']
    if e.quantity is not None:
        parts.append(_('quantity %(num)s,', num=f'{e.quantity:g}'))
    parts.append(money(float(e.amount or 0), settings))
    return ' '.join(parts)


@operation('expenses_rules', tool='expenses', level='read', method='GET', path='/expenses/rules',
           title='Recurring expenses',
           description=('Recurring expense rules that are still running (not ended): id, title, unit price, usual quantity, '
                        'frequency and category. Use this to match what a person says, such as "milk" or "newspaper", to a rule.'),
           idempotent=True)
def expenses_rules(ctx, args):
    today = _bring_up_to_date()
    rules = RecurringExpense.query.order_by(RecurringExpense.title.asc()).all()
    active = [r for r in rules if not r.end_date or r.end_date >= today]
    return {'currency': _settings().get('currency'), 'rules': [_rule(r) for r in active]}


@operation('expenses_list', tool='expenses', level='read', method='GET', path='/expenses',
           title='Expense entries',
           description=('Expense entries for one day (date, default today) or a range of at most 62 days (from and to). '
                        'Skipped recurring days are listed with skipped true and do not count as spending.'),
           properties={
               'date': {'type': 'string', 'format': 'date', 'in': 'query', 'description': 'One day, YYYY-MM-DD.'},
               'from': {'type': 'string', 'format': 'date', 'in': 'query', 'description': 'First day, YYYY-MM-DD.'},
               'to': {'type': 'string', 'format': 'date', 'in': 'query', 'description': 'Last day, YYYY-MM-DD.'},
           },
           idempotent=True)
def expenses_list(ctx, args):
    today = _bring_up_to_date()
    one, start, end = args.get('date'), args.get('from'), args.get('to')
    if one and (start or end):
        raise ApiError(400, 'bad_request', _('Use either date or from and to, not both.'))
    if start or end:
        if not (start and end):
            raise ApiError(400, 'bad_request', _('Send both from and to.'))
        if end < start:
            raise ApiError(400, 'bad_request', _('to must be on or after from.'))
        if (end - start).days + 1 > MAX_RANGE_DAYS:
            raise ApiError(400, 'bad_request', _('The range can be at most %(num)s days.', num=MAX_RANGE_DAYS))
    else:
        start = end = one or today
    rows = (ExpenseEntry.query.filter(ExpenseEntry.date >= start, ExpenseEntry.date <= end)
            .order_by(ExpenseEntry.date.asc(), ExpenseEntry.timestamp.asc()).all())
    settings = _settings()
    return {'from': start.isoformat(), 'to': end.isoformat(), 'currency': settings.get('currency'),
            'entries': [_entry(e) for e in rows]}


DAY_PROPERTIES = {
    'rule_id': {'type': 'integer', 'in': 'path', 'description': 'The recurring rule id from GET /expenses/rules.'},
    'day': {'type': 'string', 'format': 'date', 'in': 'path', 'description': 'The day, YYYY-MM-DD (today or earlier).'},
}


def _recurring_day(ctx, rule_id: int, day: date) -> ExpenseEntry:
    """The entry of a recurring rule on a day, checked as the expenses page checks it."""
    today = _bring_up_to_date()
    if day > today:
        raise ApiError(400, 'bad_request', _('Only today and earlier days can be changed.'))
    rule = db.session.get(RecurringExpense, rule_id)
    if rule is None:
        raise ApiError(404, 'not_found', _('That recurring expense was not found.'))
    entry = ExpenseEntry.query.filter_by(recurring_id=rule.id, date=day).first()
    if entry is None:
        generated = rule.last_generated_date and day <= rule.last_generated_date
        if generated and web._rule_occurs_on(rule, day):
            raise ApiError(409, 'conflict', _('%(title)s on %(date)s was deleted in HomeHub. Add it back on the expenses page first.',
                                              title=rule.title, date=day_label(day)))
        raise ApiError(404, 'not_found', _('That day is not part of this recurring rule.'))
    if not admin.can_modify(ctx.user, web.entry_owner(entry)):
        raise ApiError(403, 'forbidden', _('%(name)s cannot change this entry.', name=ctx.user))
    return entry


@operation('expenses_set_skipped', tool='expenses', level='write', method='PUT',
           path='/expenses/rules/<int:rule_id>/days/<day>/skipped',
           title='Skip or restore a recurring day',
           description=('Skip a day of a recurring expense (true), for example no newspaper today, or restore it (false). '
                        'Setting the same value again changes nothing. Only whoever added the rule may change its days.'),
           properties={**DAY_PROPERTIES, 'skipped': {'type': 'boolean', 'in': 'body', 'description': 'true to skip the day, false to restore it.'}},
           required=('rule_id', 'day', 'skipped'), idempotent=True)
def expenses_set_skipped(ctx, args):
    entry = _recurring_day(ctx, args['rule_id'], args['day'])
    if args['skipped'] and not entry.skipped and not web.can_skip(entry):
        raise ApiError(400, 'bad_request', _('Only recurring days can be skipped.'))
    entry.skipped = args['skipped']
    db.session.commit()
    ctx.target = f'{entry.title} {iso(entry.date)}'
    summary = (_('%(title)s skipped on %(date)s.', title=entry.title, date=day_label(entry.date)) if entry.skipped
               else _('%(title)s restored on %(date)s.', title=entry.title, date=day_label(entry.date)))
    return {**_entry(entry), 'summary': summary}


@operation('expenses_set_quantity', tool='expenses', level='write', method='PATCH',
           path='/expenses/rules/<int:rule_id>/days/<day>',
           title="Change a recurring day's quantity",
           description=("Change the quantity of a recurring expense on one day, for example 3 litres of milk instead of 2. "
                        "The amount becomes unit price times quantity. Only whoever added the rule may change its days."),
           properties={**DAY_PROPERTIES, 'quantity': {'type': 'number', 'in': 'body', 'exclusiveMinimum': 0, 'maximum': MAX_NUMBER,
                                                      'description': 'The new quantity (greater than zero).'}},
           required=('rule_id', 'day', 'quantity'), idempotent=True)
def expenses_set_quantity(ctx, args):
    entry = _recurring_day(ctx, args['rule_id'], args['day'])
    quantity = args['quantity']
    unit_price = entry.unit_price
    if unit_price is None:
        raise ApiError(400, 'bad_request', _('This entry has no unit price, so its quantity cannot set the amount.'))
    amount = unit_price * quantity
    if not web._all_finite(amount, unit_price, quantity):
        raise ApiError(400, 'bad_request', _('Invalid amount.'))
    problem = web._amount_problem(amount, quantity)
    if problem:
        raise ApiError(400, 'bad_request', problem)
    entry.quantity = quantity
    entry.amount = amount
    db.session.commit()
    ctx.target = f'{entry.title} {iso(entry.date)}: {quantity:g}'
    return {**_entry(entry), 'summary': _summary(entry)}


def _category(raw: str | None, settings: dict):
    if raw is None or not raw.strip():
        return None
    wanted = sanitize_text(raw).strip()
    for cat in settings.get('categories') or []:
        if cat.casefold() == wanted.casefold():
            return cat
    known = ', '.join(settings.get('categories') or []) or '-'
    raise ApiError(400, 'bad_request', _('Unknown category: %(name)s. Use one set on the expenses page: %(keys)s.', name=wanted, keys=known))


@operation('expenses_add', tool='expenses', level='write', method='POST', path='/expenses',
           title='Add an expense',
           description=('Add a one-off expense: either an amount, or a unit price and a quantity. "by" is required: the '
                        'expense is recorded as paid by the person in "by". To record that someone else paid, that person '
                        'must ask, or use HomeHub. Dates default to today; future dates are not allowed. Not split.'),
           properties={
               'title': {'type': 'string', 'in': 'body', 'minLength': 1, 'maxLength': 256, 'description': 'What it was, for example "Groceries".'},
               'amount': {'type': 'number', 'in': 'body', 'exclusiveMinimum': 0, 'maximum': MAX_NUMBER, 'description': 'The total. Leave out when sending unit_price and quantity.'},
               'unit_price': {'type': 'number', 'in': 'body', 'exclusiveMinimum': 0, 'maximum': MAX_NUMBER, 'description': 'Price of one unit.'},
               'quantity': {'type': 'number', 'in': 'body', 'exclusiveMinimum': 0, 'maximum': MAX_NUMBER, 'description': 'How many units.'},
               'category': {'type': 'string', 'in': 'body', 'maxLength': 64, 'description': 'Optional category, one of those set on the expenses page.'},
               'date': {'type': 'string', 'format': 'date', 'in': 'body', 'description': 'The day, YYYY-MM-DD (default today).'},
           },
           required=('title', 'by'), success_status=201)
def expenses_add(ctx, args):
    # Through the API the payer is always the person the change is for
    payer = ctx.require_member(_('Say which family member paid with "by".'))
    today = _bring_up_to_date()
    title = sanitize_text(args['title'])
    if not title:
        raise ApiError(400, 'bad_request', _('%(field)s cannot be empty.', field='title'))
    amount, unit_price, quantity = args.get('amount'), args.get('unit_price'), args.get('quantity')
    if amount is not None and (unit_price is not None or quantity is not None):
        raise ApiError(400, 'bad_request', _('Send either amount, or unit_price and quantity.'))
    if amount is None:
        if unit_price is None or quantity is None:
            raise ApiError(400, 'bad_request', _('Send either amount, or unit_price and quantity.'))
        amount = unit_price * quantity
    if not web._all_finite(amount, unit_price, quantity):
        raise ApiError(400, 'bad_request', _('Invalid amount.'))
    problem = web._amount_problem(amount, quantity, unit_price)
    if problem:
        raise ApiError(400, 'bad_request', problem)
    day = args.get('date') or today
    if day > today:
        raise ApiError(400, 'bad_request', _('Only today and earlier days can be changed.'))
    settings = _settings()
    entry = ExpenseEntry(date=day, title=title, category=_category(args.get('category'), settings), unit_price=unit_price,
                         quantity=quantity, amount=amount, payer=payer, split_with=None, created_by=ctx.user)
    db.session.add(entry)
    db.session.commit()
    ctx.target = f'#{entry.id} {title}'
    return {**_entry(entry), 'summary': _summary(entry, settings)}


@operation('expenses_delete', tool='expenses', level='write', method='DELETE', path='/expenses/<int:entry_id>',
           title='Move an expense to the trash',
           description=('Move an expense entry (a one-off expense or a settlement) to the trash, where it can be restored '
                        'from the Trash page in HomeHub for 7 days. Needs a token with Allow deleting. Only whoever added '
                        'it may do this. A day of a recurring expense is skipped instead (PUT .../skipped).'),
           properties={'entry_id': {'type': 'integer', 'in': 'path', 'description': 'The entry id from GET /expenses.'}},
           required=('entry_id',), idempotent=True, destructive=True)
def expenses_delete(ctx, args):
    entry = db.session.get(ExpenseEntry, args['entry_id'])
    if entry is None:
        raise ApiError(404, 'not_found', _('That expense was not found.'))
    if entry.recurring_id:
        raise ApiError(409, 'conflict', _('This is a day of a recurring expense. Skip it instead, or delete it in HomeHub.'))
    if not admin.can_modify(ctx.user, web.entry_owner(entry)):
        raise ApiError(403, 'forbidden', _('%(name)s cannot change this entry.', name=ctx.user))
    entry_id, name = entry.id, entry.title
    when = entry.date
    web.trash_expense_entry(entry, ctx.deleted_by)
    ctx.target = f'#{entry_id} {name}'
    return {'id': entry_id, 'title': name, 'date': iso(when), 'trashed': True,
            'summary': _('%(title)s moved to the trash. It can be restored from the Trash page for 7 days.', title=name)}
