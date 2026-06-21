from datetime import date

import pytest

from app import create_app, db
from app.blueprints.expenses import _build_month_payload, _load_expense_settings
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
    assert payload['settings']['fraction_factor'] == 100
