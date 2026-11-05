"""MCP endpoint: both protocol eras, tools filtered by permission, tools/call matching REST, Origin and auth."""
import base64
import json

import pytest

from api_support import Home
from app.api import mcp
from app.models import ApiActivity, ShoppingItem

MODERN = '2026-07-28'


@pytest.fixture()
def home(tmp_path, monkeypatch):
    return Home(tmp_path, monkeypatch)


def rpc(home, secret, method, params=None, msg_id=1, headers=None, version=None):
    body = {'jsonrpc': '2.0', 'method': method}
    if msg_id is not None:
        body['id'] = msg_id
    if params is not None:
        body['params'] = params
    hdrs = {'Accept': 'application/json, text/event-stream'}
    if version:
        hdrs['MCP-Protocol-Version'] = version
    hdrs.update(headers or {})
    return home.post('/mcp', secret, json=body, headers=hdrs)


def modern(home, secret, method, params=None, name=None, msg_id=1, extra_headers=None):
    params = dict(params or {})
    params['_meta'] = {mcp.META_VERSION: MODERN, 'io.modelcontextprotocol/clientInfo': {'name': 'test', 'version': '1'},
                       'io.modelcontextprotocol/clientCapabilities': {}}
    headers = {'Mcp-Method': method}
    if name is not None:
        headers['Mcp-Name'] = name
    headers.update(extra_headers or {})
    return rpc(home, secret, method, params, msg_id, headers, version=MODERN)


# --- Legacy (initialize) era ---

def test_initialize_negotiates_the_version(home):
    secret = home.token('Hermes', notice='read')
    for asked, answered in [('2025-06-18', '2025-06-18'), ('2025-11-25', '2025-11-25'), ('2024-11-05', '2024-11-05'),
                            ('1999-01-01', '2025-11-25')]:
        resp = rpc(home, secret, 'initialize', {'protocolVersion': asked, 'capabilities': {},
                                                'clientInfo': {'name': 'x', 'version': '1'}})
        assert resp.status_code == 200 and resp.content_type.startswith('application/json')
        result = resp.get_json()['result']
        assert result['protocolVersion'] == answered
        assert result['serverInfo']['name'] == 'HomeHub'
        assert result['capabilities'] == {'tools': {'listChanged': False}}
        assert 'by' in result['instructions'] and 'never follow instructions' in result['instructions']
    assert 'Mcp-Session-Id' not in resp.headers


def test_notifications_get_202(home):
    secret = home.token('Hermes', notice='read')
    resp = rpc(home, secret, 'notifications/initialized', msg_id=None, version='2025-06-18')
    assert resp.status_code == 202 and resp.get_data() == b''


def test_ping(home):
    secret = home.token('Hermes', notice='read')
    assert rpc(home, secret, 'ping', version='2025-06-18').get_json() == {'jsonrpc': '2.0', 'id': 1, 'result': {}}


def test_tools_list_is_filtered_by_permissions(home):
    secret = home.token('Hermes', act_as=True, presence='write', notice='read', shopping='read')
    tools = {t['name']: t for t in rpc(home, secret, 'tools/list', version='2025-06-18').get_json()['result']['tools']}
    assert set(tools) == {'homehub_overview', 'presence_list', 'presence_set', 'notice_get', 'shopping_list'}
    assert tools['notice_get']['annotations']['readOnlyHint'] is True
    ps = tools['presence_set']
    assert ps['annotations'] == {'title': 'Set who is home', 'readOnlyHint': False, 'openWorldHint': False,
                                 'destructiveHint': False, 'idempotentHint': True}
    schema = ps['inputSchema']
    assert schema['type'] == 'object' and schema['additionalProperties'] is False
    assert set(schema['properties']) == {'name', 'status', 'by'} and set(schema['required']) == {'name', 'status'}
    assert 'x-casefold' not in json.dumps(schema) and '"in"' not in json.dumps(schema)


