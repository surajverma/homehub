import io
import json
from datetime import date

import pytest

from app import create_app, db
from app.blueprints.expense_import import guess_mapping, parse_date, parse_number, parse_split_cell, read_csv
from app.models import ExpenseEntry


def make_app():
    test_config = {
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite://',
        'HOMEHUB_CONFIG': {
            'admin_name': 'Administrator',
            'family_members': ['Alice', 'Bob'],
        },
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'test',
    }
    app = create_app(test_config)
    with app.app_context():
        db.create_all()
        db.session.execute(db.text("CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY, value TEXT)"))
        db.session.commit()
    return app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import tempfile
    # Uploaded files wait between steps in the temp folder; keep them in this test's folder
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    return make_app().test_client()


def upload(client, text, name='expenses.csv', encoding='utf-8'):
    data = {'file': (io.BytesIO(text.encode(encoding)), name)}
    return client.post('/expenses/import', data=data, content_type='multipart/form-data')


def entries(client):
    with client.application.app_context():
        return [(e.date, e.title, e.amount, e.payer, e.split_with, bool(e.is_settlement), e.category)
                for e in ExpenseEntry.query.order_by(ExpenseEntry.id).all()]


def add(client, **values):
    with client.application.app_context():
        db.session.add(ExpenseEntry(**values))
        db.session.commit()


# --- Parsing ---

def test_parse_helpers():
    assert parse_date('2026-10-21') == date(2026, 10, 21)
    assert parse_date('21/10/2026') == date(2026, 10, 21)
    assert parse_date('21-Oct-2026') == date(2026, 10, 21)
    assert parse_date('21/10/2026 14:05') == date(2026, 10, 21)
    assert parse_date('10/21/2026', 'mdy') == date(2026, 10, 21)
    assert parse_date('yesterday') is None
    assert parse_number('₹1,23,456.50') == 123456.5
    assert parse_number('Rs. 99') == 99
    assert parse_number('(250.00)') == -250
    assert parse_number('') is None
    assert parse_number('abc') != parse_number('abc')  # NaN
    assert json.loads(parse_split_cell('Alice;Bob')) == ['Alice', 'Bob']
    assert json.loads(parse_split_cell('Mom (55);Dad (45)')) == {'mode': 'shares', 'weights': {'Mom': 55, 'Dad': 45}}
    assert json.loads(parse_split_cell('A (60%);B (40%)')) == {'mode': 'percent', 'weights': {'A': 60, 'B': 40}}
    assert parse_split_cell('') is None


def test_header_found_below_statement_details_and_columns_guessed():
    text = 'Account: 1234\nName: Suraj\n\nTxn Date,Narration,Withdrawal Amt,Deposit Amt\n01/10/2026,UPI-Milk,50.00,\n'
    header, rows = read_csv(text)
    assert header == ['Txn Date', 'Narration', 'Withdrawal Amt', 'Deposit Amt']
    assert len(rows) == 1
    assert guess_mapping(header) == {'date': 0, 'title': 1, 'amount': 2}


# --- Round trip with the export ---

def test_export_then_import_round_trips(client):
    add(client, date=date(2026, 10, 2), title='दूध', category='Food', unit_price=30, quantity=2, amount=60, payer='Alice',
        split_with=json.dumps(['Alice', 'Bob']))
    add(client, date=date(2026, 10, 5), title='=cmd', amount=500, payer='Bob',
        split_with=json.dumps({'mode': 'percent', 'weights': {'Alice': 60, 'Bob': 40}}))
    add(client, date=date(2026, 10, 6), title='Settlement: Bob → Alice', amount=100, payer='Bob',
        split_with=json.dumps(['Alice']), is_settlement=True)
    exported = client.get('/expenses/export.csv?month=2026-10').get_data()
    before = entries(client)

    # Into an empty instance everything comes back the same
    other = make_app().test_client()
    other.post('/expenses/import', data={'file': (io.BytesIO(exported), 'homehub-expenses-2026-10.csv')},
               content_type='multipart/form-data')
    page = other.get('/expenses/import').get_data(as_text=True)
    assert '3 new' in page
    resp = other.post('/expenses/import/confirm', data={'user': 'Alice', 'mapped': '1', 'map_date': '0', 'map_title': '1', 'map_category': '2',
                                                       'map_unit_price': '3', 'map_quantity': '4', 'map_amount': '5',
                                                       'map_payer': '6', 'map_split': '7', 'map_settlement': '9',
                                                       'date_format': 'auto', 'rows': ['1', '2', '3']})
    assert resp.status_code == 302
    after = entries(other)
    assert [(d, t, a, p, json.loads(s), st, c) for d, t, a, p, s, st, c in after] == \
           [(d, t, a, p, json.loads(s), st, c) for d, t, a, p, s, st, c in before]


