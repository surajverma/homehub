"""API tokens: storage, name rules, permissions, the overview, OpenAPI and the CLI."""
import json

import pytest

from api_support import Home
from app.api import tokens
from app.models import ApiActivity, ApiToken


@pytest.fixture()
def home(tmp_path, monkeypatch):
    return Home(tmp_path, monkeypatch)


def test_token_format_and_only_hash_is_stored(home):
    secret = home.token('Home Assistant', presence='read')
    assert secret.startswith('hh_') and len(secret) > 40
    with home.ctx():
        row = ApiToken.query.one()
        assert row.token_hash == tokens.hash_secret(secret)
        assert row.prefix == secret[:7]
        assert secret not in (row.permissions + row.name + row.prefix + row.token_hash)
        assert json.loads(row.permissions) == {'presence': 'read'}


@pytest.mark.parametrize('name, problem', [
    ('', 'Give the token a name'),
    ('x' * 41, 'at most 40'),
    ('priya', 'family member'),
    ('ADMINISTRATOR', 'admin name'),
    ('admin', 'admin name'),
])
def test_name_rules(home, name, problem):
    with home.ctx(), pytest.raises(tokens.TokenError, match=problem):
        tokens.create_token(name, {'notice': 'read'})


def test_names_are_unique_ignoring_case(home):
    home.token('Hermes', notice='read')
    with home.ctx(), pytest.raises(tokens.TokenError, match='already exists'):
        tokens.create_token('hermes', {'notice': 'read'})


def test_name_is_sanitised(home):
    with home.ctx():
        row, _ = tokens.create_token('<b>Node-RED</b>', {'notice': 'read'})
        assert row.name == 'Node-RED'


def test_permission_rules(home):
    with home.ctx():
        with pytest.raises(tokens.TokenError, match='Unknown tool'):
            tokens.create_token('A', {'sunshine': 'read'})
        with pytest.raises(tokens.TokenError):
            tokens.create_token('A', {'notice': 'write'})
        with pytest.raises(tokens.TokenError, match='Act as family members'):
            tokens.create_token('A', {'presence': 'write'})
        row, _ = tokens.create_token('A', {'presence': 'write', 'shopping': 'none'}, act_as_members=True)
        assert row.permission_map == {'presence': 'write'}


def test_allow_delete_needs_write_on_a_tool_with_a_trash(home):
    with home.ctx():
        row, _ = tokens.create_token('A', {'reminders': 'read', 'shopping': 'read'}, allow_delete=True)
        assert row.allow_delete is False
        # Who is home has Read & write but nothing that goes to the trash
        row, _ = tokens.create_token('A2', {'presence': 'write', 'notice': 'read'}, act_as_members=True, allow_delete=True)
        assert row.allow_delete is False
        for i, tool in enumerate(tokens.DELETABLE):
            row, _ = tokens.create_token(f'B{i}', {tool: 'write'}, allow_delete=True)
            assert row.allow_delete is True, tool


def test_regenerate_and_revoke(home):
    old = home.token('Hermes', notice='read')
    assert home.get('/', old).status_code == 200
    with home.ctx():
        row = tokens.find_by_name('HERMES')
        new = tokens.regenerate_token(row)
    assert home.get('/', old).status_code == 401
    assert home.get('/', new).status_code == 200
    with home.ctx():
        tokens.revoke_token(tokens.find_by_name('Hermes'))
    assert home.get('/', new).status_code == 401
    with home.ctx():
        # A revoked name is not free again, but regenerating brings the token back
        with pytest.raises(tokens.TokenError, match='revoked'):
            tokens.create_token('Hermes', {'notice': 'read'})
        again = tokens.regenerate_token(tokens.find_by_name('Hermes'))
    assert home.get('/', again).status_code == 200


def test_last_used_is_noted_at_most_once_a_minute(home):
    secret = home.token('Hermes', notice='read')
    home.get('/', secret)
    with home.ctx():
        first = ApiToken.query.one().last_used_at
    assert first is not None
    home.get('/', secret)
    with home.ctx():
        assert ApiToken.query.one().last_used_at == first


