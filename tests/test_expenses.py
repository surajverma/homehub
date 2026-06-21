from datetime import date

import pytest

from app import create_app, db
from app.blueprints.expenses import _build_month_payload, _load_expense_settings
from app.models import ExpenseEntry, RecurringExpense


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
def client():
    app = make_app()
    return app.test_client()


def test_expense_settings_defaults_include_fraction_factor(client):
    with client.application.app_context():
        settings = _load_expense_settings()
    assert settings['currency'] == 'Rp'
    assert settings['categories'] == []
    assert settings['fraction_factor'] == 1
    assert settings['fraction_precision'] == 0


def test_expense_settings_load_fraction_factor(client):
    with client.application.app_context():
        db.session.execute(db.text("INSERT INTO app_setting(key, value) VALUES('currency', 'USD')"))
        db.session.execute(db.text("INSERT INTO app_setting(key, value) VALUES('categories', 'Groceries, Utilities')"))
        db.session.execute(db.text("INSERT INTO app_setting(key, value) VALUES('fraction_factor', '1000')"))
        db.session.commit()
        settings = _load_expense_settings()
    assert settings['currency'] == 'USD'
    assert settings['categories'] == ['Groceries', 'Utilities']
    assert settings['fraction_factor'] == 1000
    assert settings['fraction_precision'] == 3


def test_month_payload_only_includes_days_with_entries(client):
    with client.application.app_context():
        db.session.add_all([
            ExpenseEntry(date=date(2026, 5, 2), title='Milk', category='Food', unit_price=2.5, quantity=2, amount=5.0, payer='Alice'),
            ExpenseEntry(date=date(2026, 5, 18), title='Soap', category='Home', unit_price=4.0, quantity=1, amount=4.0, payer='Bob'),
        ])
        db.session.commit()

        payload = _build_month_payload(2026, 5)

    assert sorted(payload['by_date'].keys()) == ['2026-05-02', '2026-05-18']
    assert all(payload['by_date'][key]['entries'] for key in payload['by_date'])
    assert payload['settings']['fraction_factor'] == 1


def test_recurring_edit_apply_from_keeps_history(client):
    with client.application.app_context():
        rule = RecurringExpense(
            title='Milk',
            category='Food',
            unit_price=10.0,
            default_quantity=4.0,
            frequency='daily',
            monthly_mode='day_of_month',
            start_date=date(2026, 5, 1),
            end_date=date(2026, 8, 31),
            last_generated_date=date(2026, 6, 4),
            creator='Alice',
        )
        db.session.add(rule)
        db.session.flush()
        db.session.add_all([
            ExpenseEntry(date=date(2026, 6, 2), title='Milk', category='Food', unit_price=10.0, quantity=4.0, amount=40.0, payer='Alice', recurring_id=rule.id),
            ExpenseEntry(date=date(2026, 6, 3), title='Milk', category='Food', unit_price=10.0, quantity=4.0, amount=40.0, payer='Alice', recurring_id=rule.id),
            ExpenseEntry(date=date(2026, 6, 4), title='Milk', category='Food', unit_price=10.0, quantity=4.0, amount=40.0, payer='Alice', recurring_id=rule.id),
        ])
        db.session.commit()
        rid = rule.id

    with client.session_transaction() as sess:
        sess['authed'] = True

    client.post(
        f'/expenses/recurring/edit/{rid}',
        data={
            'user': 'Alice',
            'title': 'Milk',
            'category': 'Food',
            'unit_price': '10',
            'default_quantity': '3',
            'frequency': 'daily',
            'monthly_mode': 'day_of_month',
            'start_date': '2026-05-01',
            'end_date': '2026-08-31',
            'edit_strategy': 'apply_from',
            'effective_from': '2026-06-03',
        },
    )

    with client.application.app_context():
        before = ExpenseEntry.query.filter_by(recurring_id=rid, date=date(2026, 6, 2)).first()
        on_or_after = ExpenseEntry.query.filter_by(recurring_id=rid, date=date(2026, 6, 3)).first()
        rule = RecurringExpense.query.get(rid)

    assert before is not None
    assert before.quantity == 4.0
    assert on_or_after is not None
    assert on_or_after.quantity == 3.0
    assert rule.effective_from == date(2026, 6, 3)


