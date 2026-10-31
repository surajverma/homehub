"""Read-only calendar subscription feed (/calendar.ics): off by default, token-gated, and
showing the same reminder dates as HomeHub's own calendar."""
import os
import re
from collections import Counter
from datetime import date, timedelta

import pytest
import yaml

from app import config as config_module
from app import create_app, db
from app.blueprints import calendar_feed
from app.models import Reminder, RecurringReminder

TOKEN = 'test-token-0123456789abcdefghij'
# The feed window is counted from "today". Tests freeze it, so fixed dates such as
# 2026-11-20 stay inside the window whatever the real date is.
TODAY = date(2026, 11, 15)
REAL_FEED_TODAY = calendar_feed.feed_today


def write_config(path, token=None, password=None):
    with open(config_module.EXAMPLE_CONFIG_PATH, encoding='utf-8') as fh:
        cfg = yaml.safe_load(fh)
    if token is not None:
        cfg['calendar_feed'] = {'token': token}
    if password:
        cfg['password'] = password
    with open(path, 'w', encoding='utf-8') as fh:
        yaml.safe_dump(cfg, fh, allow_unicode=True)
    # The config cache keys on mtime and size; make sure a rewrite is seen
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


@pytest.fixture()
def configure(tmp_path, monkeypatch):
    path = str(tmp_path / 'config.yml')

    def _configure(token=None, password=None):
        write_config(path, token=token, password=password)
        monkeypatch.setattr(config_module, 'CONFIG_PATH', path)
    return _configure


@pytest.fixture()
def app():
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite://', 'SECRET_KEY': 'test'})
    with app.app_context():
        db.create_all()
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def floating_by_default(monkeypatch):
    monkeypatch.delenv('TZ', raising=False)


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    monkeypatch.setattr(calendar_feed, 'feed_today', lambda: TODAY)


def test_feed_today_is_the_real_date():
    assert REAL_FEED_TODAY() == date.today()


def feed(client, token=TOKEN):
    resp = client.get(f'/calendar.ics?token={token}')
    assert resp.status_code == 200, resp.status_code
    return resp.get_data(as_text=True)


def unfold(body):
    return body.replace('\r\n ', '')


def events(body):
    """Each VEVENT as a dict of property line name (with params) -> value."""
    out = []
    for block in re.findall(r'BEGIN:VEVENT\r\n(.*?)END:VEVENT', unfold(body), re.S):
        props = {}
        for line in block.split('\r\n'):
            if line:
                name, _, value = line.partition(':')
                props[name] = value
        out.append(props)
    return out


def by_summary(body, summary):
    return [e for e in events(body) if e.get('SUMMARY') == summary]


def add_reminder(client, **payload):
    payload.setdefault('creator', 'Mom')
    resp = client.post('/api/reminders', json=payload)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


# --- Off by default and token checks ---------------------------------------------------

def test_feed_is_off_by_default(client):
    # The example config ships with an empty token
    assert client.get('/calendar.ics').status_code == 404
    assert client.get(f'/calendar.ics?token={TOKEN}').status_code == 404
    assert client.get('/calendar.ics?token=').status_code == 404


def test_feed_without_calendar_feed_section_is_off(client, configure, tmp_path):
    configure()  # no calendar_feed key at all, like an existing install's config.yml
    assert client.get(f'/calendar.ics?token={TOKEN}').status_code == 404


@pytest.mark.parametrize('token', ['', '   ', 'short-token'])
def test_empty_or_short_configured_token_keeps_feed_off(client, configure, token):
    configure(token=token)
    assert client.get(f'/calendar.ics?token={token}').status_code == 404


