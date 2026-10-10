"""Toasts after every action, no zero expenses, recurring chore ticks, and the undo window."""
import io
import json
from datetime import date, timedelta

import pytest

from app import create_app, db, undo
from app.clock import utcnow
from app.models import Chore, ExpenseEntry, RecurringChore, RecurringExpense, UndoStash

TODAY = date.today()


@pytest.fixture()
def client():
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite://', 'SECRET_KEY': 'test'})
    with app.app_context():
        db.create_all()
        db.session.execute(db.text("CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY, value TEXT)"))
        db.session.commit()
    return app.test_client()


def flashes(client):
    """Messages waiting to be shown as toasts, oldest first; reading them clears them."""
    with client.session_transaction() as sess:
        return [message for _category, message in sess.pop('_flashes', [])]


# --- A toast after every add / edit / delete ---------------------------------------------------

def last_id(client, table):
    with client.application.app_context():
        return db.session.execute(db.text(f'SELECT MAX(id) FROM {table}')).scalar()


def test_notes_say_what_happened(client):
    client.post('/notes', data={'content': 'hello', 'creator': 'Mom'})
    assert flashes(client) == ['Note added.']
    note = last_id(client, 'note')
    client.post('/notes', data={'content': 'hi', 'creator': 'Mom', 'note_id': note})
    assert flashes(client) == ['Note updated.']
    client.post('/notes', data={'content': 'hi', 'creator': 'Dad', 'note_id': note})
    assert flashes(client) == ['Not allowed to edit note.']
    client.post(f'/notes/delete/{note}', data={'user': 'Dad'})
    assert flashes(client) == ['Not allowed to delete note.']
    client.post(f'/notes/delete/{note}', data={'user': 'Mom'})
    assert flashes(client) == ['Note deleted.']


@pytest.mark.parametrize('add_url, add_data, table, delete_url, added, deleted, refused', [
    ('/shopping', {'item': 'Eggs', 'tags': '[]'}, 'shopping_item', '/shopping/delete/{id}',
     'Item added.', 'Item deleted.', 'Not allowed to delete item.'),
    ('/expiry', {'name': 'Milk', 'expiry_date': '2030-01-01'}, 'expiry_item', '/expiry/delete/{id}',
     'Item added.', 'Item deleted.', 'Not allowed to delete item.'),
    ('/shorten', {'original_url': 'https://example.com'}, 'short_url', '/shorten/delete/{id}',
     'Short link created.', 'Short link deleted.', 'Not allowed to delete short link.'),
    ('/qr', {'qrtext': 'hello'}, 'qr_code', '/qr/delete/{id}',
     'QR code created.', 'QR code deleted.', 'Not allowed to delete QR code.'),
])
def test_simple_lists_say_what_happened(client, add_url, add_data, table, delete_url, added, deleted, refused):
    resp = client.post(add_url, data=dict(add_data, creator='Mom'))
    # The QR page answers directly instead of redirecting, so its message is already in the page
    assert flashes(client) == [added] or added in resp.get_data(as_text=True)
    row = last_id(client, table)
    client.post(delete_url.format(id=row), data={'user': 'Dad'})
    assert flashes(client) == [refused]
    client.post(delete_url.format(id=row), data={'user': 'Mom'})
    assert flashes(client) == [deleted]


def test_shared_cloud_says_what_happened(client):
    client.post('/upload', data={'files': [(io.BytesIO(b'a'), 'a.txt'), (io.BytesIO(b'b'), 'b.txt')], 'creator': 'Mom'},
                content_type='multipart/form-data')
    assert flashes(client) == ['2 files uploaded.']
    client.post('/upload', data={'creator': 'Mom'}, content_type='multipart/form-data')
    assert flashes(client) == ['No file selected.']
    row = last_id(client, 'file')
    client.post(f'/upload/delete/{row}', data={'user': 'Dad'})
    assert flashes(client) == ['Not allowed to delete file.']
    client.post(f'/upload/delete/{row}', data={'user': 'Mom'})
    assert flashes(client) == ['File deleted.']


