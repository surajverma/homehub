"""Import expenses from a CSV file: HomeHub's own export, or a bank or UPI statement.

The file is kept in a temp file between steps. The preview maps its columns to HomeHub's
fields, shows every row with what will happen to it, and flags likely duplicates (same date,
title, amount and payer as an entry already saved, or as an earlier row in the file). Only
the rows left ticked are saved, and the import can be undone for a few seconds.
"""
import csv
import glob
import io
import json
import math
import os
import re
import secrets
import tempfile
import time
from datetime import date, datetime

import bleach
from flask import current_app, flash, redirect, render_template, request, session, url_for
from flask_babel import gettext as _, ngettext

from ..blueprints import main_bp
from ..models import db, ExpenseEntry, parse_split
from ..security import sanitize_text
from ..undo import offer_undo, restorer, row_to_dict, same_record
from .expenses import MAX_SPLIT_WEIGHT, _CSV_FORMULA_PREFIXES, _amount_problem, _load_expense_settings, _split_people

MAX_ROWS = 5000
# Checked before the file is read, separately from the site-wide upload limit
MAX_IMPORT_BYTES = 10 * 1024 * 1024
_SESSION_KEY = 'expense_import'
_TEMP_PREFIX = 'homehub-import-'
# An import left half-way is forgotten after a day
_TEMP_MAX_AGE = 24 * 3600
_TOKEN_RE = re.compile(r'^[0-9a-f]{32}$')

# HomeHub field -> column names it is recognised by (compared lower-case, letters and digits only)
FIELDS = ('date', 'title', 'amount', 'category', 'payer', 'quantity', 'unit_price', 'split', 'settlement')
REQUIRED_FIELDS = ('date', 'title', 'amount')
_ALIASES = {
    'date': ('date', 'transaction date', 'txn date', 'tran date', 'value date', 'posting date', 'booking date'),
    'title': ('title', 'description', 'narration', 'details', 'particulars', 'remarks', 'transaction details',
              'transaction remarks', 'merchant', 'payee'),
    'amount': ('amount', 'debit', 'debit amount', 'withdrawal', 'withdrawal amount', 'withdrawal amt', 'dr',
               'amount inr', 'paid'),
    'category': ('category',),
    'payer': ('payer', 'paid by'),
    'quantity': ('quantity', 'qty'),
    'unit_price': ('unit price', 'unit_price', 'price', 'rate'),
    'split': ('split', 'split with'),
    'settlement': ('settlement',),
}

# (value, strptime formats); "auto" reads day before month, as Indian banks do
DATE_FORMATS = {
    'auto': ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%d/%m/%y', '%d-%m-%y', '%d %b %Y', '%d-%b-%Y',
             '%d-%b-%y', '%d %b %y', '%d %B %Y', '%Y/%m/%d'),
    'ymd': ('%Y-%m-%d', '%Y/%m/%d'),
    'dmy': ('%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%d/%m/%y', '%d-%m-%y'),
    'mdy': ('%m/%d/%Y', '%m-%d-%Y', '%m/%d/%y'),
}
_TIME_SUFFIX_RE = re.compile(r'[ T]\d{1,2}:\d{2}(:\d{2})?(\s*[AaPp][Mm])?$')
# The whole cell, once currency text is taken off: 1234.5, 1,234.50, 1,23,456.50, 1.5e3.
# Commas only as thousands separators, western (1,234,567) or Indian (1,23,45,678), so "12,34",
# "1,2,3" and "1.234,56" are refused rather than guessed.
_NUMBER_RE = re.compile(
    r'[-+]?(?:(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3}|\d+)(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?')
# Text before the number ("Rs. ", "INR ", "₹"), stopping where a sign, a decimal point or a digit starts it
_NUMBER_PREFIX_RE = re.compile(r'^\D*?(?=[-+]?\.?\d)')
# Text after the last digit (" INR", " Dr")
_NUMBER_SUFFIX_RE = re.compile(r'(?<=\d)\D*$')
_SPLIT_PART_RE = re.compile(r'^(.*?)\s*\(\s*(\d+(?:\.\d+)?|\.\d+)\s*(%?)\s*\)$')
# "Name (something with a digit)" that is not a valid weight, e.g. "Mom (1.2.3)"
_SPLIT_BAD_WEIGHT_RE = re.compile(r'\(([^()]*\d[^()]*)\)$')


class SplitWeightError(ValueError):
    """A split cell with a weight in brackets that is not a number."""

    def __init__(self, part: str):
        super().__init__(part)
        self.part = part


def _normal_header(text: str) -> str:
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()).split())


