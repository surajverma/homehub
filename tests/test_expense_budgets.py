from datetime import date

import pytest

from app import create_app, db
from app.blueprints.expenses import budget_status, budgets_over, household_budgets
from app.models import ExpenseBudget, ExpenseEntry


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
        db.session.execute(db.text("INSERT INTO app_setting(key, value) VALUES('categories', 'Food, Home')"))
        db.session.commit()
    return app


@pytest.fixture()
def client():
    return make_app().test_client()


def save_budgets(client, user='Administrator', **amounts):
    """amounts: overall=..., and category names; '' leaves one without a budget."""
    categories, values = [], []
    for name, value in amounts.items():
        categories.append('' if name == 'overall' else name)
        values.append(str(value))
    return client.post('/expenses/budgets', data={'user': user, 'budget_category': categories, 'budget_amount': values})


def budgets(client):
    with client.application.app_context():
        return {(b.category, b.member): b.amount for b in ExpenseBudget.query.all()}


def add(client, **values):
    with client.application.app_context():
        db.session.add(ExpenseEntry(**values))
        db.session.commit()


def test_admin_saves_overall_and_category_budgets(client):
    save_budgets(client, overall=5000, Food=2000, Home='')
    assert budgets(client) == {(None, None): 5000.0, ('Food', None): 2000.0}
    # Saving again replaces them; an emptied box removes that budget
    save_budgets(client, overall='', Food=2500)
    assert budgets(client) == {('Food', None): 2500.0}


def test_only_admin_saves_budgets(client):
    save_budgets(client, user='Alice', overall=100)
    assert budgets(client) == {}


@pytest.mark.parametrize('bad', ['-5', 'abc', 'inf'])
def test_invalid_budget_is_refused(client, bad):
    save_budgets(client, overall=100)
    resp = client.post('/expenses/budgets', data={'user': 'Administrator', 'budget_category': ['', 'Food'],
                                                  'budget_amount': ['200', bad]}, follow_redirects=True)
    assert 'Invalid budget for Food.' in resp.get_data(as_text=True)
    assert budgets(client) == {(None, None): 100.0}


def test_per_person_budgets_are_left_alone(client):
    # Rows with a member are for per-person budgets later; saving the household's must not touch them
    with client.application.app_context():
        db.session.add(ExpenseBudget(category=None, member='Bob', amount=300))
        db.session.commit()
    save_budgets(client, overall=1000)
    assert budgets(client) == {(None, 'Bob'): 300.0, (None, None): 1000.0}
    with client.application.app_context():
        assert [(b.category, b.member) for b in household_budgets()] == [(None, None)]


def test_status_levels(client):
    save_budgets(client, overall=1000, Food=100, Home=200)
    with client.application.app_context():
        status = {b['category']: b for b in budget_status(700, {'Food': 120, 'Home': 160})}
    assert (status[None]['level'], status[None]['percent']) == ('ok', 70)
    assert (status['Home']['level'], status['Home']['percent']) == ('near', 80)
    assert (status['Food']['level'], status['Food']['percent']) == ('over', 120)
    # Exactly the budget is not over yet
    with client.application.app_context():
        assert budget_status(1000, {})[0]['level'] == 'near'


def test_month_payload_counts_spending_but_not_skips_or_settlements(client):
    save_budgets(client, overall=100, Food=50)
    add(client, date=date(2026, 10, 2), title='Milk', category='Food', amount=60, payer='Alice')
    add(client, date=date(2026, 10, 3), title='Paper', category='Food', amount=10, payer='Alice', recurring_id=9, skipped=True)
    add(client, date=date(2026, 10, 4), title='Settle', amount=500, payer='Bob', is_settlement=True, split_with='["Alice"]')
    add(client, date=date(2026, 11, 1), title='Next month', category='Food', amount=99, payer='Alice')
    data = client.get('/api/expenses/month?year=2026&month=10').get_json()
    status = {b['category']: b for b in data['budgets']}
    assert status[None]['spent'] == 60 and status[None]['level'] == 'ok'
    assert status['Food']['spent'] == 60 and status['Food']['level'] == 'over'


def test_spending_without_a_category_is_its_own_bucket(client):
    save_budgets(client, overall=1000, Food=50)
    add(client, date=date(2026, 10, 2), title='Milk', category='Food', amount=40, payer='Alice')
    add(client, date=date(2026, 10, 3), title='Odd job', amount=300, payer='Alice')
    add(client, date=date(2026, 10, 4), title='Settle', amount=500, payer='Bob', is_settlement=True, split_with='["Alice"]')
    data = client.get('/api/expenses/month?year=2026&month=10').get_json()
    assert data['summary']['uncategorised'] == 300
    assert data['summary']['per_category'] == {'Food': 40}
    status = {b['category']: b for b in data['budgets']}
    # Counted in the overall budget, never in a category's
    assert status[None]['spent'] == 340 and status['Food']['spent'] == 40


def test_welcome_note_only_when_over(client):
    today = date.today()
    save_budgets(client, overall=100)
    assert 'id="budgetAlert"' not in client.get('/').get_data(as_text=True)
    add(client, date=today, title='Big shop', category='Food', amount=150, payer='Alice')
    html = client.get('/').get_data(as_text=True)
    assert 'id="budgetAlert"' in html
    assert 'Overall: ₹150.00 of ₹100.00' in html
    with client.application.app_context():
        assert [b['category'] for b in budgets_over(today)] == [None]


def test_no_budgets_no_card_data(client):
    data = client.get('/api/expenses/month?year=2026&month=10').get_json()
    assert data['budgets'] == []
    assert 'id="budgetAlert"' not in client.get('/').get_data(as_text=True)


def test_settings_page_lists_categories_for_budgets(client):
    save_budgets(client, overall=5000, Travel=800)
    html = client.get('/expenses/recurring?tab=general-settings').get_data(as_text=True)
    assert 'id="budgets-form"' in html
    # Configured categories, plus a budgeted category that is no longer in the list
    for name in ('Food', 'Home', 'Travel'):
        assert f'value="{name}"' in html
    assert 'value="5000.0"' in html and 'value="800.0"' in html


def test_expenses_page_has_budget_and_chart_cards(client):
    html = client.get('/expenses').get_data(as_text=True)
    assert 'id="budgets-card"' in html and 'id="charts-card"' in html
    assert 'expense_charts.js' in html
    # A summary line instead of the cards, charts above the budgets and the calendar, settings as a quiet link
    assert 'id="summary-line"' in html and 'Top Category' not in html
    assert html.index('id="summary-line"') < html.index('id="charts-card"') < html.index('id="budgets-card"') < html.index('id="calendar-grid"')
    assert 'class="btn btn-sm chart-tab w-full justify-center" data-chart="categories"' in html
    assert 'href="/expenses/recurring"' in html


def test_migration_creates_budget_table(tmp_path):
    import sqlite3
    from app import migrations
    path = tmp_path / 'old.db'
    sqlite3.connect(path).close()
    migrations.run(str(path))
    conn = sqlite3.connect(path)
    cols = [r[1] for r in conn.execute('PRAGMA table_info(expense_budget)')]
    conn.close()
    assert cols == ['id', 'category', 'member', 'amount', 'updated_by', 'updated_at']
