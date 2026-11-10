"""Who gets into /api/v1: only a Bearer token, never a cookie, a query string or a form field;
and a token never opens anything outside /api/v1."""
import pytest

from api_support import Home


@pytest.fixture()
def home(tmp_path, monkeypatch):
    return Home(tmp_path, monkeypatch)


@pytest.fixture()
def locked(tmp_path, monkeypatch):
    """A site password is set."""
    return Home(tmp_path, monkeypatch, password='site-secret')


def assert_401(resp):
    assert resp.status_code == 401
    assert resp.get_json() == {'error': 'unauthorized', 'message': resp.get_json()['message']}
    assert resp.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('fixture', ['home', 'locked'])
def test_api_is_off_without_tokens(request, fixture):
    home = request.getfixturevalue(fixture)
    assert_401(home.get('/'))
    assert_401(home.get('/', 'hh_' + 'x' * 43))
    assert_401(home.get('/openapi.json'))


@pytest.mark.parametrize('fixture', ['home', 'locked'])
def test_same_401_with_or_without_tokens(request, fixture):
    home = request.getfixturevalue(fixture)
    before = home.get('/', 'hh_wrong')
    home.token('Hermes', notice='read')
    after = home.get('/', 'hh_wrong')
    assert (before.status_code, before.get_data()) == (after.status_code, after.get_data())


@pytest.mark.parametrize('path', ['/', '/presence', '/openapi.json', '/nothing-here'])
def test_site_password_without_valid_token_is_401_json(locked, path):
    from app.api import tokens
    secret = locked.token('Hermes', presence='read')
    revoked = locked.token('Old', presence='read')
    with locked.ctx():
        tokens.revoke_token(tokens.find_by_name('Old'))
    for bad in (None, 'hh_wrong', revoked):
        resp = locked.get(path, bad)
        assert resp.status_code == 401, (path, bad)
        assert resp.get_json()['error'] == 'unauthorized'
        assert resp.headers['WWW-Authenticate'] == 'Bearer realm="HomeHub"'
        assert resp.headers['Cache-Control'] == 'no-store'
    assert locked.get('/', secret).status_code == 200