def test_reimporting_into_same_instance_flags_duplicates(client):
    add(client, date=date(2026, 10, 2), title='Milk', amount=60, payer='Alice')
    exported = client.get('/expenses/export.csv?month=2026-10').get_data(as_text=True).lstrip('﻿')
    upload(client, exported + '2026-10-03,Bread,,40.00,,40.00,Alice,,no,no\n')
    page = client.get('/expenses/import').get_data(as_text=True)
    assert '1 new' in page and '1 already saved' in page
    # Duplicates start unticked, new rows ticked
    assert not checked(page, 1) and checked(page, 2)
    resp = client.post('/expenses/import/confirm', data={'user': 'Alice', 'rows': ['2']})
    assert resp.status_code == 302
    assert [t for _d, t, *_ in entries(client)] == ['Milk', 'Bread']


def checked(html, line):
    import re
    tag = re.search(r'<input type="checkbox" name="rows" value="%d"[^>]*>' % line, html).group(0)
    return ' checked' in tag


# --- Bank statements and validation ---

STATEMENT = (
    'Txn Date,Narration,Withdrawal Amt,Deposit Amt\n'
    '01/10/2026,UPI-Milk,50.00,\n'
    '02/10/2026,Salary,,10000.00\n'
    '03/10/2026,UPI-Milk,50.00,\n'
    '03/10/2026,UPI-Milk,50.00,\n'
    'soon,Bad date,10,\n'
    '04/10/2026,Refund,-20,\n'
    '05/10/2026,,30,\n'
    '06/10/2026,Odd amount,abc,\n'
)


def test_statement_preview_flags_errors_and_repeats(client):
    upload(client, STATEMENT)
    page = client.get('/expenses/import?mapped=1&map_date=0&map_title=1&map_amount=2&payer=Alice').get_data(as_text=True)
    assert '2 new' in page
    assert '1 repeated in the file' in page
    assert '5 cannot be imported' in page
    assert 'Date not recognised' in page
    assert 'No amount' in page
    assert 'Amount must be greater than zero.' in page
    assert 'The title is empty.' in page
    assert 'Not a number: ' in page


def test_statement_needs_a_payer(client):
    upload(client, STATEMENT)
    page = client.get('/expenses/import').get_data(as_text=True)
    assert 'Choose who paid' in page
    resp = client.post('/expenses/import/confirm', data={'user': 'Alice', 'mapped': '1', 'map_date': '0', 'map_title': '1', 'map_amount': '2',
                                                       'rows': ['1']})
    assert resp.status_code == 302
    assert entries(client) == []


def test_statement_import_ignores_error_rows_even_if_sent(client):
    upload(client, STATEMENT)
    form = {'user': 'Alice', 'mapped': '1', 'map_date': '0', 'map_title': '1', 'map_amount': '2', 'payer': 'Alice',
            'rows': [str(n) for n in range(1, 9)]}
    resp = client.post('/expenses/import/confirm', data=form)
    assert resp.status_code == 302
    saved = entries(client)
    assert [(d, t, a, p) for d, t, a, p, *_ in saved] == [
        (date(2026, 10, 1), 'UPI-Milk', 50.0, 'Alice'),
        (date(2026, 10, 3), 'UPI-Milk', 50.0, 'Alice'),
        (date(2026, 10, 3), 'UPI-Milk', 50.0, 'Alice'),
    ]


