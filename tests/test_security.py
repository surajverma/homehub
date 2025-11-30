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


def test_upload_preview_endpoint_security(client):
    """Test that preview endpoint properly validates file paths and prevents directory traversal"""
    # Test 1: Attempt directory traversal
    res = client.get('/uploads/preview/../config.yml')
    assert res.status_code in (404, 400), "Should not allow directory traversal"
    
    # Test 2: Attempt absolute path
    res = client.get('/uploads/preview//etc/passwd')
    assert res.status_code == 404, "Should not allow absolute paths"
    
    # Test 3: Safe file types (images, PDFs) can be previewed with security headers
    import io
    
    # Upload a safe image file
    img_data = {
        'file': (io.BytesIO(b'fake image content'), 'test.jpg'),
        'creator': 'testuser'
    }
    client.post('/upload', data=img_data, content_type='multipart/form-data')
    
    preview_res = client.get('/uploads/preview/test.jpg')
    assert preview_res.status_code == 200
    # Should have security headers
    assert 'Content-Security-Policy' in preview_res.headers
    assert 'X-Content-Type-Options' in preview_res.headers
    assert preview_res.headers.get('X-Content-Type-Options') == 'nosniff'
    assert 'X-Frame-Options' in preview_res.headers
    # Should be inline (not attachment)
    disposition = preview_res.headers.get('Content-Disposition', '')
    assert 'attachment' not in disposition.lower(), "Safe files should be inline"
    
    # Test 4: Dangerous file types (HTML, SVG) are forced to download
    html_data = {
        'file': (io.BytesIO(b'<script>alert("xss")</script>'), 'malicious.html'),
        'creator': 'testuser'
    }
    client.post('/upload', data=html_data, content_type='multipart/form-data')
    
    html_preview = client.get('/uploads/preview/malicious.html')
    # Should force download (attachment) instead of inline preview
    html_disposition = html_preview.headers.get('Content-Disposition', '')
    assert 'attachment' in html_disposition.lower(), "HTML files must be downloaded, not previewed"
    
    # Test 5: SVG files (can contain scripts) are forced to download
    svg_data = {
        'file': (io.BytesIO(b'<svg><script>alert(1)</script></svg>'), 'malicious.svg'),
        'creator': 'testuser'
    }
    client.post('/upload', data=svg_data, content_type='multipart/form-data')
    
    svg_preview = client.get('/uploads/preview/malicious.svg')
    svg_disposition = svg_preview.headers.get('Content-Disposition', '')
    assert 'attachment' in svg_disposition.lower(), "SVG files must be downloaded, not previewed"
    
    # Test 6: Regular download endpoint still forces attachment for all files
    download_res = client.get('/uploads/test.jpg')
    download_disposition = download_res.headers.get('Content-Disposition', '')
    assert 'attachment' in download_disposition.lower(), "Download endpoint should force attachment"