def test_right_token_returns_calendar(client, configure):
    configure(token=TOKEN)
    resp = client.get(f'/calendar.ics?token={TOKEN}')
    assert resp.status_code == 200
    assert resp.headers['Content-Type'] == 'text/calendar; charset=utf-8'
    assert resp.headers['Cache-Control'] == 'no-store'
    body = resp.get_data(as_text=True)
    assert body.startswith('BEGIN:VCALENDAR\r\nVERSION:2.0\r\n')
    assert body.endswith('END:VCALENDAR\r\n')
    assert 'X-WR-CALNAME:My Home Hub\r\n' in body
    assert 'REFRESH-INTERVAL;VALUE=DURATION:PT1H\r\n' in body
    assert 'X-PUBLISHED-TTL:PT1H\r\n' in body
    assert 'Set-Cookie' not in resp.headers


@pytest.mark.parametrize('query', ['', '?token=', '?token=wrong', f'?token={TOKEN}x', f'?token={TOKEN[:-1]}',
                                   '?token=%E0%A4%B9%E0%A4%BF', f'?tok={TOKEN}'])
def test_wrong_or_missing_token_is_plain_404(client, configure, query):
    configure(token=TOKEN)
    resp = client.get('/calendar.ics' + query)
    assert resp.status_code == 404
    assert 'VCALENDAR' not in resp.get_data(as_text=True)


def test_changing_token_revokes_old_link_without_restart(client, configure):
    configure(token=TOKEN)
    assert client.get(f'/calendar.ics?token={TOKEN}').status_code == 200
    new = 'another-token-abcdefghijklmnop'
    configure(token=new)
    assert client.get(f'/calendar.ics?token={TOKEN}').status_code == 404
    assert client.get(f'/calendar.ics?token={new}').status_code == 200
    configure(token='')
    assert client.get(f'/calendar.ics?token={new}').status_code == 404


def test_numeric_token_in_yaml_works(client, configure):
    configure(token=12345678901234567890)
    assert client.get('/calendar.ics?token=12345678901234567890').status_code == 200


def test_only_get_is_served(client, configure):
    configure(token=TOKEN)
    for method in ('post', 'put', 'patch', 'delete'):
        resp = getattr(client, method)(f'/calendar.ics?token={TOKEN}')
        assert resp.status_code == 405


# --- Site password gate ----------------------------------------------------------------

def _url_for_rule(rule):
    values = {}
    for arg in rule.arguments:
        conv = rule._converters.get(arg)
        values[arg] = 1 if conv is not None and type(conv).__name__ in ('IntegerConverter', 'FloatConverter') else 'x'
    path = rule.rule
    for arg, value in values.items():
        path = re.sub(r'<(?:[^:<>]+:)?' + re.escape(arg) + r'>', str(value), path)
    return path


def test_site_password_still_gates_every_other_route(app, client, configure):
    configure(token=TOKEN, password='secret-site-password')
    open_endpoints = {'main.login', 'main.healthz', 'main.calendar_feed', 'static'}
    checked = 0
    for rule in app.url_map.iter_rules():
        if rule.endpoint in open_endpoints:
            continue
        url = _url_for_rule(rule)
        for method in sorted(rule.methods - {'HEAD', 'OPTIONS'}):
            resp = client.open(url, method=method, headers={'Sec-Fetch-Site': 'same-origin'})
            assert resp.status_code == 302, (method, url, resp.status_code)
            assert resp.headers['Location'].endswith('/login'), (method, url)
            checked += 1
    assert checked > 50


def test_feed_works_without_login_when_site_password_set(client, configure):
    configure(token=TOKEN, password='secret-site-password')
    add_reminder_direct(client.application, title='Dentist', d=TODAY)
    resp = client.get(f'/calendar.ics?token={TOKEN}')
    assert resp.status_code == 200
    assert 'SUMMARY:Dentist\r\n' in resp.get_data(as_text=True)
    assert 'Set-Cookie' not in resp.headers


@pytest.mark.parametrize('query', ['', '?token=', '?token=wrong'])
def test_bad_token_with_site_password_is_404_not_login_redirect(client, configure, query):
    configure(token=TOKEN, password='secret-site-password')
    assert client.get('/calendar.ics' + query).status_code == 404


