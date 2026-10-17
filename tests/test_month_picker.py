import os
import re

import pytest

from app import create_app, db, i18n


@pytest.fixture()
def client():
    app = create_app({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite://',
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'test',
    })
    with app.app_context():
        db.create_all()
    return app.test_client()


def _script_order(html):
    return re.findall(r'<script src="/static/js/([^"?]+)', html)


@pytest.mark.parametrize('path', ['/', '/calendar'])
def test_reminders_month_label_is_a_picker_button(client, path):
    html = client.get(path).get_data(as_text=True)
    assert re.search(r'<button type="button" id="calLabel" class="month-picker-label[^"]*" title="Choose month and year">', html)
    scripts = _script_order(html)
    assert 'month-picker.js' in scripts
    assert scripts.index('month-picker.js') < scripts.index('pages/home/reminders.js')


def test_expenses_month_label_is_a_picker_button(client):
    html = client.get('/expenses').get_data(as_text=True)
    assert re.search(r'<h2 class="text-lg font-semibold"><button type="button" id="calendar-header" class="month-picker-label" title="Choose month and year">', html)
    scripts = _script_order(html)
    assert 'month-picker.js' in scripts
    assert scripts.index('month-picker.js') < scripts.index('pages/expenses.js')


def test_month_picker_strings_are_translatable():
    with open(os.path.join(i18n.JS_DIR, 'month-picker.js'), encoding='utf-8') as f:
        found = {text for _line, _kind, _context, text, _plural in i18n.find_js_messages(f.read())}
    assert {'Choose month and year', 'Previous year', 'Next year', 'This month'} <= found
