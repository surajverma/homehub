import json
from datetime import date, timedelta

import pytest

from app import create_app, db


def make_app():
    test_config = {
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite://',
        'HOMEHUB_CONFIG': {
            'admin_name': 'Administrator',
            'family_members': ['Alice', 'Bob'],
            'reminders': {'calendar_start_day': 'sunday'}
        },
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'test',
    }
    app = create_app(test_config)
    with app.app_context():
        db.create_all()
    return app


@pytest.fixture()
def client():
    app = make_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['authed'] = True
    return client


def list_month(client, y, m):
    base = date(y, m, 1)
    resp = client.get(f"/api/reminders?scope=month&date={base.strftime('%Y-%m-%d')}")
    assert resp.status_code == 200
    return resp.get_json()


def assert_dates_for_title(data, title, expected_dates):
    rems = [r for r in data['reminders'] if r['title'] == title]
    got = sorted(set(r['date'] for r in rems))
    assert sorted(set(expected_dates)) == got


def test_biweekly_generation(client):
    anchor = date(2025, 10, 1)
    payload = {
        'date': anchor.strftime('%Y-%m-%d'),
        'title': 'Biweekly',
        'description': 'x',
        'creator': 'Alice',
        'recurring': {'interval': 2, 'unit': 'week'}
    }
    r = client.post('/api/reminders', json=payload)
    assert r.status_code == 200
    # For October 2025, expect occurrences on 1st, 15th, and 29th
    data = list_month(client, 2025, 10)
    expected = [
        '2025-10-01',
        '2025-10-15',
        '2025-10-29',
    ]
    assert_dates_for_title(data, 'Biweekly', expected)


def test_monthly_clamp_end_of_month(client):
    # Jan 31 monthly -> Feb 28 (non-leap year)
    anchor = date(2025, 1, 31)
    payload = {
        'date': anchor.strftime('%Y-%m-%d'),
        'title': 'MonthlyClamp',
        'description': 'x',
        'creator': 'Alice',
        'recurring': {'interval': 1, 'unit': 'month'}
    }
    r = client.post('/api/reminders', json=payload)
    assert r.status_code == 200
    feb = list_month(client, 2025, 2)
    assert_dates_for_title(feb, 'MonthlyClamp', ['2025-02-28'])


def test_yearly_leap_day_fallback(client):
    # Start at leap day 2024-02-29; next year should be 2025-02-28
    anchor = date(2024, 2, 29)
    payload = {
        'date': anchor.strftime('%Y-%m-%d'),
        'title': 'LeapYear',
        'description': 'x',
        'creator': 'Alice',
        'recurring': {'interval': 1, 'unit': 'year'}
    }
    r = client.post('/api/reminders', json=payload)
    assert r.status_code == 200
    feb25 = list_month(client, 2025, 2)
    assert_dates_for_title(feb25, 'LeapYear', ['2025-02-28'])


def test_end_date_inclusive(client):
    anchor = date(2025, 10, 10)
    end = anchor + timedelta(days=2)  # 10,11,12
    payload = {
        'date': anchor.strftime('%Y-%m-%d'),
        'title': 'DailyShort',
        'description': 'x',
        'creator': 'Alice',
        'recurring': {'interval': 1, 'unit': 'day', 'end_date': end.strftime('%Y-%m-%d')}
    }
    r = client.post('/api/reminders', json=payload)
    assert r.status_code == 200
    data = list_month(client, 2025, 10)
    expected = ['2025-10-10', '2025-10-11', '2025-10-12']
    assert_dates_for_title(data, 'DailyShort', expected)
    # and ensure next day not present
    rems = [r for r in data['reminders'] if r['title'] == 'DailyShort']
    assert '2025-10-13' not in [r['date'] for r in rems]


def test_legacy_frequency_compat(client):
    anchor = date(2025, 10, 3)
    payload = {
        'date': anchor.strftime('%Y-%m-%d'),
        'title': 'LegacyWeekly',
        'description': 'x',
        'creator': 'Alice',
        'recurring': {'frequency': 'weekly'}
    }
    r = client.post('/api/reminders', json=payload)
    assert r.status_code == 200
    # Expect 10/3, 10/10, 10/17, 10/24, 10/31 within October 2025
    data = list_month(client, 2025, 10)
    expected = ['2025-10-03','2025-10-10','2025-10-17','2025-10-24','2025-10-31']
    assert_dates_for_title(data, 'LegacyWeekly', expected)