def test_import_can_be_undone(client):
    upload(client, 'date,title,amount,payer\n2026-10-01,Milk,50,Alice\n')
    client.post('/expenses/import/confirm', data={'user': 'Alice', 'rows': ['1']})
    assert len(entries(client)) == 1
    with client.session_transaction() as sess:
        offers = [json.loads(m) for cat, m in sess.get('_flashes', []) if cat == 'undo']
    assert offers
    assert client.post(f"/undo/{offers[0]['token']}").status_code == 200
    assert entries(client) == []


@pytest.mark.parametrize('text,message', [
    ('', 'no rows'),
    ('date,title,amount\n', 'no rows'),
])
def test_upload_rejects_empty_files(client, text, message):
    upload(client, text)
    page = client.get('/expenses/import').get_data(as_text=True)
    assert message in page
    assert 'name="file"' in page


def test_upload_reads_windows_encoding(client):
    upload(client, 'date,title,amount,payer\n2026-10-01,Café,50,Alice\n', encoding='cp1252')
    client.post('/expenses/import/confirm', data={'user': 'Alice', 'rows': ['1']})
    assert entries(client)[0][1] == 'Café'


def test_missing_required_column_blocks_import(client):
    upload(client, 'when,what\n2026-10-01,Milk\n')
    page = client.get('/expenses/import').get_data(as_text=True)
    assert 'Choose the column for:' in page


def test_start_over_and_expired_import(client):
    upload(client, 'date,title,amount,payer\n2026-10-01,Milk,50,Alice\n')
    client.post('/expenses/import/cancel')
    assert 'name="file"' in client.get('/expenses/import').get_data(as_text=True)
    resp = client.post('/expenses/import/confirm', data={'user': 'Alice', 'rows': ['1']}, follow_redirects=True)
    assert 'The import has expired' in resp.get_data(as_text=True)
    assert entries(client) == []


def test_expenses_page_links_to_import(client):
    assert 'href="/expenses/import"' in client.get('/expenses').get_data(as_text=True)


# --- Numbers, limits and splits (PR review) ---

@pytest.mark.parametrize('cell,expected', [
    ('1234.5', 1234.5),
    ('1,234.50', 1234.5),
    ('₹1,23,456.50', 123456.5),
    ('Rs. 99', 99),
    ('INR 1,000 Dr', 1000),
    ('(250.00)', -250),
    ('-.5', -0.5),
    ('1.5e3', 1500),
    ('1e+06', 1000000),
])
def test_parse_number_reads_whole_numbers(cell, expected):
    assert parse_number(cell) == expected


@pytest.mark.parametrize('cell', ['1.234,56', '12 34', '1,234.5.6', '5 apples 3', 'abc', '1.2.3'])
def test_parse_number_refuses_anything_that_is_not_one_number(cell):
    value = parse_number(cell)
    assert value != value  # NaN


def test_unreadable_amount_cannot_be_imported(client):
    upload(client, 'date;title;amount;payer\n2026-10-01;Milk;1.234,56;Alice\n2026-10-02;Bread;40;Alice\n')
    page = client.get('/expenses/import').get_data(as_text=True)
    assert 'Not a number: &#34;1.234,56&#34;.' in page or 'Not a number: &quot;1.234,56&quot;.' in page
    assert '1 cannot be imported' in page
    client.post('/expenses/import/confirm', data={'user': 'Alice', 'rows': ['1', '2']})
    assert [t for _d, t, *_ in entries(client)] == ['Bread']


def test_export_writes_plain_decimals(client):
    add(client, date=date(2026, 10, 1), title='Big', amount=1000000, quantity=1000000, unit_price=1, payer='Alice')
    add(client, date=date(2026, 10, 2), title='Tiny', amount=1, quantity=0.0000001, unit_price=1, payer='Alice')
    text = client.get('/expenses/export.csv?month=2026-10').get_data(as_text=True)
    assert 'e+' not in text and 'e-' not in text
    assert ',1000000,' in text and ',0.0000001,' in text