def test_feed_off_with_site_password_is_404(client, configure):
    configure(token='', password='secret-site-password')
    assert client.get(f'/calendar.ics?token={TOKEN}').status_code == 404


def test_writes_to_feed_url_still_need_login_with_site_password(client, configure):
    configure(token=TOKEN, password='secret-site-password')
    resp = client.post(f'/calendar.ics?token={TOKEN}', headers={'Sec-Fetch-Site': 'same-origin'})
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/login')


def test_feed_does_not_unlock_other_pages(client, configure):
    configure(token=TOKEN, password='secret-site-password')
    assert client.get(f'/calendar.ics?token={TOKEN}').status_code == 200
    resp = client.get('/api/reminders?scope=month')
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/login')


def test_cross_site_write_check_unaffected(app, client, configure):
    configure(token=TOKEN)
    from app.models import Note
    with app.app_context():
        note = Note(content='hello', creator='Mom')
        db.session.add(note)
        db.session.commit()
        note_id = note.id
    resp = client.post(f'/notes/delete/{note_id}', data={'user': 'Mom'},
                       headers={'Sec-Fetch-Site': 'cross-site', 'Origin': 'https://example.com'})
    assert resp.status_code == 403
    with app.app_context():
        assert db.session.get(Note, note_id) is not None
    # A cross-site write to the feed URL is refused before anything else
    resp = client.post(f'/calendar.ics?token={TOKEN}', headers={'Sec-Fetch-Site': 'cross-site'})
    assert resp.status_code == 403
    # Calendar apps send GETs, which are never treated as cross-site writes
    resp = client.get(f'/calendar.ics?token={TOKEN}', headers={'Sec-Fetch-Site': 'cross-site'})
    assert resp.status_code == 200


# --- Content ---------------------------------------------------------------------------

def add_reminder_direct(app, title, d, **fields):
    with app.app_context():
        r = Reminder(date=d, start_date=d, title=title, **fields)
        db.session.add(r)
        db.session.commit()
        return r.id


def test_trashed_excluded_completed_included(app, client, configure):
    configure(token=TOKEN)
    from app.clock import utcnow
    today = TODAY
    add_reminder_direct(app, 'Kept', today)
    add_reminder_direct(app, 'Done', today, completed_at=utcnow())
    add_reminder_direct(app, 'Trashed', today, deleted_at=utcnow())
    body = feed(client)
    assert by_summary(body, 'Kept')
    assert by_summary(body, 'Done')
    assert not by_summary(body, 'Trashed')


def test_window_is_60_days_back_to_365_ahead(app, client, configure):
    configure(token=TOKEN)
    today = TODAY
    for title, offset in (('Back60', -60), ('Back61', -61), ('Ahead365', 365), ('Ahead366', 366)):
        add_reminder_direct(app, title, today + timedelta(days=offset))
    body = feed(client)
    assert by_summary(body, 'Back60') and by_summary(body, 'Ahead365')
    assert not by_summary(body, 'Back61') and not by_summary(body, 'Ahead366')


def test_all_day_reminder(client, configure):
    configure(token=TOKEN)
    add_reminder(client, title='Holiday', start_date='2026-11-20')
    (ev,) = by_summary(feed(client), 'Holiday')
    assert ev['DTSTART;VALUE=DATE'] == '20261120'
    assert ev['DTEND;VALUE=DATE'] == '20261121'


def test_multi_day_all_day_reminder(client, configure):
    configure(token=TOKEN)
    add_reminder(client, title='Trip', start_date='2026-11-20', end_date='2026-11-23')
    (ev,) = by_summary(feed(client), 'Trip')
    assert ev['DTSTART;VALUE=DATE'] == '20261120'
    assert ev['DTEND;VALUE=DATE'] == '20261124'