def _cell(value: str) -> str:
    """A cell's text, without the quote our export puts in front of formula-like text."""
    value = (value or '').strip()
    if len(value) > 1 and value[0] == "'" and value[1:].startswith(_CSV_FORMULA_PREFIXES):
        return value[1:]
    return value


def _decode(raw: bytes) -> str:
    for encoding in ('utf-8-sig', 'cp1252'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('latin-1')


def read_csv(text: str, max_rows: int = MAX_ROWS) -> tuple[list, list]:
    """(header, rows) of a CSV file. The header is the first row naming a date column,
    so statements with a few lines of account details above the table still work.

    Reading stops one row past ``max_rows``, so a huge file is not parsed in full just to be refused.
    """
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel
    # Up to 30 lines of account details, the header, then one row more than allowed
    limit = 30 + 1 + max_rows + 1
    rows = []
    for row in csv.reader(io.StringIO(text), dialect):
        if any((c or '').strip() for c in row):
            rows.append(row)
            if len(rows) >= limit:
                break
    if not rows:
        return [], []
    start = 0
    date_names = {_normal_header(a) for a in _ALIASES['date']}
    for i, row in enumerate(rows[:30]):
        if any(_normal_header(c) in date_names for c in row):
            start = i
            break
    return [(c or '').strip() for c in rows[start]], rows[start + 1:start + 2 + max_rows]


def guess_mapping(header: list) -> dict:
    """field -> column index, from the column names."""
    mapping = {}
    names = [_normal_header(h) for h in header]
    for field in FIELDS:
        aliases = [_normal_header(a) for a in _ALIASES[field]]
        for alias in aliases:
            if alias in names and names.index(alias) not in mapping.values():
                mapping[field] = names.index(alias)
                break
    return mapping


def parse_date(value: str, fmt: str = 'auto') -> date | None:
    value = _TIME_SUFFIX_RE.sub('', (value or '').strip())
    for pattern in DATE_FORMATS.get(fmt, DATE_FORMATS['auto']):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    return None


def parse_number(value: str) -> float | None:
    """A number from a statement cell: "₹1,23,456.50", "Rs. 99", "(250.00)" (negative). None when blank.

    Currency text around the number is ignored, but what is left must be one whole number:
    "1.234,56" or "12 34" give NaN rather than a wrong amount.
    """
    text = (value or '').strip()
    if not text:
        return None
    negative = text.startswith('(') and text.endswith(')')
    if negative:
        text = text[1:-1].strip()
    text = _NUMBER_SUFFIX_RE.sub('', _NUMBER_PREFIX_RE.sub('', text, count=1)).strip()
    if not _NUMBER_RE.fullmatch(text):
        return float('nan')
    number = float(text.replace(',', ''))
    return -abs(number) if negative else number


def parse_split_cell(value: str):
    """Our export's split text back into stored JSON: "Alice;Bob", "Mom (55);Dad (45)" or "A (60%);B (40%)"."""
    names, weights, percent = [], {}, False
    for raw in (value or '').split(';'):
        part = sanitize_text(raw).strip()
        if not part:
            continue
        match = _SPLIT_PART_RE.match(part)
        if match:
            name, weight = match.group(1).strip(), float(match.group(2))
            percent = percent or bool(match.group(3))
            # A weight with hundreds of digits reads as infinity; refuse that and anything absurdly large
            if not math.isfinite(weight) or weight > MAX_SPLIT_WEIGHT:
                raise SplitWeightError(part)
            if name and weight > 0:
                weights[name] = weights.get(name, 0) + weight
                if not math.isfinite(weights[name]):
                    raise SplitWeightError(part)
        elif _SPLIT_BAD_WEIGHT_RE.search(part):
            raise SplitWeightError(part)
        elif part not in names:
            names.append(part)
    if weights:
        if not math.isfinite(sum(weights.values())):
            raise SplitWeightError(value)
        for name in names:
            weights.setdefault(name, 1.0)
        return json.dumps({'mode': 'percent' if percent else 'shares', 'weights': weights})
    return json.dumps(names) if names else None


def _duplicate_key(d: date, title: str, amount: float, payer: str, precision: int) -> tuple:
    return (d, ' '.join((title or '').casefold().split()), round(float(amount or 0), precision), (payer or '').casefold())


def build_rows(header: list, rows: list, mapping: dict, date_format: str, default_payer: str) -> list:
    """Every data row with what importing it would do: status new, duplicate, repeat or error."""
    precision = _load_expense_settings().get('fraction_precision', 2)

    def get(row, field):
        index = mapping.get(field)
        return _cell(row[index]) if index is not None and index < len(row) else ''

    result = []
    for line, row in enumerate(rows, start=1):
        item = {'line': line, 'status': 'new', 'error': None}
        d = parse_date(get(row, 'date'), date_format)
        title = bleach.clean(get(row, 'title'))[:256]
        quantity = parse_number(get(row, 'quantity'))
        unit_price = parse_number(get(row, 'unit_price'))
        amount = parse_number(get(row, 'amount'))
        if amount is None and unit_price is not None:
            amount = unit_price * (quantity if quantity is not None else 1)
        settlement = get(row, 'settlement').lower() in ('yes', 'y', 'true', '1')
        split_problem = None
        try:
            split_with = parse_split_cell(get(row, 'split'))
        except SplitWeightError as exc:
            split_with, split_problem = None, exc.part
        item.update(
            date=d, title=title, category=bleach.clean(get(row, 'category'))[:64] or None,
            payer=bleach.clean(get(row, 'payer'))[:64] or default_payer, quantity=quantity,
            unit_price=unit_price, amount=round(amount, precision) if amount is not None and math.isfinite(amount) else amount,
            split_with=split_with, split_names=list(parse_split(split_with)[1]), is_settlement=settlement,
        )
        bad_number = next((get(row, f) for f, v in (('amount', amount), ('quantity', quantity), ('unit_price', unit_price))
                           if v is not None and not math.isfinite(v)), None)
        if d is None:
            item['error'] = _('Date not recognised: "%(value)s".', value=get(row, 'date'))
        elif bad_number is not None:
            item['error'] = _('Not a number: "%(value)s".', value=bad_number)
        elif split_problem is not None:
            item['error'] = _('The split weight is not a number: "%(value)s".', value=split_problem)
        elif not title:
            item['error'] = _('The title is empty.')
        elif amount is None:
            item['error'] = _('No amount (for example a credit or a balance line).')
        elif not all(v is None or math.isfinite(v) for v in (amount, quantity, unit_price)):
            item['error'] = _('Invalid amount.')
        elif not item['payer']:
            item['error'] = _('Choose who paid, below.')
        else:
            item['error'] = _amount_problem(amount, quantity, unit_price)
            if not item['error'] and settlement and len(item['split_names']) != 1:
                item['error'] = _('A settlement needs exactly one person in the split column.')
        if item['error']:
            item['status'] = 'error'
        result.append(item)

    dated = [r['date'] for r in result if r['status'] != 'error']
    existing = set()
    if dated:
        for e in ExpenseEntry.query.filter(ExpenseEntry.date >= min(dated), ExpenseEntry.date <= max(dated)).all():
            existing.add(_duplicate_key(e.date, e.title, e.amount, e.payer, precision))
    seen = set()
    for r in result:
        if r['status'] == 'error':
            continue
        key = _duplicate_key(r['date'], r['title'], r['amount'], r['payer'], precision)
        if key in existing:
            r['status'] = 'duplicate'
        elif key in seen:
            r['status'] = 'repeat'
        seen.add(key)
    return result


# --- The uploaded file between steps ---

def _temp_path(token: str) -> str:
    return os.path.join(tempfile.gettempdir(), f'{_TEMP_PREFIX}{token}.csv')


def _forget_old_imports() -> None:
    now = time.time()
    for path in glob.glob(os.path.join(tempfile.gettempdir(), _TEMP_PREFIX + '*.csv')):
        try:
            if now - os.path.getmtime(path) > _TEMP_MAX_AGE:
                os.remove(path)
        except OSError:
            pass


def _current_import():
    """(token, file name, text) of this browser's import in progress, or None."""
    data = session.get(_SESSION_KEY) or {}
    token = data.get('token') or ''
    if not _TOKEN_RE.match(token):
        return None
    try:
        with open(_temp_path(token), 'rb') as f:
            return token, data.get('name') or '', _decode(f.read())
    except OSError:
        return None


def _end_import() -> None:
    data = session.pop(_SESSION_KEY, None) or {}
    token = data.get('token') or ''
    if _TOKEN_RE.match(token):
        try:
            os.remove(_temp_path(token))
        except OSError:
            pass


def _options(values, header: list) -> tuple[dict, str, str]:
    """(mapping, date format, default payer) from the preview form; guesses fill what it does not say."""
    mapping = guess_mapping(header)
    if values.get('mapped'):
        mapping = {}
        for field in FIELDS:
            raw = values.get(f'map_{field}', '')
            if raw.isdigit() and int(raw) < len(header):
                mapping[field] = int(raw)
    date_format = values.get('date_format') if values.get('date_format') in DATE_FORMATS else 'auto'
    payer = sanitize_text(values.get('payer', '')).strip()
    return mapping, date_format, payer


@main_bp.route('/expenses/import', methods=['GET'])
def expense_import_page():
    config = current_app.config['HOMEHUB_CONFIG']
    current = _current_import()
    if not current:
        return render_template('expenses_import.html', step='upload', config=config)
    _token, name, text = current
    try:
        header, rows = read_csv(text)
        mapping, date_format, payer = _options(request.args, header)
        preview = build_rows(header, rows, mapping, date_format, payer)
    except Exception:
        current_app.logger.exception('Could not preview the expense import')
        _end_import()
        flash(_('That file could not be read. Check that it is a CSV file and try again.'), 'error')
        return render_template('expenses_import.html', step='upload', config=config)
    counts = {status: sum(1 for r in preview if r['status'] == status) for status in ('new', 'duplicate', 'repeat', 'error')}
    return render_template(
        'expenses_import.html', step='preview', config=config, file_name=name, header=header,
        mapping=mapping, date_format=date_format, payer=payer, rows=preview, counts=counts,
        fields=FIELDS, required_fields=REQUIRED_FIELDS, date_formats=list(DATE_FORMATS),
        split_people=_split_people(config), missing=[f for f in REQUIRED_FIELDS if f not in mapping],
    )


@main_bp.route('/expenses/import', methods=['POST'])
def expense_import_upload():
    file = request.files.get('file')
    if not file or not file.filename:
        flash(_('No file selected.'), 'error')
        return redirect(url_for('main.expense_import_page'))
    # Its own limit, checked before reading the whole file into memory
    too_big = _('That file is larger than %(size)s MB. Split it into smaller files.', size=MAX_IMPORT_BYTES // (1024 * 1024))
    if request.content_length and request.content_length > MAX_IMPORT_BYTES + 64 * 1024:
        flash(too_big, 'error')
        return redirect(url_for('main.expense_import_page'))
    raw = file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        flash(too_big, 'error')
        return redirect(url_for('main.expense_import_page'))
    header, rows = read_csv(_decode(raw))
    if not header or not rows:
        flash(_('That file has no rows to import.'), 'error')
        return redirect(url_for('main.expense_import_page'))
    if len(rows) > MAX_ROWS:
        flash(_('That file has more than %(max)s rows. Split it into smaller files.', max=MAX_ROWS), 'error')
        return redirect(url_for('main.expense_import_page'))
    _end_import()
    _forget_old_imports()
    token = secrets.token_hex(16)
    with open(_temp_path(token), 'wb') as f:
        f.write(raw)
    session[_SESSION_KEY] = {'token': token, 'name': os.path.basename(file.filename)[:120]}
    return redirect(url_for('main.expense_import_page'))


@main_bp.route('/expenses/import/cancel', methods=['POST'])
def expense_import_cancel():
    _end_import()
    return redirect(url_for('main.expense_import_page'))


@main_bp.route('/expenses/import/confirm', methods=['POST'])
def expense_import_confirm():
    current = _current_import()
    if not current:
        flash(_('The import has expired. Upload the file again.'), 'error')
        return redirect(url_for('main.expense_import_page'))
    _token, _name, text = current
    # The importer owns the rows (never a name from the file), so they can change them later
    user = sanitize_text(request.form.get('user', '')).strip()
    if not user:
        flash(_('Choose who you are in the user menu before importing.'), 'error')
        return redirect(url_for('main.expense_import_page', **{k: v for k, v in request.form.items() if k != 'rows'}))
    header, rows = read_csv(text)
    mapping, date_format, payer = _options(request.form, header)
    chosen = {int(v) for v in request.form.getlist('rows') if v.isdigit()}
    # Rows are worked out again from the file, so nothing the browser sends is trusted as data
    added = []
    for r in build_rows(header, rows, mapping, date_format, payer):
        if r['line'] not in chosen or r['status'] == 'error':
            continue
        entry = ExpenseEntry(date=r['date'], title=r['title'], category=r['category'], unit_price=r['unit_price'],
                             quantity=r['quantity'], amount=r['amount'], payer=r['payer'], split_with=r['split_with'],
                             is_settlement=r['is_settlement'], created_by=user)
        db.session.add(entry)
        added.append(entry)
    if not added:
        flash(_('No rows selected to import.'), 'warning')
        return redirect(url_for('main.expense_import_page', **{k: v for k, v in request.form.items() if k != 'rows'}))
    db.session.commit()
    _end_import()
    first = min(e.date for e in added)
    offer_undo('expense_import', {'entries': [row_to_dict(e) for e in added]},
               ngettext('%(num)s expense imported.', '%(num)s expenses imported.', len(added)))
    return redirect(url_for('main.expenses', y=first.year, m=first.month))


@restorer('expense_import')
def _undo_import(payload: dict) -> None:
    for data in payload['entries']:
        entry = db.session.get(ExpenseEntry, data['id'])
        if same_record(entry, data):
            db.session.delete(entry)