def test_import_file_size_is_capped_before_reading(client, monkeypatch):
    from app.blueprints import expense_import
    monkeypatch.setattr(expense_import, 'MAX_IMPORT_BYTES', 100)
    resp = upload(client, 'date,title,amount,payer\n' + '2026-10-01,Milk,50,Alice\n' * 20)
    assert resp.status_code == 302
    page = client.get('/expenses/import').get_data(as_text=True)
    assert 'That file is larger than' in page
    assert 'name="file"' in page


def test_read_csv_stops_one_row_past_the_limit():
    text = 'date,title,amount\n' + '2026-10-01,Milk,50\n' * 50
    header, rows = read_csv(text, max_rows=10)
    assert header == ['date', 'title', 'amount']
    assert len(rows) == 11


def test_too_many_rows_are_refused(client, monkeypatch):
    from app.blueprints import expense_import
    monkeypatch.setattr(expense_import, 'MAX_ROWS', 3)
    monkeypatch.setattr(expense_import.read_csv, '__defaults__', (3,))
    upload(client, 'date,title,amount,payer\n' + '2026-10-01,Milk,50,Alice\n' * 10)
    assert 'more than 3 rows' in client.get('/expenses/import').get_data(as_text=True)


def test_split_weight_must_be_a_number(client):
    assert parse_split_cell('Alice (1.5);Bob (.5)') == json.dumps({'mode': 'shares', 'weights': {'Alice': 1.5, 'Bob': 0.5}})
    upload(client, 'date,title,amount,payer,split\n2026-10-01,Milk,50,Alice,Alice (1.2.3);Bob (1)\n')
    page = client.get('/expenses/import').get_data(as_text=True)
    assert 'The split weight is not a number' in page
    assert '1 cannot be imported' in page


def test_preview_that_fails_drops_the_file_instead_of_an_error_page(client, monkeypatch):
    from app.blueprints import expense_import
    upload(client, 'date,title,amount,payer\n2026-10-01,Milk,50,Alice\n')

    def broken(*_args, **_kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr(expense_import, 'build_rows', broken)
    resp = client.get('/expenses/import')
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert 'could not be read' in page and 'name="file"' in page
    # The stored file is gone, so the next visit starts over
    monkeypatch.undo()
    assert 'name="file"' in client.get('/expenses/import').get_data(as_text=True)


def test_export_keeps_split_weights_exact(client):
    split = json.dumps({'mode': 'shares', 'weights': {'Alice': 123456.78, 'Bob': 876543.22}})
    add(client, date=date(2026, 10, 1), title='Flat', amount=1000000, payer='Alice', split_with=split)
    add(client, date=date(2026, 10, 2), title='Tiny', amount=10, payer='Alice',
        split_with=json.dumps({'mode': 'shares', 'weights': {'Alice': 0.0000001, 'Bob': 1}}))
    text = client.get('/expenses/export.csv?month=2026-10').get_data(as_text=True)
    assert 'Alice (123456.78);Bob (876543.22)' in text
    assert 'Alice (0.0000001);Bob (1)' in text
    assert 'e+' not in text and 'e-' not in text
    # Reading the cell back gives the same weights
    assert json.loads(parse_split_cell('Alice (123456.78);Bob (876543.22)'))['weights'] == {'Alice': 123456.78, 'Bob': 876543.22}


@pytest.mark.parametrize('cell', [
    'Alice (' + '9' * 400 + ');Bob (1)',
    'Alice (1' + '0' * 308 + ');Bob (1' + '0' * 308 + ')',
])
def test_split_weights_must_be_finite(client, cell):
    upload(client, f'date,title,amount,payer,split\n2026-10-01,Milk,50,Alice,{cell}\n')
    page = client.get('/expenses/import').get_data(as_text=True)
    assert 'The split weight is not a number' in page
    assert '1 cannot be imported' in page
    client.post('/expenses/import/confirm', data={'user': 'Alice', 'rows': ['1']})
    assert entries(client) == []
    # The expenses page still works
    assert client.get('/api/expenses/month?year=2026&month=10').status_code == 200