def test_timed_reminder_with_end(client, configure):
    configure(token=TOKEN)
    add_reminder(client, title='Meeting', start_date='2026-11-20', start_time='09:15',
                 end_date='2026-11-20', end_time='10:45')
    (ev,) = by_summary(feed(client), 'Meeting')
    assert ev['DTSTART'] == '20261120T091500'
    assert ev['DTEND'] == '20261120T104500'


def test_timed_reminder_without_end_lasts_30_minutes(client, configure):
    configure(token=TOKEN)
    add_reminder(client, title='Pills', start_date='2026-11-20', start_time='23:45')
    (ev,) = by_summary(feed(client), 'Pills')
    assert ev['DTSTART'] == '20261120T234500'
    assert ev['DTEND'] == '20261121T001500'


def test_timed_reminder_with_end_date_only_runs_to_end_of_that_day(client, configure):
    configure(token=TOKEN)
    add_reminder(client, title='Conference', start_date='2026-11-20', start_time='09:00', end_date='2026-11-21')
    (ev,) = by_summary(feed(client), 'Conference')
    assert ev['DTSTART'] == '20261120T090000'
    assert ev['DTEND'] == '20261122T000000'


@pytest.mark.parametrize('times, expected', [
    # no times: all-day
    ({}, {'DTSTART;VALUE=DATE': '20261120', 'DTEND;VALUE=DATE': '20261121'}),
    # start time only: 30 minutes
    ({'start_time': '08:00'}, {'DTSTART': '20261120T080000', 'DTEND': '20261120T083000'}),
    # end time only: starts at midnight on the start date, as the reminder API reads it
    ({'end_date': '2026-11-20', 'end_time': '17:30'}, {'DTSTART': '20261120T000000', 'DTEND': '20261120T173000'}),
    ({'end_date': '2026-11-22', 'end_time': '09:00'}, {'DTSTART': '20261120T000000', 'DTEND': '20261122T090000'}),
    # both
    ({'start_time': '08:00', 'end_date': '2026-11-20', 'end_time': '17:30'},
     {'DTSTART': '20261120T080000', 'DTEND': '20261120T173000'}),
])
def test_start_and_end_time_combinations(client, configure, times, expected):
    configure(token=TOKEN)
    created = add_reminder(client, title='Combo', start_date='2026-11-20', **times)['reminder']
    (ev,) = by_summary(feed(client), 'Combo')
    for name, value in expected.items():
        assert ev[name] == value, (name, ev)
    # Same reading as HomeHub's own calendar: all-day only when neither time is set
    assert ('DTSTART;VALUE=DATE' in ev) == created['all_day']


def test_summary_description_categories(client, configure):
    configure(token=TOKEN)
    add_reminder(client, title='Checkup', start_date='2026-11-20', category='health',
                 description='<p>Bring the <b>card</b></p><p>Room 4</p>', creator='Dad')
    (ev,) = by_summary(feed(client), 'Checkup')
    assert ev['CATEGORIES'] == 'Health'
    assert ev['DESCRIPTION'] == 'Bring the card\\n\\nRoom 4\\n\\nAdded by Dad'


def test_unknown_category_falls_back_to_key(client, configure):
    configure(token=TOKEN)
    add_reminder(client, title='Odd', start_date='2026-11-20', category='misc')
    (ev,) = by_summary(feed(client), 'Odd')
    assert ev['CATEGORIES'] == 'misc'


def test_escaping(client, configure):
    configure(token=TOKEN)
    add_reminder(client, title='Tom & Jerry; a, b \\ c', start_date='2026-11-20',
                 description='line one\nline two, with; marks \\', creator='')
    body = feed(client)
    (ev,) = [e for e in events(body) if e['SUMMARY'].startswith('Tom')]
    assert ev['SUMMARY'] == 'Tom & Jerry\\; a\\, b \\\\ c'
    assert ev['DESCRIPTION'] == 'line one\\nline two\\, with\\; marks \\\\'