def test_overview(home):
    secret = home.token('Hermes', act_as=True, presence='write', notice='read', shopping='read')
    resp = home.get('/', secret)
    assert resp.status_code == 200
    assert resp.headers['Cache-Control'] == 'no-store'
    assert resp.content_type.startswith('application/json')
    data = resp.get_json()
    assert data == {
        'token': 'Hermes', 'act_as_members': True, 'allow_delete': False,
        'tools': {'presence': 'write', 'notice': 'read', 'shopping': 'read'},
        'family_members': ['Priya', 'Dad', 'Mom'], 'version': '1',
    }


def test_overview_leaves_out_the_admin_even_when_listed_as_family(tmp_path, monkeypatch):
    home = Home(tmp_path, monkeypatch, family_members=['Priya', 'Administrator'])
    secret = home.token('Hermes', notice='read')
    assert home.get('/', secret).get_json()['family_members'] == ['Priya']


def test_switched_off_tool_is_hidden_and_comes_back(home):
    secret = home.token('Hermes', shopping='read', notice='read')
    home.toggle('shopping_list', False)
    assert 'shopping' not in home.get('/', secret).get_json()['tools']
    spec = home.get('/openapi.json', secret).get_json()
    assert not any(p.startswith('/shopping') for p in spec['paths'])
    home.toggle('shopping_list', True)
    assert home.get('/', secret).get_json()['tools']['shopping'] == 'read'


def test_feature_defaults_match_the_sidebar(home):
    # A missing line: calendar, expenses, who is home and personal status stay on; the rest are off
    home.set(feature_toggles={})
    with home.ctx():
        from app.config import load_config
        on = {t.key for t in tokens.enabled_tools(load_config())}
    assert on == {'presence', 'statuses', 'notice', 'reminders', 'expenses'}


def test_openapi_lists_only_what_the_token_can_use(home):
    secret = home.token('Hermes', notice='read')
    resp = home.get('/openapi.json', secret)
    assert resp.status_code == 200
    spec = resp.get_json()
    assert spec['openapi'].startswith('3.1')
    assert spec['servers'] == [{'url': 'http://localhost/api/v1'}]
    assert '/' in spec['paths']
    assert '/shopping' not in spec['paths']


def test_openapi_needs_a_token(home):
    home.token('Hermes', notice='read')
    assert home.get('/openapi.json').status_code == 401


def test_docs_openapi_copy_is_up_to_date(home):
    from app.api import openapi
    with open(openapi.DOCS_PATH, encoding='utf-8') as f:
        saved = f.read()
    assert saved == openapi.docs_text(), 'docs/openapi.json is out of date: run python -m app.api.openapi'


def test_activity_is_pruned_to_the_newest_rows(home, monkeypatch):
    monkeypatch.setattr(tokens, 'ACTIVITY_KEEP', 5)
    home.token('Hermes', notice='read')
    with home.ctx():
        row = ApiToken.query.one()
        for i in range(8):
            tokens.log_activity(row, 'Priya', 'test', f'item {i}')
        targets = [a.target for a in ApiActivity.query.order_by(ApiActivity.id).all()]
        assert targets == [f'item {i}' for i in range(3, 8)]
        assert [a.target for a in tokens.recent_activity(row, 2)] == ['item 7', 'item 6']


# --- CLI ---

def run(home, *args):
    return home.app.test_cli_runner().invoke(args=['api-token', *args])


def test_cli_create_list_regenerate_revoke(home):
    result = run(home, 'create', 'Home Assistant', '--tool', 'presence=write', '--tool', 'reminders=read', '--act-as-members')
    assert result.exit_code == 0, result.output
    secret = [line.strip() for line in result.output.splitlines() if line.strip().startswith('hh_')][0]
    assert home.get('/', secret).get_json()['tools'] == {'presence': 'write', 'reminders': 'read'}

    listed = run(home, 'list')
    assert 'Home Assistant' in listed.output and secret not in listed.output and 'active' in listed.output

    regen = run(home, 'regenerate', 'home assistant')
    assert regen.exit_code == 0
    new = [line.strip() for line in regen.output.splitlines() if line.strip().startswith('hh_')][0]
    assert home.get('/', secret).status_code == 401
    assert home.get('/', new).status_code == 200

    assert run(home, 'revoke', 'Home Assistant').exit_code == 0
    assert home.get('/', new).status_code == 401
    assert 'revoked' in run(home, 'list').output