def test_recurring_edit_split_rule_preserves_old_history(client):
    with client.application.app_context():
        rule = RecurringExpense(
            title='Newspaper',
            category='Home',
            unit_price=5.0,
            default_quantity=1.0,
            frequency='daily',
            monthly_mode='day_of_month',
            start_date=date(2026, 5, 1),
            end_date=None,
            last_generated_date=date(2026, 6, 4),
            creator='Alice',
        )
        db.session.add(rule)
        db.session.flush()
        db.session.add_all([
            ExpenseEntry(date=date(2026, 6, 2), title='Newspaper', category='Home', unit_price=5.0, quantity=1.0, amount=5.0, payer='Alice', recurring_id=rule.id),
            ExpenseEntry(date=date(2026, 6, 4), title='Newspaper', category='Home', unit_price=5.0, quantity=1.0, amount=5.0, payer='Alice', recurring_id=rule.id),
        ])
        db.session.commit()
        rid = rule.id

    with client.session_transaction() as sess:
        sess['authed'] = True

    client.post(
        f'/expenses/recurring/edit/{rid}',
        data={
            'user': 'Alice',
            'title': 'Newspaper',
            'category': 'Home',
            'unit_price': '6',
            'default_quantity': '1',
            'frequency': 'daily',
            'monthly_mode': 'day_of_month',
            'start_date': '2026-06-03',
            'end_date': '',
            'edit_strategy': 'split_rule',
            'effective_from': '2026-06-03',
        },
    )

    with client.application.app_context():
        old_rule = RecurringExpense.query.get(rid)
        new_rules = RecurringExpense.query.filter(RecurringExpense.id != rid, RecurringExpense.title == 'Newspaper').all()
        old_future = ExpenseEntry.query.filter_by(recurring_id=rid, date=date(2026, 6, 4)).first()

    assert old_rule.end_date == date(2026, 6, 2)
    assert len(new_rules) == 1
    assert new_rules[0].start_date == date(2026, 6, 3)
    assert old_future is None


def test_recurring_delete_checked_removes_generated_entries(client):
    with client.application.app_context():
        rule = RecurringExpense(
            title='Milk',
            category='Food',
            unit_price=10.0,
            default_quantity=2.0,
            frequency='daily',
            monthly_mode='day_of_month',
            start_date=date(2026, 5, 1),
            end_date=None,
            creator='Alice',
        )
        db.session.add(rule)
        db.session.flush()
        db.session.add(
            ExpenseEntry(
                date=date(2026, 5, 10),
                title='Milk',
                category='Food',
                unit_price=10.0,
                quantity=2.0,
                amount=20.0,
                payer='Alice',
                recurring_id=rule.id,
            )
        )
        db.session.commit()
        rid = rule.id

    with client.session_transaction() as sess:
        sess['authed'] = True

    client.post(f'/expenses/recurring/delete/{rid}', data={'user': 'Alice', 'delete_entries': '1'})

    with client.application.app_context():
        deleted_rule = RecurringExpense.query.get(rid)
        linked_entry = ExpenseEntry.query.filter_by(recurring_id=rid).first()

    assert deleted_rule is None
    assert linked_entry is None


def test_recurring_delete_unchecked_keeps_generated_entries(client):
    with client.application.app_context():
        rule = RecurringExpense(
            title='Bread',
            category='Food',
            unit_price=5.0,
            default_quantity=1.0,
            frequency='daily',
            monthly_mode='day_of_month',
            start_date=date(2026, 5, 1),
            end_date=None,
            creator='Alice',
        )
        db.session.add(rule)
        db.session.flush()
        db.session.add(
            ExpenseEntry(
                date=date(2026, 5, 10),
                title='Bread',
                category='Food',
                unit_price=5.0,
                quantity=1.0,
                amount=5.0,
                payer='Alice',
                recurring_id=rule.id,
            )
        )
        db.session.commit()
        rid = rule.id

    with client.session_transaction() as sess:
        sess['authed'] = True

    client.post(f'/expenses/recurring/delete/{rid}', data={'user': 'Alice'})

    with client.application.app_context():
        deleted_rule = RecurringExpense.query.get(rid)
        kept_entry = ExpenseEntry.query.filter_by(recurring_id=rid).first()

    assert deleted_rule is None
    assert kept_entry is not None