def test_crlf_and_line_folding(client, configure):
    configure(token=TOKEN)
    title = 'बहुत लंबा अनुस्मारक शीर्षक ' * 6 + 'end'
    add_reminder(client, title=title, start_date='2026-11-20', description='x' * 300)
    body = feed(client)
    assert '\n' not in body.replace('\r\n', '')
    for line in body.split('\r\n'):
        assert len(line.encode('utf-8')) <= 75, line
    (ev,) = by_summary(body, title.strip())
    assert ev['DESCRIPTION'] == 'x' * 300 + '\\n\\nAdded by Mom'


def test_fold_helper_never_splits_characters():
    line = 'SUMMARY:' + 'ह' * 100
    folded = calendar_feed.fold(line)
    parts = folded.split('\r\n')
    assert all(len(p.encode('utf-8')) <= 75 for p in parts)
    assert all(p.startswith(' ') for p in parts[1:])
    assert ''.join([parts[0]] + [p[1:] for p in parts[1:]]) == line
    assert calendar_feed.fold('A' * 75) == 'A' * 75


def test_stable_uids(app, client, configure):
    configure(token=TOKEN)
    rid = add_reminder(client, title='Stable', start_date='2026-11-20')['reminder']['id']
    rule_id = add_reminder(client, title='Weekly', start_date=TODAY.strftime('%Y-%m-%d'),
                           recurring={'interval': 1, 'unit': 'week'})['recurring_id']
    first = feed(client)
    (ev,) = by_summary(first, 'Stable')
    assert ev['UID'] == f'reminder-{rid}@homehub'
    weekly = by_summary(first, 'Weekly')
    assert weekly[0]['UID'] == f'rule-{rule_id}-{TODAY.strftime("%Y%m%d")}@homehub'
    assert len({e['UID'] for e in weekly}) == len(weekly)
    # Editing keeps the UID, and a second fetch gives the same UIDs
    client.patch(f'/api/reminders/{rid}', json={'title': 'Stable renamed', 'creator': 'Mom'})
    second = feed(client)
    (ev2,) = by_summary(second, 'Stable renamed')
    assert ev2['UID'] == ev['UID']
    assert [e['UID'] for e in by_summary(second, 'Weekly')] == [e['UID'] for e in weekly]
    assert re.fullmatch(r'\d{8}T\d{6}Z', ev2['DTSTAMP'])