def test_cli_rejects_bad_input(home):
    assert 'TOOL=LEVEL' in run(home, 'create', 'X', '--tool', 'presence').output
    result = run(home, 'create', 'Priya', '--tool', 'notice=read')
    assert result.exit_code != 0 and 'family member' in result.output
    result = run(home, 'create', 'X', '--tool', 'reminders=read', '--tool', 'presence=read', '--allow-delete')
    assert result.exit_code != 0 and 'needs write on a tool' in result.output and 'shopping' in result.output
    result = run(home, 'create', 'Y', '--tool', 'shopping=write', '--allow-delete')
    assert result.exit_code == 0 and 'can move items to the trash' in result.output
    assert run(home, 'revoke', 'Nobody').exit_code != 0


def test_cli_works_with_an_admin_password_set(home):
    from app import admin
    with home.ctx():
        admin.set_admin_password('4321')
    assert run(home, 'create', 'X', '--tool', 'notice=read').exit_code == 0


def test_activity_is_pruned_per_token(home, monkeypatch):
    monkeypatch.setattr(tokens, 'ACTIVITY_KEEP', 3)
    home.token('Busy', notice='read')
    home.token('Quiet', notice='read')
    with home.ctx():
        busy, quiet = tokens.find_by_name('Busy'), tokens.find_by_name('Quiet')
        tokens.log_activity(quiet, 'Mom', 'notes_add', 'keep me')
        for i in range(50):
            tokens.log_activity(busy, 'Dad', 'shopping_add', f'item {i}')
        assert [a.target for a in tokens.recent_activity(quiet)] == ['keep me']
        assert [a.target for a in tokens.recent_activity(busy)] == ['item 49', 'item 48', 'item 47']
        assert ApiActivity.query.count() == 4


def test_default_activity_limit_is_200_per_token():
    assert tokens.ACTIVITY_KEEP == 200


# --- A token whose name is now a person's ---

def test_writes_as_a_token_named_like_a_new_family_member_are_refused(home):
    secret = home.token('Asha', act_as=True, notes='write')
    assert home.post('/notes', secret, json={'content': 'before'}).status_code == 201
    home.set(family_members=['Priya', 'Dad', 'Mom', 'asha'])
    resp = home.post('/notes', secret, json={'content': 'after'})
    assert resp.status_code == 409 and resp.get_json()['error'] == 'conflict'
    assert 'rename' in resp.get_json()['message']
    # Acting as a real family member still works, and so does reading
    assert home.post('/notes', secret, json={'content': 'for Mom', 'by': 'Mom'}).status_code == 201
    assert home.get('/notes', secret).status_code == 200
    with home.ctx():
        tokens.rename_token(tokens.find_by_name('Asha'), 'Asha bot')
    assert home.post('/notes', secret, json={'content': 'renamed'}).get_json()['creator'] == 'Asha bot'


def test_rename_follows_the_name_rules(home):
    home.token('Hermes', notice='read')
    home.token('Other', notice='read')
    with home.ctx():
        token = tokens.find_by_name('Hermes')
        for bad in ('', 'Priya', 'admin', 'other', 'x' * 41):
            with pytest.raises(tokens.TokenError):
                tokens.rename_token(token, bad)
        assert tokens.rename_token(token, 'hermes') == 'hermes'  # its own name in another case is fine
        assert tokens.rename_token(token, 'Hermes 2') == 'Hermes 2'