def test_pdfs_and_downloads_say_what_happened(client):
    client.post('/pdfs', data={'pdf': (io.BytesIO(b'%PDF'), 'bill.pdf'), 'creator': 'Mom'}, content_type='multipart/form-data')
    assert flashes(client) == ['PDF compressed.']
    client.post(f"/pdfs/delete/{last_id(client, 'pdf')}", data={'user': 'Mom'})
    assert flashes(client) == ['PDF deleted.']
    from app.models import Media
    with client.application.app_context():
        db.session.add(Media(title='x', url='https://example.com', creator='Mom', filepath='', status='error'))
        db.session.commit()
    client.post(f"/media/delete/{last_id(client, 'media')}", data={'user': 'Mom'})
    assert flashes(client) == ['Download deleted.']


# --- No zero expenses --------------------------------------------------------------------------

def add_expense(client, **fields):
    data = {'form_type': 'single', 'title': 'Tea', 'date': TODAY.isoformat(), 'payer': 'Mom', 'amount': '10'}
    data.update(fields)
    return client.post('/expenses', data=data)


def entry_count(client):
    with client.application.app_context():
        return ExpenseEntry.query.count()


@pytest.mark.parametrize('fields, message', [
    ({'amount': '0'}, 'Amount must be greater than zero.'),
    ({'amount': ''}, 'Amount must be greater than zero.'),
    ({'amount': '-5'}, 'Amount must be greater than zero.'),
    ({'amount': '10', 'quantity': '0'}, 'Quantity must be greater than zero.'),
    ({'amount': '', 'quantity': '0', 'unit_price': '5'}, 'Quantity must be greater than zero.'),
    ({'amount': '', 'quantity': '2', 'unit_price': '0'}, 'Amount must be greater than zero.'),
])
def test_expense_with_zero_amount_or_quantity_is_rejected(client, fields, message):
    add_expense(client, **fields)
    assert flashes(client) == [message]
    assert entry_count(client) == 0


def test_expense_with_real_numbers_is_saved(client):
    add_expense(client, amount='', quantity='2', unit_price='5')
    add_expense(client, amount='7')
    assert entry_count(client) == 2


def test_editing_an_expense_to_zero_is_rejected_and_old_zero_rows_can_be_fixed(client):
    with client.application.app_context():
        # A row saved by an older version, when zeros were accepted
        db.session.add(ExpenseEntry(date=TODAY, title='Old', amount=0.0, quantity=0.0, payer='Mom'))
        db.session.commit()
        eid = ExpenseEntry.query.one().id
    # It still shows up
    month = client.get(f'/api/expenses/month?year={TODAY.year}&month={TODAY.month}').get_json()
    assert month['by_date'][TODAY.isoformat()]['entries'][0]['title'] == 'Old'
    client.post(f'/expenses/edit/{eid}', data={'user': 'Mom', 'title': 'Old', 'amount': '0', 'quantity': '0'})
    assert flashes(client) == ['Quantity must be greater than zero.']
    client.post(f'/expenses/edit/{eid}', data={'user': 'Mom', 'title': 'Old', 'amount': '0', 'quantity': '1'})
    assert flashes(client) == ['Amount must be greater than zero.']
    client.post(f'/expenses/edit/{eid}', data={'user': 'Mom', 'title': 'Fixed', 'amount': '12', 'quantity': '1'})
    with client.application.app_context():
        entry = db.session.get(ExpenseEntry, eid)
        assert (entry.title, entry.amount, entry.quantity) == ('Fixed', 12.0, 1.0)


@pytest.mark.parametrize('price, quantity, message', [
    ('0', '1', 'Unit price must be greater than zero.'),
    ('5', '0', 'Quantity must be greater than zero.'),
])
def test_recurring_rule_needs_price_and_quantity_above_zero(client, price, quantity, message):
    client.post('/expenses', data={'form_type': 'recurring', 'title': 'Milk', 'unit_price': price,
                                   'default_quantity': quantity, 'frequency': 'daily', 'creator': 'Mom'})
    assert flashes(client) == [message]
    with client.application.app_context():
        assert RecurringExpense.query.count() == 0
        db.session.add(RecurringExpense(title='Milk', unit_price=10, default_quantity=1, frequency='daily',
                                        start_date=TODAY, effective_from=TODAY, creator='Mom'))
        db.session.commit()
        rid = RecurringExpense.query.one().id
    form = {'user': 'Mom', 'title': 'Milk', 'unit_price': price, 'default_quantity': quantity, 'frequency': 'daily',
            'edit_strategy': 'apply_from', 'effective_from': TODAY.isoformat()}
    preview = client.post(f'/expenses/recurring/edit/{rid}/preview', data=form)
    assert preview.status_code == 400 and preview.get_json()['error'] == message
    client.post(f'/expenses/recurring/edit/{rid}', data=form)
    assert flashes(client) == [message]
    with client.application.app_context():
        rule = db.session.get(RecurringExpense, rid)
        assert (rule.unit_price, rule.default_quantity) == (10, 1)