def _months_in(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def test_recurring_dates_match_homehub_calendar(app, client, configure):
    configure(token=TOKEN)
    today = TODAY
    # A 31st inside the window: monthly rules on it clamp to the month end
    d31 = next(today - timedelta(days=i) for i in range(62) if (today - timedelta(days=i)).day == 31)
    add_reminder(client, title='Rent', start_date=d31.strftime('%Y-%m-%d'), recurring={'interval': 1, 'unit': 'month'})
    add_reminder(client, title='Bins', start_date=(today - timedelta(days=90)).strftime('%Y-%m-%d'),
                 start_time='07:30', recurring={'interval': 2, 'unit': 'week'})
    add_reminder(client, title='Birthday', start_date='2024-02-29', recurring={'interval': 1, 'unit': 'year'})
    ended = add_reminder(client, title='Course', start_date=today.strftime('%Y-%m-%d'),
                         recurring={'interval': 3, 'unit': 'day', 'end_date': (today + timedelta(days=40)).strftime('%Y-%m-%d')})
    # A stored row for one occurrence replaces the generated one (same de-duplication as the API)
    with app.app_context():
        rr = db.session.get(RecurringReminder, ended['recurring_id'])
        stored_day = today + timedelta(days=3)
        db.session.add(Reminder(date=stored_day, start_date=stored_day, title='Course', recurring_id=rr.id))
        db.session.commit()

    body = feed(client)
    feed_by_month = Counter()
    for ev in events(body):
        raw = ev.get('DTSTART;VALUE=DATE') or ev.get('DTSTART')
        d = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        feed_by_month[(d.year, d.month, d.isoformat(), ev['SUMMARY'])] += 1

    window_start = today - timedelta(days=60)
    window_end = today + timedelta(days=365)
    compared = 0
    for y, m in _months_in(window_start, window_end):
        first = date(y, m, 1)
        last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        if first < window_start or last > window_end:
            continue  # only months the feed covers in full
        data = client.get(f'/api/reminders?scope=month&date={first.isoformat()}').get_json()
        api = Counter((y, m, r['date'], r['title']) for r in data['reminders'])
        ours = Counter({k: v for k, v in feed_by_month.items() if k[:2] == (y, m)})
        assert ours == api, (y, m)
        compared += 1
    assert compared >= 10

    rent = sorted(e['DTSTART;VALUE=DATE'] for e in by_summary(body, 'Rent'))
    # Month-end clamping: every Rent date is the 31st or the last day of a shorter month
    for raw in rent:
        d = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        assert (d + timedelta(days=1)).day == 1
    assert len(rent) >= 12
    course = by_summary(body, 'Course')
    assert sum(e['UID'].startswith('reminder-') for e in course) == 1
    assert stored_day.strftime('%Y%m%d') not in ''.join(e['UID'] for e in course if e['UID'].startswith('rule-'))
    bins = by_summary(body, 'Bins')
    assert bins and all(e['DTSTART'].endswith('T073000') and e['DTEND'].endswith('T080000') for e in bins)


def test_monthly_rule_on_31st_matches_api_for_fixed_months(app, configure):
    """Month-end clamping against the API for specific months, independent of today's date."""
    configure(token=TOKEN)
    with app.app_context():
        rr = RecurringReminder(title='Pay', interval=1, unit='month', start_date=date(2026, 1, 31))
        db.session.add(rr)
        db.session.commit()
        evs = calendar_feed.collect_events(date(2026, 6, 15), {})
        got = sorted(e['date'].isoformat() for e in evs if e['title'] == 'Pay')
    assert got[:6] == ['2026-04-30', '2026-05-31', '2026-06-30', '2026-07-31', '2026-08-31', '2026-09-30']
    assert '2027-02-28' in got and '2027-03-31' in got
    client = app.test_client()
    for month in ('2026-04-01', '2026-06-01', '2027-02-01'):
        data = client.get(f'/api/reminders?scope=month&date={month}').get_json()
        api_dates = [r['date'] for r in data['reminders'] if r['title'] == 'Pay']
        assert api_dates == [d for d in got if d.startswith(month[:7])]


def test_trashed_stored_occurrence_falls_back_to_rule_like_api(app, client, configure):
    configure(token=TOKEN)
    from app.clock import utcnow
    today = TODAY
    rule_id = add_reminder(client, title='Daily', start_date=today.strftime('%Y-%m-%d'),
                           recurring={'interval': 1, 'unit': 'day'})['recurring_id']
    add_reminder_direct(app, 'Daily', today, recurring_id=rule_id, deleted_at=utcnow())
    todays = [e for e in by_summary(feed(client), 'Daily') if today.strftime('%Y%m%d') in e['DTSTART;VALUE=DATE']]
    api = [r for r in client.get(f'/api/reminders?date={today.isoformat()}').get_json()['reminders'] if r['title'] == 'Daily']
    assert len(todays) == len(api) == 1


# --- Time zones ------------------------------------------------------------------------

def test_floating_times_without_tz(client, configure):
    configure(token=TOKEN)
    add_reminder(client, title='Call', start_date='2026-11-20', start_time='18:00')
    body = feed(client)
    assert 'VTIMEZONE' not in body and 'TZID' not in body
    (ev,) = by_summary(body, 'Call')
    assert ev['DTSTART'] == '20261120T180000'


@pytest.mark.parametrize('tz', ['', 'Not/AZone'])
def test_empty_or_unknown_tz_gives_floating_times(client, configure, monkeypatch, tz):
    monkeypatch.setenv('TZ', tz)
    configure(token=TOKEN)
    add_reminder(client, title='Call', start_date='2026-11-20', start_time='18:00')
    body = feed(client)
    assert 'TZID' not in body


def _zone_available(name):
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _zone_available('Asia/Kolkata'), reason='no time zone data')
def test_tzid_without_dst(client, configure, monkeypatch):
    monkeypatch.setenv('TZ', 'Asia/Kolkata')
    configure(token=TOKEN)
    add_reminder(client, title='Call', start_date='2026-11-20', start_time='18:00')
    body = unfold(feed(client))
    assert 'BEGIN:VTIMEZONE\r\nTZID:Asia/Kolkata\r\n' in body
    assert 'TZOFFSETTO:+0530' in body and 'BEGIN:DAYLIGHT' not in body
    (ev,) = by_summary(body, 'Call')
    assert ev['DTSTART;TZID=Asia/Kolkata'] == '20261120T180000'
    assert ev['DTEND;TZID=Asia/Kolkata'] == '20261120T183000'


