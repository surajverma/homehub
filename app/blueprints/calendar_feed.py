"""Read-only calendar subscription feed (iCalendar, RFC 5545) of HomeHub reminders.

Off unless config.yml sets ``calendar_feed.token``. Calendar apps fetch
``/calendar.ics?token=<token>``; they can't log in, so this one endpoint is let past the
site password (see ``_OPEN_ENDPOINTS`` in auth.py) and checks the token itself. Anything
wrong answers a plain 404 so the link doesn't reveal whether the feed exists.

Recurring reminders are listed as the actual dates HomeHub shows (``occurrences_between``),
not as RRULEs: RFC 5545 skips months without the start day, HomeHub clamps to the month end.

Times in HomeHub are local wall-clock ``HH:MM`` strings. When the ``TZ`` environment
variable names a known zone they are sent with that TZID and a matching VTIMEZONE;
otherwise they are sent as floating local times, which calendar apps show in the
viewer's own zone.
"""
import hmac
import html
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from html.parser import HTMLParser

from flask import Response, abort, current_app, request

from ..blueprints import main_bp
from ..clock import utcnow
from ..models import Reminder, RecurringReminder
from ..recurrence import occurrences_between

DAYS_BACK = 60
DAYS_AHEAD = 365
DEFAULT_DURATION = timedelta(minutes=30)
# Shorter tokens are treated as unset: a guessable link would expose every reminder
MIN_TOKEN_LENGTH = 16
REFRESH = 'PT1H'
PRODID = '-//HomeHub//Calendar feed//EN'


# --- Token -----------------------------------------------------------------------------

def configured_token(cfg) -> str | None:
    """The feed token from config, or None when the feed is off."""
    section = (cfg or {}).get('calendar_feed')
    if not isinstance(section, dict):
        return None
    token = section.get('token')
    if token is None or isinstance(token, bool):
        return None
    token = str(token).strip()
    if not token:
        return None
    if len(token) < MIN_TOKEN_LENGTH:
        current_app.logger.warning(
            'calendar_feed.token is shorter than %d characters, so the calendar feed stays off', MIN_TOKEN_LENGTH)
        return None
    return token


def _token_matches(given: str, expected: str) -> bool:
    # Bytes, so a non-ASCII guess can't raise inside compare_digest
    return hmac.compare_digest(given.encode('utf-8'), expected.encode('utf-8'))


