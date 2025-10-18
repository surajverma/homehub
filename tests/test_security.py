import re
import json
from datetime import date

import pytest

from app import create_app, db
from app.models import Note, Reminder


def make_app():
    # Minimal config for tests
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
    return app.test_client()


def test_note_sanitization_blocks_script(client):
    resp = client.post('/notes', data={
        'content': '<b>ok</b><script>alert(1)</script>',
        'creator': 'Alice'
    }, follow_redirects=True)
    assert resp.status_code == 200
    # Ensure the stored note has no script tag
    with client.application.app_context():
        n = Note.query.order_by(Note.timestamp.desc()).first()
        assert n is not None
        assert '<script' not in n.content.lower()
        assert '&lt;script' in n.content.lower() or 'script' not in n.content.lower()


def test_reminders_api_and_index_bootstrap(client):
    # Create a reminder via API
    d = date.today().strftime('%Y-%m-%d')
    payload = {
        'date': d,
        'title': 'Test',
        'description': '<i>x</i><img src=x onerror=alert(1)>',
        'creator': 'Alice',
        'time': '09:30',
        'category': 'work'
    }
    resp = client.post('/api/reminders', json=payload)
    assert resp.status_code == 200
    j = resp.get_json()
    assert j['ok'] is True
    rid = j['reminder']['id']

    # Index page should embed reminders via tojson; no raw tags
    resp2 = client.get('/')
    assert resp2.status_code == 200
    html = resp2.get_data(as_text=True)
    # The JSON script tag should exist and not include an <img ... onerror>
    m = re.search(r'<script id="legacyRemindersData"[^>]*>(.*?)</script>', html, re.S)
    assert m, 'Bootstrap JSON script not found'
    data_text = m.group(1)
    # Must be valid JSON
    data = json.loads(data_text)
    # Expect our date key
    assert d in data
    # The reminder description should be sanitized (no tag like img with onerror)
    rec = next((r for r in data[d] if r['id'] == rid), None)
    assert rec is not None
    assert 'onerror' not in (rec.get('description') or '').lower()


def test_media_ssrf_block_localhost(client, monkeypatch):
    # Try to enqueue a media download to localhost; should be rejected with a redirect back and a flash
    resp = client.post('/media', data={
        'url': 'http://127.0.0.1:8000',
        'creator': 'Alice',
        'format': 'mp4',
        'quality': 'best'
    }, follow_redirects=True)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Invalid or disallowed URL' in body


def test_shortener_allows_only_http_https(client):
    # Creation with javascript: should be rejected and redirect back
    resp = client.post('/shorten', data={
        'original_url': 'javascript:alert(1)',
        'creator': 'Alice'
    }, follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert 'Please enter a valid http(s) URL' in body

    # Valid http should succeed
    resp2 = client.post('/shorten', data={
        'original_url': 'http://example.com',
        'creator': 'Alice'
    })
    assert resp2.status_code in (302, 303)