# --- Recurring chore ticks ---------------------------------------------------------------------

def make_recurring_chore(client, start, unit='day', end=None):
    client.post('/chores', data={
        'description': 'Water plants', 'creator': 'Mom', 'tags': '[]', 'is_recurring': 'on', 'rec_interval': '1',
        'rec_unit': unit, 'rec_start_date': start.isoformat(), 'rec_end_date': end.isoformat() if end else '',
    })
    flashes(client)
    with client.application.app_context():
        return Chore.query.one().id


def state(client):
    client.get('/chores')
    with client.application.app_context():
        chore = Chore.query.one()
        return chore.due_date, bool(chore.done)


def test_ticking_a_recurring_chore_again_takes_the_tick_back(client):
    cid = make_recurring_chore(client, TODAY)
    client.post(f'/chores/toggle/{cid}')
    assert state(client) == (TODAY + timedelta(days=1), False)
    page = client.get('/chores').get_data(as_text=True)
    assert 'Done. Next due on ' + (TODAY + timedelta(days=1)).isoformat() in page
    assert 'class="check-btn is-checked"' in page
    flashes(client)
    # A second tap does not move it another day ahead
    client.post(f'/chores/toggle/{cid}')
    assert flashes(client) == [f'Water plants is open again. Due on {TODAY.isoformat()}.']
    assert state(client) == (TODAY, False)
    assert 'class="check-btn "' in client.get('/chores').get_data(as_text=True)


def test_many_taps_never_move_a_recurring_chore_more_than_one_round(client):
    cid = make_recurring_chore(client, TODAY)
    seen = set()
    for _tap in range(7):
        client.post(f'/chores/toggle/{cid}')
        seen.add(state(client)[0])
    assert seen == {TODAY, TODAY + timedelta(days=1)}


def test_a_chore_due_later_can_be_ticked_early_once(client):
    start = TODAY + timedelta(days=3)
    cid = make_recurring_chore(client, start, unit='week')
    client.post(f'/chores/toggle/{cid}')
    assert state(client) == (start + timedelta(days=7), False)
    client.post(f'/chores/toggle/{cid}')
    assert state(client) == (start, False)


def test_last_date_of_a_recurring_chore_shows_as_completed(client):
    cid = make_recurring_chore(client, TODAY, end=TODAY)
    client.post(f'/chores/toggle/{cid}')
    assert state(client) == (TODAY, True)
    page = client.get('/chores').get_data(as_text=True)
    assert 'Completed. That was its last date.' in page
    assert 'Next due on' not in page
    # Still completed on a later load
    assert state(client) == (TODAY, True)
    # Tapping it again opens it up
    client.post(f'/chores/toggle/{cid}')
    assert state(client) == (TODAY, False)
    assert 'Next due on ' + TODAY.isoformat() in client.get('/chores').get_data(as_text=True)


def test_ended_recurring_chore_cannot_be_reopened(client):
    cid = make_recurring_chore(client, TODAY)
    with client.application.app_context():
        rule = RecurringChore.query.one()
        rule.end_date = TODAY - timedelta(days=1)
        db.session.commit()
    assert state(client)[1] is True
    flashes(client)
    client.post(f'/chores/toggle/{cid}')
    assert flashes(client) == ['This recurring chore has ended.']
    assert state(client)[1] is True


# --- Undo window -------------------------------------------------------------------------------

def test_undo_lasts_twenty_seconds(client):
    assert undo.UNDO_SECONDS == 20
    cid = make_recurring_chore(client, TODAY)
    before = utcnow()
    client.post(f'/chores/toggle/{cid}')
    with client.session_transaction() as sess:
        offer = json.loads(dict(sess['_flashes'])['undo'])
    assert offer['seconds'] == 20
    with client.application.app_context():
        stash = UndoStash.query.one()
        # A few seconds of grace past what the toast shows, and no more
        assert timedelta(seconds=20) < stash.expires_at - before < timedelta(seconds=30)
        stash.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
    assert client.post(f"/undo/{offer['token']}").status_code == 410
    assert state(client) == (TODAY + timedelta(days=1), False)