def test_cli_rename(home):
    secret = home.token('Hermes', notice='read')
    result = run(home, 'rename', 'hermes', 'Kitchen agent')
    assert result.exit_code == 0 and 'renamed to Kitchen agent' in result.output
    assert home.get('/', secret).get_json()['token'] == 'Kitchen agent'
    assert run(home, 'rename', 'Kitchen agent', 'Priya').exit_code != 0
    assert run(home, 'rename', 'Nobody', 'X').exit_code != 0


def test_cli_list_flags_a_name_clash(home):
    home.token('Asha', notice='read')
    home.set(family_members=['Priya', 'Asha'])
    assert 'writes blocked until renamed' in run(home, 'list').output


def test_repeated_query_parameter_has_its_own_message(home):
    secret = home.token('Hermes', reminders='read')
    resp = home.get('/reminders?scope=week&scope=today', secret)
    assert resp.status_code == 400 and resp.get_json()['message'] == 'scope was given more than once.'
    assert 'not a known field' in home.get('/reminders?colour=red', secret).get_json()['message']


# --- Deleting revoked tokens ---

def test_delete_revoked_token_deletes_its_history(home):
    home.token('Hermes', notice='read')
    home.token('Other', notice='read')
    with home.ctx():
        token = tokens.find_by_name('Hermes')
        tokens.log_activity(token, 'Mom', 'notes_add', 'gone')
        tokens.log_activity(tokens.find_by_name('Other'), 'Dad', 'notes_add', 'stays')
        with pytest.raises(tokens.TokenError, match='Revoke'):
            tokens.delete_token(token)
        tokens.revoke_token(token)
        # Revoking keeps the history
        assert [a.target for a in tokens.recent_activity(token)] == ['gone']
        tokens.delete_token(token)
        assert tokens.find_by_name('Hermes') is None
        assert [a.target for a in ApiActivity.query.all()] == ['stays']
    # The name is free again, and the new token does not show the old history
    home.token('Hermes', notice='read')
    with home.ctx():
        assert tokens.recent_activity(tokens.find_by_name('Hermes')) == []


def test_delete_removes_every_row_of_the_token(home):
    home.token('Hermes', notice='read')
    with home.ctx():
        token = tokens.find_by_name('Hermes')
        for i in range(10):
            tokens.log_activity(token, 'Mom', 'notes_add', f'{i}')
        tokens.revoke_token(token)
        tokens.delete_token(token)
        assert ApiActivity.query.count() == 0


def test_cli_delete(home):
    home.token('Hermes', notice='read')
    result = run(home, 'delete', 'Hermes', '--yes')
    assert result.exit_code != 0 and 'Revoke it first' in result.output
    run(home, 'revoke', 'Hermes')
    assert home.app.test_cli_runner().invoke(args=['api-token', 'delete', 'Hermes'], input='n\n').exit_code != 0
    with home.ctx():
        assert tokens.find_by_name('Hermes') is not None
    result = home.app.test_cli_runner().invoke(args=['api-token', 'delete', 'hermes'], input='y\n')
    assert result.exit_code == 0 and 'deleted' in result.output
    assert 'Its activity history is deleted too.' in result.output
    assert run(home, 'create', 'Hermes', '--tool', 'notice=read').exit_code == 0
    assert run(home, 'delete', 'Nobody', '--yes').exit_code != 0


def test_cli_activity(home):
    home.token('Hermes', notes='write')
    assert 'No changes made with Hermes yet.' in run(home, 'activity', 'hermes').output
    with home.ctx():
        token = tokens.find_by_name('Hermes')
        tokens.log_activity(token, 'Priya', 'notes_add', 'first')
        tokens.log_activity(token, 'Dad', 'notes_add', 'second')
    result = run(home, 'activity', 'Hermes')
    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert lines[0].endswith('UTC  Dad  notes_add  second') and lines[1].endswith('UTC  Priya  notes_add  first')
    assert lines[2] == '2 changes made with Hermes.'
    result = run(home, 'activity', 'Hermes', '--csv')
    assert result.output.splitlines()[0] == 'time_utc,token,acting_as,action,target'
    assert result.output.splitlines()[1].endswith(',Hermes,Dad,notes_add,second')
    assert run(home, 'activity', 'Nobody').exit_code != 0