def test_site_password_mcp_without_valid_token_is_401(locked):
    locked.token('Hermes', presence='read')
    for bad in (None, 'hh_wrong'):
        resp = locked.post('/mcp', bad, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        assert resp.status_code == 401 and resp.get_json()['error'] == 'unauthorized'


@pytest.mark.parametrize('path', ['/', '/notes', '/admin/api', '/api/reminders', '/api/shopping'])
def test_other_pages_still_redirect_with_or_without_token(locked, path):
    secret = locked.token('Hermes', notice='read', shopping='write', reminders='write')
    for headers in ({}, {'Authorization': f'Bearer {secret}'}):
        resp = locked.client.get(path, headers=headers)
        assert resp.status_code == 302 and resp.headers['Location'].endswith('/login'), (path, headers)


def test_no_site_password_without_token_is_401(home):
    home.token('Hermes', notice='read')
    resp = home.get('/', 'hh_wrong')
    assert resp.status_code == 401 and resp.get_json()['error'] == 'unauthorized'


@pytest.mark.parametrize('fixture', ['home', 'locked'])
def test_valid_token_works(request, fixture):
    home = request.getfixturevalue(fixture)
    secret = home.token('Hermes', notice='read')
    assert home.get('/', secret).status_code == 200


@pytest.mark.parametrize('header', ['Basic {s}', 'Token {s}', '{s}', 'Bearer', 'Bearer  '])
def test_only_bearer_scheme(home, header):
    secret = home.token('Hermes', notice='read')
    assert_401(home.client.get('/api/v1/', headers={'Authorization': header.format(s=secret)}))


def test_bearer_scheme_is_case_insensitive(home):
    secret = home.token('Hermes', notice='read')
    assert home.client.get('/api/v1/', headers={'Authorization': f'bearer {secret}'}).status_code == 200


def test_token_elsewhere_is_ignored(home):
    secret = home.token('Hermes', notice='read')
    assert_401(home.client.get(f'/api/v1/?token={secret}'))
    assert_401(home.client.get(f'/api/v1/?access_token={secret}'))
    home.client.set_cookie('token', secret)
    home.client.set_cookie('Authorization', f'Bearer {secret}')
    assert_401(home.client.get('/api/v1/'))
    assert_401(home.client.post('/api/v1/', data={'token': secret, 'access_token': secret}))


def test_browser_session_alone_cannot_call_the_api(locked):
    locked.token('Hermes', notice='read')
    resp = locked.client.post('/login', data={'password': 'site-secret'})
    assert resp.status_code == 302
    assert locked.client.get('/').status_code == 200  # the browser is logged in
    assert_401(locked.client.get('/api/v1/'))


def test_unlocked_admin_session_cannot_call_the_api(home):
    from app import admin
    with home.ctx():
        admin.set_admin_password('4321')
    home.token('Hermes', notice='read')
    assert home.client.post('/admin/unlock', json={'password': '4321'}).get_json()['ok']
    assert_401(home.client.get('/api/v1/'))


def test_revoked_token_is_refused(home):
    from app.api import tokens
    secret = home.token('Hermes', notice='read')
    with home.ctx():
        tokens.revoke_token(tokens.find_by_name('Hermes'))
    assert_401(home.get('/', secret))


PAGES = ['/', '/notes', '/shopping', '/calendar', '/expenses', '/admin/backups', '/upload', '/sw.js']
WEB_API = ['/api/reminders', '/api/shopping', '/api/chores', '/api/recipes', '/api/expenses/month', '/trash']


@pytest.mark.parametrize('path', PAGES + WEB_API)
def test_token_does_not_open_other_pages(locked, path):
    secret = locked.token('Hermes', **{'notice': 'read', 'shopping': 'write', 'reminders': 'write', 'expenses': 'write'})
    resp = locked.client.get(path, headers={'Authorization': f'Bearer {secret}'})
    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']


def test_token_does_not_unlock_web_writes(locked):
    secret = locked.token('Hermes', notes='write')
    resp = locked.client.post('/notes', data={'content': 'hi', 'creator': 'Priya'},
                              headers={'Authorization': f'Bearer {secret}'})
    assert resp.status_code == 302 and '/login' in resp.headers['Location']
    with locked.ctx():
        from app.models import Note
        assert Note.query.count() == 0


def test_unknown_api_path(home, locked):
    for h in (home, locked):
        secret = h.token('Hermes', notice='read')
        assert_401(h.get('/nothing-here'))
        resp = h.get('/nothing-here', secret)
        assert resp.status_code == 404
        assert resp.get_json()['error'] == 'not_found'


def test_wrong_method_is_json(home):
    secret = home.token('Hermes', notice='read')
    resp = home.call('DELETE', '/', secret)
    assert resp.status_code == 405
    assert resp.get_json()['error'] == 'method_not_allowed'


def test_web_404_pages_unchanged(home):
    resp = home.client.get('/no-such-page')
    assert resp.status_code == 404
    assert resp.content_type.startswith('text/html')


def test_cross_site_browser_write_is_still_blocked(home):
    secret = home.token('Hermes', notice='read')
    resp = home.client.post('/api/v1/', headers={'Authorization': f'Bearer {secret}', 'Sec-Fetch-Site': 'cross-site'})
    assert resp.status_code == 403
    resp = home.client.post('/notes', data={'content': 'x', 'creator': 'Priya'},
                            headers={'Origin': 'https://evil.example'})
    assert resp.status_code == 403


def test_scripts_without_browser_headers_pass(home):
    secret = home.token('Hermes', notice='read')
    assert home.get('/', secret).status_code == 200


def test_no_cors_headers(home):
    secret = home.token('Hermes', notice='read')
    resp = home.get('/', secret, headers={'Origin': 'https://evil.example'})
    assert 'Access-Control-Allow-Origin' not in resp.headers


def test_api_does_not_set_cookies(locked):
    secret = locked.token('Hermes', notice='read')
    resp = locked.get('/', secret)
    assert 'Set-Cookie' not in resp.headers


def test_body_limit(home):
    secret = home.token('Hermes', notice='read')
    from app.api import core
    with home.app.test_request_context('/api/v1/', method='POST', data='{"a": "' + 'x' * 70000 + '"}'):
        with pytest.raises(core.ApiError) as exc:
            core.read_json_body()
        assert exc.value.status == 413
    with home.app.test_request_context('/api/v1/', method='POST', data='[1, 2]'):
        with pytest.raises(core.ApiError) as exc:
            core.read_json_body()
        assert exc.value.status == 400
    assert secret