@pytest.mark.skipif(not _zone_available('Europe/Berlin'), reason='no time zone data')
def test_tzid_with_dst_transitions(client, configure, monkeypatch):
    monkeypatch.setenv('TZ', 'Europe/Berlin')
    configure(token=TOKEN)
    today = TODAY
    add_reminder(client, title='Call', start_date=today.strftime('%Y-%m-%d'), start_time='18:00',
                 recurring={'interval': 1, 'unit': 'week'})
    add_reminder(client, title='Holiday', start_date=today.strftime('%Y-%m-%d'))
    body = unfold(feed(client))
    assert 'TZID:Europe/Berlin' in body
    assert 'BEGIN:DAYLIGHT' in body and 'BEGIN:STANDARD' in body
    assert 'TZOFFSETTO:+0200' in body and 'TZOFFSETTO:+0100' in body
    assert by_summary(body, 'Call')[0]['DTSTART;TZID=Europe/Berlin'].endswith('T180000')
    # All-day events stay plain dates
    assert by_summary(body, 'Holiday')[0]['DTSTART;VALUE=DATE'] == today.strftime('%Y%m%d')


# --- Real parser -----------------------------------------------------------------------

@pytest.mark.parametrize('tz', [None, 'Europe/Berlin', 'America/New_York', 'Asia/Kolkata'])
def test_parses_with_icalendar(client, configure, monkeypatch, tz):
    icalendar = pytest.importorskip('icalendar')
    if tz:
        if not _zone_available(tz):
            pytest.skip('no time zone data')
        monkeypatch.setenv('TZ', tz)
    configure(token=TOKEN)
    today = TODAY
    add_reminder(client, title='Tom & Jerry; a, b', start_date='2026-11-20', start_time='09:00',
                 end_date='2026-11-20', end_time='10:00', description='a\nb', category='health')
    add_reminder(client, title='Trip', start_date='2026-11-20', end_date='2026-11-23')
    add_reminder(client, title='Weekly', start_date=today.strftime('%Y-%m-%d'), start_time='08:00',
                 recurring={'interval': 1, 'unit': 'week'})
    cal = icalendar.Calendar.from_ical(feed(client))
    evs = list(cal.walk('VEVENT'))
    assert len(evs) >= 52
    tom = next(e for e in evs if str(e['SUMMARY']).startswith('Tom'))
    assert str(tom['SUMMARY']) == 'Tom & Jerry; a, b'
    assert str(tom['DESCRIPTION']) == 'a\nb\n\nAdded by Mom'
    start = tom.decoded('DTSTART')
    assert (start.hour, start.minute) == (9, 0)
    if tz:
        from zoneinfo import ZoneInfo
        assert start.utcoffset() == start.replace(tzinfo=ZoneInfo(tz)).utcoffset()
    else:
        assert start.tzinfo is None
    trip = next(e for e in evs if str(e['SUMMARY']) == 'Trip')
    assert trip.decoded('DTEND') - trip.decoded('DTSTART') == timedelta(days=4)
    assert not cal.errors