def test_one_time_reminder_with_optional_end_fields(client):
    payload = {
        'title': 'Multi-day Event',
        'creator': 'Alice',
        'start_date': '2026-07-31',
        'start_time': '09:30',
        'end_date': '2026-08-02',
        'end_time': '18:00',
        'description': 'Trip',
    }
    r = client.post('/api/reminders', json=payload)
    assert r.status_code == 200
    j = r.get_json()
    assert j['ok'] is True
    rem = j['reminder']
    assert rem['date'] == '2026-07-31'
    assert rem['time'] == '09:30'
    assert rem['start_date'] == '2026-07-31'
    assert rem['start_time'] == '09:30'
    assert rem['end_date'] == '2026-08-02'
    assert rem['end_time'] == '18:00'
    assert rem['all_day'] is False


def test_no_times_defaults_to_all_day(client):
    payload = {
        'title': 'Festival',
        'creator': 'Alice',
        'start_date': '2026-08-05',
    }
    r = client.post('/api/reminders', json=payload)
    assert r.status_code == 200
    rem = r.get_json()['reminder']
    assert rem['date'] == '2026-08-05'
    assert rem['time'] is None
    assert rem['start_time'] is None
    assert rem['end_time'] is None
    assert rem['all_day'] is True


def test_date_range_highlights_all_days_in_month_counts(client):
    payload = {
        'title': 'Trip',
        'creator': 'Alice',
        'start_date': '2026-07-23',
        'end_date': '2026-07-31',
    }
    r = client.post('/api/reminders', json=payload)
    assert r.status_code == 200
    data = list_month(client, 2026, 7)
    for day in range(23, 32):
        key = f"2026-07-{day:02d}"
        assert data['counts'].get(key, 0) >= 1


def test_end_before_start_rejected(client):
    payload = {
        'title': 'Bad range',
        'creator': 'Alice',
        'start_date': '2026-08-10',
        'start_time': '12:00',
        'end_date': '2026-08-10',
        'end_time': '11:59',
    }
    r = client.post('/api/reminders', json=payload)
    assert r.status_code == 400
    j = r.get_json()
    assert j['ok'] is False


def test_patch_invalid_date_time_values_preserve_existing_values(client):
    create_resp = client.post('/api/reminders', json={
        'title': 'Keep Existing Values',
        'creator': 'Alice',
        'start_date': '2026-07-31',
        'start_time': '09:30',
        'end_date': '2026-08-02',
        'end_time': '18:00',
    })
    assert create_resp.status_code == 200
    rid = create_resp.get_json()['reminder']['id']

    patch_resp = client.patch(f'/api/reminders/{rid}', json={
        'creator': 'Alice',
        'start_time': 'not-a-time',
        'end_date': 'not-a-date',
        'end_time': 'bad-time',
    })
    assert patch_resp.status_code == 200
    reminder = patch_resp.get_json()['reminder']
    assert reminder['start_time'] == '09:30'
    assert reminder['end_date'] == '2026-08-02'
    assert reminder['end_time'] == '18:00'


def test_restore_rejects_non_trashed_reminder(client):
    create_resp = client.post('/api/reminders', json={
        'title': 'Restore Guard',
        'creator': 'Alice',
        'start_date': '2026-08-01',
    })
    assert create_resp.status_code == 200
    rid = create_resp.get_json()['reminder']['id']

    restore_resp = client.post(f'/api/reminders/{rid}/restore', json={'creator': 'Alice'})
    assert restore_resp.status_code == 400
    assert restore_resp.get_json()['ok'] is False


def test_purge_rejects_non_trashed_reminder(client):
    create_resp = client.post('/api/reminders', json={
        'title': 'Purge Guard',
        'creator': 'Alice',
        'start_date': '2026-08-01',
    })
    assert create_resp.status_code == 200
    rid = create_resp.get_json()['reminder']['id']

    purge_resp = client.delete(f'/api/reminders/{rid}/purge', json={'creator': 'Alice'})
    assert purge_resp.status_code == 400
    assert purge_resp.get_json()['ok'] is False


def test_calendar_module_page_is_available(client):
    r = client.get('/calendar')
    assert r.status_code == 200