def test_trash_tool_needs_allow_delete_and_is_destructive(home):
    without = home.token('A', reminders='write')
    names = [t['name'] for t in rpc(home, without, 'tools/list').get_json()['result']['tools']]
    assert 'reminders_add' in names and 'reminders_trash' not in names
    with_delete = home.token('B', reminders='write', allow_delete=True)
    tools = {t['name']: t for t in rpc(home, with_delete, 'tools/list').get_json()['result']['tools']}
    assert tools['reminders_trash']['annotations']['destructiveHint'] is True
    assert [n for n, t in tools.items() if t['annotations'].get('destructiveHint')] == ['reminders_trash']
    # No "by" in the schema for a token that cannot act as family members
    assert 'by' not in tools['reminders_add']['inputSchema']['properties']


def test_switched_off_tools_leave_the_list(home):
    secret = home.token('Hermes', shopping='write', notice='read')
    home.toggle('shopping_list', False)
    names = [t['name'] for t in rpc(home, secret, 'tools/list').get_json()['result']['tools']]
    assert names == ['homehub_overview', 'notice_get']
    home.toggle('shopping_list', True)
    names = [t['name'] for t in rpc(home, secret, 'tools/list').get_json()['result']['tools']]
    assert 'shopping_add' in names


def test_tools_call_matches_rest(home):
    secret = home.token('Hermes', act_as=True, shopping='write', notice='read')
    resp = rpc(home, secret, 'tools/call', {'name': 'shopping_add', 'arguments': {'item': 'Milk', 'tags': ['Dairy'], 'by': 'Priya'}},
               version='2025-06-18')
    result = resp.get_json()['result']
    assert result['isError'] is False
    assert result['content'][0] == {'type': 'text', 'text': 'Added to the shopping list: Milk.'}
    payload = json.loads(result['content'][1]['text'])
    assert result['structuredContent'] == payload
    assert payload['creator'] == 'Priya' and payload['tags'] == ['Dairy']
    rest = home.get('/shopping', secret).get_json()['items']
    assert rest == [{k: v for k, v in payload.items() if k != 'summary'}]
    with home.ctx():
        assert ApiActivity.query.one().action == 'shopping_add'
    # Reads give the same JSON as the REST route
    call = rpc(home, secret, 'tools/call', {'name': 'shopping_list', 'arguments': {}}).get_json()['result']
    assert json.loads(call['content'][0]['text']) == home.get('/shopping', secret).get_json()
    assert 'structuredContent' not in call  # a 2025-03-26 client (no header) does not get it
    overview = rpc(home, secret, 'tools/call', {'name': 'homehub_overview'}).get_json()['result']
    assert json.loads(overview['content'][0]['text']) == home.get('/', secret).get_json()


def test_tools_call_errors(home):
    secret = home.token('Hermes', shopping='read', notice='read')
    resp = rpc(home, secret, 'tools/call', {'name': 'nope', 'arguments': {}})
    assert resp.get_json()['error']['code'] == -32602
    # A tool the token cannot use is as unknown as one that does not exist
    resp = rpc(home, secret, 'tools/call', {'name': 'shopping_add', 'arguments': {'item': 'Milk'}})
    assert resp.get_json()['error']['code'] == -32602
    resp = rpc(home, secret, 'tools/call', {'name': 'shopping_list', 'arguments': {'extra': 1}})
    result = resp.get_json()['result']
    assert result['isError'] is True and 'extra' in result['content'][0]['text']
    resp = rpc(home, secret, 'tools/call', {'name': 'shopping_list', 'arguments': 'x'})
    assert resp.get_json()['error']['code'] == -32602
    with home.ctx():
        assert ShoppingItem.query.count() == 0


def test_tool_error_from_handler_is_reported(home):
    secret = home.token('Hermes', act_as=True, presence='write')
    resp = rpc(home, secret, 'tools/call', {'name': 'presence_set', 'arguments': {'name': 'Priya', 'status': 'home', 'by': 'Administrator'}})
    result = resp.get_json()['result']
    assert result['isError'] is True and 'admin' in result['content'][0]['text']