# --- Text helpers ----------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    """Plain text from the small HTML subset reminder descriptions may hold."""
    _BREAKS = {'br', 'p', 'li', 'h1', 'h2', 'h3', 'pre', 'ul', 'ol'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._href = None
        self._link_text_start = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._BREAKS:
            self.parts.append('\n')
        if tag == 'li':
            self.parts.append('- ')
        if tag == 'a':
            self._href = dict(attrs).get('href')
            self._link_text_start = len(self.parts)

    def handle_endtag(self, tag):
        if tag in self._BREAKS and tag != 'br':
            self.parts.append('\n')
        if tag == 'a' and self._href:
            text = ''.join(self.parts[self._link_text_start:]).strip()
            if self._href != text:
                self.parts.append(f' ({self._href})')
            self._href = None

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(value: str) -> str:
    if not value:
        return ''
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
        text = ''.join(parser.parts)
    except Exception:
        text = html.unescape(re.sub(r'<[^>]*>', '', value))
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = [line.rstrip() for line in text.split('\n')]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def plain(value) -> str:
    """Titles and names are stored HTML-escaped by bleach; calendar apps want the text."""
    return html.unescape(str(value or '')).strip()


def escape_text(value: str) -> str:
    """RFC 5545 TEXT escaping."""
    value = str(value or '').replace('\r\n', '\n').replace('\r', '\n')
    return (value.replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n'))


def fold(line: str) -> str:
    """Fold to at most 75 octets per line, never splitting a UTF-8 character."""
    data = line.encode('utf-8')
    if len(data) <= 75:
        return line
    out, current, limit = [], b'', 75
    for ch in line:
        b = ch.encode('utf-8')
        if len(current) + len(b) > limit:
            out.append(current)
            current, limit = b' ', 75  # continuation lines start with one space
        current += b
    out.append(current)
    return '\r\n'.join(part.decode('utf-8') for part in out)


# --- Time zone -------------------------------------------------------------------------

def feed_timezone():
    """(TZID, ZoneInfo) from the TZ environment variable, or (None, None) for floating times."""
    name = (os.environ.get('TZ') or '').strip().lstrip(':')
    if not name:
        return None, None
    try:
        from zoneinfo import ZoneInfo
        return name, ZoneInfo(name)
    except Exception:
        current_app.logger.warning('TZ=%r is not a known time zone; the calendar feed uses floating times', name)
        return None, None


def _fmt_offset(td: timedelta) -> str:
    total = int(td.total_seconds())
    sign = '+' if total >= 0 else '-'
    total = abs(total)
    hh, rem = divmod(total, 3600)
    mm, ss = divmod(rem, 60)
    return f'{sign}{hh:02d}{mm:02d}' + (f'{ss:02d}' if ss else '')


def _transitions(zone, start: datetime, end: datetime):
    """UTC instants in [start, end) where the zone's offset changes (minute precision)."""
    found = []
    step = timedelta(hours=6)
    t = start
    prev = t.astimezone(zone).utcoffset()
    while t < end:
        nxt = t + step
        off = nxt.astimezone(zone).utcoffset()
        if off != prev:
            lo, hi = t, nxt
            while hi - lo > timedelta(minutes=1):
                mid = lo + (hi - lo) / 2
                if mid.astimezone(zone).utcoffset() == prev:
                    lo = mid
                else:
                    hi = mid
            found.append(hi.replace(second=0, microsecond=0))
            prev = off
        t = nxt
    return found


def vtimezone_lines(tzid: str, zone, first: date, last: date) -> list[str]:
    """A VTIMEZONE with explicit observances covering ``first`` to ``last`` (no RRULEs)."""
    start = datetime.combine(first - timedelta(days=1), time(), tzinfo=timezone.utc)
    end = datetime.combine(last + timedelta(days=2), time(), tzinfo=timezone.utc)

    def observance(instant_utc, offset_from):
        local = instant_utc.astimezone(zone)
        kind = 'DAYLIGHT' if local.dst() else 'STANDARD'
        wall = (instant_utc + offset_from).replace(tzinfo=None)
        return [
            f'BEGIN:{kind}',
            f'DTSTART:{wall.strftime("%Y%m%dT%H%M%S")}',
            f'TZOFFSETFROM:{_fmt_offset(offset_from)}',
            f'TZOFFSETTO:{_fmt_offset(local.utcoffset())}',
            f'TZNAME:{escape_text(local.tzname() or tzid)}',
            f'END:{kind}',
        ]

    lines = ['BEGIN:VTIMEZONE', f'TZID:{escape_text(tzid)}']
    first_offset = start.astimezone(zone).utcoffset()
    lines += observance(start, first_offset)
    for instant in _transitions(zone, start, end):
        before = (instant - timedelta(seconds=1)).astimezone(zone).utcoffset()
        lines += observance(instant, before)
    lines.append('END:VTIMEZONE')
    return lines


# --- Events ----------------------------------------------------------------------------

def _category_labels(cfg) -> dict:
    labels = {}
    entries = ((cfg or {}).get('reminders') or {}).get('categories') or []
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and entry.get('key'):
                labels[entry['key']] = str(entry.get('label') or entry['key'])
    return labels


def _parse_hhmm(value):
    raw = str(value or '').strip()
    m = re.fullmatch(r'(\d{1,2}):(\d{2})', raw)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if 0 <= hh < 24 and 0 <= mm < 60:
        return time(hh, mm)
    return None


def _utc_stamp(dt) -> str:
    return (dt or utcnow()).strftime('%Y%m%dT%H%M%SZ')


def _description(description, creator) -> str:
    text = html_to_text(description)
    who = plain(creator)
    if who:
        text = (text + '\n\n' if text else '') + f'Added by {who}'
    return text


def _event_times(start_d, start_t, end_d, end_t):
    """(all_day, start, end) with ``end`` exclusive: dates for all-day, datetimes otherwise."""
    if start_t is None:
        last = end_d if end_d and end_d >= start_d else start_d
        return True, start_d, last + timedelta(days=1)
    start = datetime.combine(start_d, start_t)
    if end_d and end_t:
        end = datetime.combine(end_d, end_t)
    elif end_d:
        # An end date without a time runs to the end of that day, as HomeHub treats it
        end = datetime.combine(end_d + timedelta(days=1), time())
    elif end_t:
        end = datetime.combine(start_d, end_t)
    else:
        end = start + DEFAULT_DURATION
    if end <= start:
        end = start + DEFAULT_DURATION
    return False, start, end


def _event_lines(uid, summary, description, category, stamp, timing, tzid):
    all_day, start, end = timing
    lines = ['BEGIN:VEVENT', f'UID:{uid}', f'DTSTAMP:{stamp}']
    if all_day:
        lines += [f'DTSTART;VALUE=DATE:{start.strftime("%Y%m%d")}',
                  f'DTEND;VALUE=DATE:{end.strftime("%Y%m%d")}']
    else:
        param = f';TZID={tzid}' if tzid else ''
        lines += [f'DTSTART{param}:{start.strftime("%Y%m%dT%H%M%S")}',
                  f'DTEND{param}:{end.strftime("%Y%m%dT%H%M%S")}']
    lines.append(f'SUMMARY:{escape_text(summary)}')
    if description:
        lines.append(f'DESCRIPTION:{escape_text(description)}')
    if category:
        lines.append(f'CATEGORIES:{escape_text(category)}')
    lines += ['TRANSP:TRANSPARENT', 'END:VEVENT']
    return lines


def collect_events(today: date, cfg) -> list[dict]:
    """Every reminder date in the feed window, as HomeHub's own calendar would show them."""
    window_start = today - timedelta(days=DAYS_BACK)
    window_end = today + timedelta(days=DAYS_AHEAD)
    labels = _category_labels(cfg)
    events = []

    rows = Reminder.query.filter(Reminder.deleted_at.is_(None), Reminder.date <= window_end).all()
    kept = []
    for r in rows:
        start_d = r.start_date or r.date
        end_d = r.end_date if r.end_date and r.end_date >= start_d else start_d
        if end_d < window_start or start_d > window_end:
            continue
        kept.append(r)
        start_t = _parse_hhmm(r.start_time if r.start_time is not None else r.time)
        events.append({
            'uid': f'reminder-{r.id}@homehub',
            'sort': (start_d, r.id),
            'date': start_d,
            'title': plain(r.title),
            'description': _description(r.description, r.creator),
            'category': labels.get(r.category, r.category) if r.category else None,
            'stamp': _utc_stamp(r.updated_at or r.timestamp),
            'timing': _event_times(start_d, start_t, r.end_date, _parse_hhmm(r.end_time)),
        })

    now_stamp = _utc_stamp(None)
    for rr in RecurringReminder.query.all():
        for d in occurrences_between(rr, window_start, window_end):
            # Same de-duplication as /api/reminders: a stored row for this date replaces it
            if any(r.date == d and r.title == rr.title and r.recurring_id == rr.id for r in kept):
                continue
            events.append({
                'uid': f'rule-{rr.id}-{d.strftime("%Y%m%d")}@homehub',
                'sort': (d, 1_000_000 + rr.id),
                'date': d,
                'title': plain(rr.title),
                'description': _description(rr.description, rr.creator),
                'category': labels.get(rr.category, rr.category) if rr.category else None,
                'stamp': now_stamp,
                'timing': _event_times(d, _parse_hhmm(rr.time), None, None),
            })
    events.sort(key=lambda e: e['sort'])
    return events


def build_calendar(today: date, cfg) -> str:
    tzid, zone = feed_timezone()
    events = collect_events(today, cfg)
    name = plain((cfg or {}).get('instance_name') or 'HomeHub')
    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        f'PRODID:{PRODID}',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        f'X-WR-CALNAME:{escape_text(name)}',
        f'NAME:{escape_text(name)}',
        f'REFRESH-INTERVAL;VALUE=DURATION:{REFRESH}',
        f'X-PUBLISHED-TTL:{REFRESH}',
    ]
    if tzid:
        lines.append(f'X-WR-TIMEZONE:{escape_text(tzid)}')
        timed = [e['timing'] for e in events if not e['timing'][0]]
        first = min([t[1].date() for t in timed], default=today)
        last = max([t[2].date() for t in timed], default=today)
        lines += vtimezone_lines(tzid, zone, first, last)
    for e in events:
        lines += _event_lines(e['uid'], e['title'], e['description'], e['category'], e['stamp'], e['timing'], tzid)
    lines.append('END:VCALENDAR')
    return ''.join(fold(line) + '\r\n' for line in lines)


@main_bp.route('/calendar.ics', methods=['GET'])
def calendar_feed():
    cfg = current_app.config.get('HOMEHUB_CONFIG', {})
    expected = configured_token(cfg)
    given = request.args.get('token', '')
    if not expected or not given or not _token_matches(given, expected):
        abort(404)
    body = build_calendar(date.today(), cfg)
    resp = Response(body, content_type='text/calendar; charset=utf-8')
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['Content-Disposition'] = 'inline; filename="homehub.ics"'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    return resp