def test_split_rule_at_start_falls_back_to_apply_from(client):
    with client.application.app_context():
        rule = RecurringExpense(
            title='Water',
            category='Home',
            unit_price=3.0,
            default_quantity=1.0,
            frequency='daily',
            monthly_mode='day_of_month',
            start_date=date(2026, 5, 1),
            end_date=None,
            last_generated_date=date(2026, 5, 2),
            creator='Alice',
        )
        db.session.add(rule)
        db.session.flush()
        db.session.add_all([
            ExpenseEntry(date=date(2026, 5, 1), title='Water', category='Home', unit_price=3.0, quantity=1.0, amount=3.0, payer='Alice', recurring_id=rule.id),
            ExpenseEntry(date=date(2026, 5, 2), title='Water', category='Home', unit_price=3.0, quantity=1.0, amount=3.0, payer='Alice', recurring_id=rule.id),
        ])
        db.session.commit()
        rid = rule.id

    with client.session_transaction() as sess:
        sess['authed'] = True

    client.post(
        f'/expenses/recurring/edit/{rid}',
        data={
            'user': 'Alice',
            'title': 'Water',
            'category': 'Home',
            'unit_price': '4',
            'default_quantity': '2',
            'frequency': 'daily',
            'monthly_mode': 'day_of_month',
            'start_date': '2026-05-01',
            'end_date': '',
            'edit_strategy': 'split_rule',
            'effective_from': '2026-05-01',
        },
    )

    with client.application.app_context():
        rules = RecurringExpense.query.filter_by(title='Water').all()
        original = RecurringExpense.query.get(rid)

    assert len(rules) == 1
    assert original is not None
    assert original.start_date == date(2026, 5, 1)
    assert original.end_date is None


def test_apply_from_effective_date_clamped_to_start_date(client):
    with client.application.app_context():
        rule = RecurringExpense(
            title='Gas',
            category='Home',
            unit_price=8.0,
            default_quantity=1.0,
            frequency='daily',
            monthly_mode='day_of_month',
            start_date=date(2026, 5, 10),
            end_date=date(2026, 5, 31),
            creator='Alice',
        )
        db.session.add(rule)
        db.session.commit()
        rid = rule.id

    with client.session_transaction() as sess:
        sess['authed'] = True

    client.post(
        f'/expenses/recurring/edit/{rid}',
        data={
            'user': 'Alice',
            'title': 'Gas',
            'category': 'Home',
            'unit_price': '8',
            'default_quantity': '2',
            'frequency': 'daily',
            'monthly_mode': 'day_of_month',
            'start_date': '2026-05-10',
            'end_date': '2026-05-31',
            'edit_strategy': 'apply_from',
            'effective_from': '2026-05-01',
        },
    )

    with client.application.app_context():
        updated = RecurringExpense.query.get(rid)

    assert updated.effective_from == date(2026, 5, 10)


def test_apply_from_effective_date_clamped_to_end_date(client):
    with client.application.app_context():
        rule = RecurringExpense(
            title='Phone',
            category='Utilities',
            unit_price=30.0,
            default_quantity=1.0,
            frequency='daily',
            monthly_mode='day_of_month',
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 20),
            creator='Alice',
        )
        db.session.add(rule)
        db.session.commit()
        rid = rule.id

    with client.session_transaction() as sess:
        sess['authed'] = True

    client.post(
        f'/expenses/recurring/edit/{rid}',
        data={
            'user': 'Alice',
            'title': 'Phone',
            'category': 'Utilities',
            'unit_price': '30',
            'default_quantity': '1',
            'frequency': 'daily',
            'monthly_mode': 'day_of_month',
            'start_date': '2026-05-01',
            'end_date': '2026-05-20',
            'edit_strategy': 'apply_from',
            'effective_from': '2026-05-31',
        },
    )

    with client.application.app_context():
        updated = RecurringExpense.query.get(rid)

    assert updated.effective_from == date(2026, 5, 20)