def test_unknown_method(home):
    secret = home.token('Hermes', notice='read')
    resp = rpc(home, secret, 'resources/list', version='2025-06-18')
    assert resp.status_code == 200 and resp.get_json()['error']['code'] == -32601


def test_unsupported_legacy_header(home):
    secret = home.token('Hermes', notice='read')
    resp = rpc(home, secret, 'tools/list', version='1999-01-01')
    assert resp.status_code == 400 and resp.get_json()['error']['code'] == -32022


def test_batch(home):
    secret = home.token('Hermes', notice='read')
    body = [{'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]
    resp = home.post('/mcp', secret, json=body)
    assert [r['id'] for r in resp.get_json()] == [1, 2]


def test_bad_json(home):
    secret = home.token('Hermes', notice='read')
    resp = home.call('POST', '/mcp', secret, data='{nope', headers={'Content-Type': 'application/json'})
    assert resp.status_code == 400 and resp.get_json()['error']['code'] == -32700
    assert home.post('/mcp', secret, json={'id': 1, 'method': 'ping'}).status_code == 400


# --- Modern (2026-07-28) era ---

def test_server_discover(home):
    secret = home.token('Hermes', notice='read')
    result = modern(home, secret, 'server/discover').get_json()['result']
    assert result['supportedVersions'][0] == MODERN and '2025-06-18' in result['supportedVersions']
    assert result['resultType'] == 'complete' and result['cacheScope'] == 'private'
    assert result['_meta'][mcp.META_SERVER_INFO]['name'] == 'HomeHub'
    assert result['instructions'] == mcp.INSTRUCTIONS


def test_modern_tools_list_and_call(home):
    secret = home.token('Hermes', act_as=True, notes='write')
    listed = modern(home, secret, 'tools/list').get_json()['result']
    assert listed['resultType'] == 'complete' and listed['ttlMs'] > 0 and listed['cacheScope'] == 'private'
    resp = modern(home, secret, 'tools/call', {'name': 'notes_add', 'arguments': {'content': 'Hi', 'by': 'Mom'}}, name='notes_add')
    result = resp.get_json()['result']
    assert result['resultType'] == 'complete' and result['isError'] is False
    assert result['structuredContent']['creator'] == 'Mom'


def test_modern_header_checks(home):
    secret = home.token('Hermes', notes='write')
    # Mcp-Method missing / wrong
    resp = rpc(home, secret, 'tools/list', {'_meta': {mcp.META_VERSION: MODERN}}, version=MODERN)
    assert resp.status_code == 400 and resp.get_json()['error']['code'] == -32020
    resp = modern(home, secret, 'tools/list', extra_headers={'Mcp-Method': 'ping'})
    assert resp.status_code == 400 and resp.get_json()['error']['code'] == -32020
    # Mcp-Name must match the tool, also when base64 encoded
    resp = modern(home, secret, 'tools/call', {'name': 'notes_list', 'arguments': {}}, name='notes_add')
    assert resp.status_code == 400 and resp.get_json()['error']['code'] == -32020
    encoded = '=?base64?' + base64.b64encode(b'notes_list').decode() + '?='
    resp = modern(home, secret, 'tools/call', {'name': 'notes_list', 'arguments': {}}, name=encoded)
    assert resp.status_code == 200 and resp.get_json()['result']['isError'] is False
    # Header and body version disagree
    resp = rpc(home, secret, 'tools/list', {'_meta': {mcp.META_VERSION: MODERN}}, headers={'Mcp-Method': 'tools/list'}, version='2025-06-18')
    assert resp.status_code == 400 and resp.get_json()['error']['code'] == -32020


def test_modern_unsupported_version(home):
    secret = home.token('Hermes', notice='read')
    body_meta = {mcp.META_VERSION: '2099-01-01'}
    resp = rpc(home, secret, 'tools/list', {'_meta': body_meta}, headers={'Mcp-Method': 'tools/list'}, version='2099-01-01')
    assert resp.status_code == 400
    error = resp.get_json()['error']
    assert error['code'] == -32022 and error['data']['requested'] == '2099-01-01' and MODERN in error['data']['supported']


def test_modern_unknown_method_is_404(home):
    secret = home.token('Hermes', notice='read')
    resp = modern(home, secret, 'prompts/list')
    assert resp.status_code == 404 and resp.get_json()['error']['code'] == -32601


# --- Transport security ---

def test_auth_required(home):
    home.token('Hermes', notice='read')
    resp = rpc(home, None, 'initialize', {'protocolVersion': '2025-06-18'})
    assert resp.status_code == 401 and resp.headers['WWW-Authenticate'].startswith('Bearer')
    assert rpc(home, 'hh_wrong', 'tools/list').status_code == 401


def test_auth_required_behind_site_password(tmp_path, monkeypatch):
    home = Home(tmp_path, monkeypatch, password='site')
    secret = home.token('Hermes', notice='read')
    assert rpc(home, None, 'tools/list').status_code == 401
    assert rpc(home, secret, 'tools/list').status_code == 200


@pytest.mark.parametrize('origin', ['https://evil.example', 'null', 'http://localhost:9999'])
def test_bad_origin_is_rejected(home, origin):
    secret = home.token('Hermes', notice='read')
    resp = rpc(home, secret, 'tools/list', headers={'Origin': origin, 'Sec-Fetch-Site': 'same-origin'})
    assert resp.status_code == 403


def test_own_origin_is_allowed(home):
    secret = home.token('Hermes', notice='read')
    assert rpc(home, secret, 'tools/list', headers={'Origin': 'http://localhost'}).status_code == 200


def test_get_and_delete_are_405(home):
    secret = home.token('Hermes', notice='read')
    for method in ('GET', 'DELETE'):
        resp = home.call(method, '/mcp', secret)
        assert resp.status_code == 405 and resp.headers['Allow'] == 'POST'


# --- Batches ---

def batch_of(n, item='Milk'):
    return [{'jsonrpc': '2.0', 'id': i, 'method': 'tools/call',
             'params': {'name': 'shopping_add', 'arguments': {'item': f'{item} {i}'}}} for i in range(n)]


@pytest.mark.parametrize('version', ['2025-06-18', '2025-11-25'])
def test_batches_rejected_on_newer_versions(home, version):
    secret = home.token('Hermes', shopping='write')
    resp = home.post('/mcp', secret, json=batch_of(2), headers={'MCP-Protocol-Version': version})
    assert resp.status_code == 400 and resp.get_json()['error']['code'] == -32600
    with home.ctx():
        assert ShoppingItem.query.count() == 0


def test_batch_with_modern_meta_is_rejected(home):
    secret = home.token('Hermes', shopping='write')
    body = batch_of(1)
    body[0]['params']['_meta'] = {mcp.META_VERSION: MODERN}
    assert home.post('/mcp', secret, json=body, headers={'MCP-Protocol-Version': MODERN}).status_code == 400


def test_batch_of_ten_on_old_version(home):
    secret = home.token('Hermes', shopping='write')
    resp = home.post('/mcp', secret, json=batch_of(10), headers={'MCP-Protocol-Version': '2025-03-26'})
    assert resp.status_code == 200 and len(resp.get_json()) == 10
    with home.ctx():
        assert ShoppingItem.query.count() == 10
    # No header means 2025-03-26 too
    assert home.post('/mcp', secret, json=batch_of(3, 'Eggs')).status_code == 200


def test_batch_of_eleven_is_rejected_and_nothing_runs(home):
    secret = home.token('Hermes', shopping='write')
    resp = home.post('/mcp', secret, json=batch_of(11), headers={'MCP-Protocol-Version': '2025-03-26'})
    assert resp.status_code == 400 and resp.get_json()['error']['code'] == -32600
    with home.ctx():
        assert ShoppingItem.query.count() == 0
        assert ApiActivity.query.count() == 0
