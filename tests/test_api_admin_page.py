"""Admin tools > API access: admin only, tokens shown once, name rules, enabled tools, presets."""
import re

import pytest

from api_support import Home
from app import admin
from app.api import tokens
from app.blueprints import api_admin
from app.models import ApiToken

PASSWORD = '4321'
ADMIN = 'Administrator'


@pytest.fixture()
def bare(tmp_path, monkeypatch):
    return Home(tmp_path, monkeypatch)


@pytest.fixture()
def home(bare):
    with bare.ctx():
        admin.set_admin_password(PASSWORD)
    assert bare.client.post('/admin/unlock', json={'password': PASSWORD}).get_json()['ok']
    return bare


def create(home, name='Home Assistant', act_as=False, allow_delete=False, **perms):
    data = {'user': ADMIN, 'name': name}
    data.update({f'perm_{k}': v for k, v in perms.items()})
    if act_as:
        data['act_as'] = '1'
    if allow_delete:
        data['allow_delete'] = '1'
    return home.client.post('/admin/api/create', data=data)


def shown_secret(resp):
    match = re.search(r'value="(hh_[A-Za-z0-9_-]+)"', resp.get_data(as_text=True))
    return match.group(1) if match else None


# --- Access ---

def test_needs_an_admin_password(bare):
    resp = bare.client.get('/admin/api?user=Administrator')
    assert resp.status_code == 403
    body = resp.get_data(as_text=True)
    assert 'flask set-admin-password' in body and 'flask api-token create' in body
    assert create(bare).status_code == 403
    with bare.ctx():
        assert ApiToken.query.count() == 0


def test_needs_the_unlocked_admin(bare):
    with bare.ctx():
        admin.set_admin_password(PASSWORD)
    # Admin name without unlocking, and a family member: both sent back to the Welcome page
    for user in (ADMIN, 'Priya'):
        resp = bare.client.get(f'/admin/api?user={user}')
        assert resp.status_code == 302 and resp.headers['Location'].endswith('/')
    resp = bare.client.post('/admin/api/create', data={'user': ADMIN, 'name': 'X', 'perm_notice': 'read'})
    assert resp.status_code == 302
    with bare.ctx():
        assert ApiToken.query.count() == 0


def test_page_loads_for_admin_with_no_store(home):
    resp = home.client.get('/admin/api?user=Administrator')
    assert resp.status_code == 200
    assert resp.headers['Cache-Control'] == 'no-store'
    body = resp.get_data(as_text=True)
    assert 'Create a token' in body and 'No tokens yet' in body
    assert 'docs/api.md' in body


def test_admin_tools_card_links_to_the_page(home):
    body = home.client.get('/').get_data(as_text=True)
    assert 'action="/admin/api"' in body
    assert "Back up HomeHub&#39;s data, and manage tokens for the API." in body


# --- Create, shown once ---

def test_create_shows_the_token_once(home):
    resp = create(home, presence='write', reminders='read', act_as=True)
    assert resp.status_code == 200
    assert resp.headers['Cache-Control'] == 'no-store'
    secret = shown_secret(resp)
    assert secret
    body = resp.get_data(as_text=True)
    assert 'Copy this token now' in body
    assert f'curl -H "Authorization: Bearer {secret}" http://localhost/api/v1/' in body
    assert home.get('/', secret).get_json()['tools'] == {'presence': 'write', 'reminders': 'read'}
    # A refresh (a GET of the page) no longer shows it, nor does the Welcome page
    again = home.client.get('/admin/api?user=Administrator').get_data(as_text=True)
    assert secret not in again and 'Copy this token now' not in again
    assert secret not in home.client.get('/').get_data(as_text=True)
    with home.client.session_transaction() as sess:
        assert secret not in repr(dict(sess))
    assert secret not in (resp.headers.get('Location') or '')


def test_regenerate_shows_new_token_once(home):
    old = shown_secret(create(home, notice='read'))
    with home.ctx():
        token_id = ApiToken.query.one().id
    resp = home.client.post(f'/admin/api/{token_id}/regenerate', data={'user': ADMIN})
    assert resp.status_code == 200 and resp.headers['Cache-Control'] == 'no-store'
    new = shown_secret(resp)
    assert new and new != old
    assert home.get('/', old).status_code == 401
    assert home.get('/', new).status_code == 200
    assert new not in home.client.get('/admin/api?user=Administrator').get_data(as_text=True)


def test_revoke(home):
    secret = shown_secret(create(home, notice='read'))
    with home.ctx():
        token_id = ApiToken.query.one().id
    resp = home.client.post(f'/admin/api/{token_id}/revoke', data={'user': ADMIN})
    assert resp.status_code == 302
    assert home.get('/', secret).status_code == 401
    body = home.client.get('/admin/api?user=Administrator').get_data(as_text=True)
    assert 'Revoked' in body


def test_regenerate_and_revoke_ask_first(home):
    create(home, notice='read')
    body = home.client.get('/admin/api?user=Administrator').get_data(as_text=True)
    assert re.search(r'regenerate"\s+data-confirm="Make a new token', body)
    assert re.search(r'revoke"\s+data-confirm="Revoke', body)
    assert 'class="btn btn-danger btn-sm"' in body


@pytest.mark.parametrize('name, message', [
    ('Priya', 'family member'),
    ('administrator', 'admin name'),
    ('', 'Give the token a name'),
])
def test_name_rules(home, name, message):
    resp = create(home, name=name, notice='read')
    assert resp.status_code == 400
    assert message in resp.get_data(as_text=True)
    assert shown_secret(resp) is None


def test_duplicate_name(home):
    create(home, name='Hermes', notice='read')
    resp = create(home, name='HERMES', notice='read')
    assert resp.status_code == 400 and 'already exists' in resp.get_data(as_text=True)


def test_needs_at_least_one_tool(home):
    resp = create(home, name='Empty')
    assert resp.status_code == 400 and 'at least one tool' in resp.get_data(as_text=True)


def test_write_on_presence_needs_act_as(home):
    resp = create(home, name='X', presence='write')
    assert resp.status_code == 400 and 'Act as family members' in resp.get_data(as_text=True)


def test_allow_delete_needs_reminders_write(home):
    create(home, name='A', reminders='read', allow_delete=True)
    create(home, name='B', reminders='write', allow_delete=True)
    with home.ctx():
        assert tokens.find_by_name('A').allow_delete is False
        assert tokens.find_by_name('B').allow_delete is True
    body = home.client.get('/admin/api?user=Administrator').get_data(as_text=True)
    assert body.count('Can move reminders to the trash') == 1


# --- What the page lists ---

def test_only_enabled_tools_are_offered(home):
    home.toggle('recipes', False)
    body = home.client.get('/admin/api?user=Administrator').get_data(as_text=True)
    assert 'name="perm_shopping"' in body
    assert 'name="perm_recipes"' not in body
    # A switched-off tool can't be granted by posting it anyway
    resp = create(home, name='Sneaky', recipes='write', notice='read')
    assert resp.status_code == 200
    with home.ctx():
        assert tokens.find_by_name('Sneaky').permission_map == {'notice': 'read'}


def test_table_lists_tools_in_order_with_dash_for_missing_levels(home):
    body = home.client.get('/admin/api?user=Administrator').get_data(as_text=True)
    order = [m for m in re.findall(r'data-tool="(\w+)"', body)]
    assert order == [t.key for t in tokens.TOOLS]
    notice_row = body.split('data-tool="notice"')[1].split('</tr>')[0]
    assert 'value="write"' not in notice_row and '–' in notice_row


def test_access_summary_follows_table_order(home):
    create(home, name='Mixed', expenses='read', presence='read', notes='write')
    body = home.client.get('/admin/api?user=Administrator').get_data(as_text=True)
    summary = body.split('class="text-xs mt-0.5 token-access"')[1].split('</div>')[0]
    assert summary.index('Who is home') < summary.index('Notes') < summary.index('Expenses')


def test_token_whose_tools_are_all_off_stays_listed(home):
    create(home, name='Shopper', shopping='read')
    home.toggle('shopping_list', False)
    body = home.client.get('/admin/api?user=Administrator').get_data(as_text=True)
    assert 'Shopper' in body and 'Currently reaches nothing' in body


def test_recent_activity_is_listed(home):
    secret = shown_secret(create(home, name='Hermes', notes='write'))
    with home.ctx():
        token = tokens.find_by_name('Hermes')
        tokens.log_activity(token, 'Priya', 'notes_add', 'Buy a gift')
    body = home.client.get('/admin/api?user=Administrator').get_data(as_text=True)
    assert 'Recent activity' in body and 'Buy a gift' in body and 'notes_add' in body
    assert secret


def test_presets(home):
    all_on = [t.key for t in tokens.TOOLS]
    p = api_admin.presets(all_on)
    assert p['read_all'] == {'permissions': {k: 'read' for k in all_on}, 'act_as': False, 'allow_delete': False}
    assert p['home_automation'] == {'permissions': {'presence': 'write', 'notice': 'read', 'reminders': 'read'},
                                    'act_as': True, 'allow_delete': False}
    ai = p['ai_assistant']
    assert ai['act_as'] is True and ai['allow_delete'] is False
    assert ai['permissions']['notice'] == 'read'
    assert all(ai['permissions'][k] == 'write' for k in all_on if k != 'notice')
    # Switched-off tools are left out of every preset
    p = api_admin.presets(['presence', 'notice'])
    assert p['home_automation']['permissions'] == {'presence': 'write', 'notice': 'read'}
    assert set(p['ai_assistant']['permissions']) == {'presence', 'notice'}
    body = home.client.get('/admin/api?user=Administrator').get_data(as_text=True)
    assert 'data-preset="read_all"' in body and 'data-preset="home_automation"' in body and 'data-preset="ai_assistant"' in body


def test_rename_on_the_page(home):
    secret = shown_secret(create(home, name='Hermes', notice='read'))
    with home.ctx():
        token_id = ApiToken.query.one().id
    resp = home.client.post(f'/admin/api/{token_id}/rename', data={'user': ADMIN, 'name': 'Kitchen agent'})
    assert resp.status_code == 302
    assert home.get('/', secret).get_json()['token'] == 'Kitchen agent'  # the token keeps working
    resp = home.client.post(f'/admin/api/{token_id}/rename', data={'user': ADMIN, 'name': 'Priya'}, follow_redirects=True)
    assert 'family member' in resp.get_data(as_text=True)
    with home.ctx():
        assert ApiToken.query.one().name == 'Kitchen agent'


def test_rename_needs_the_unlocked_admin(bare):
    with bare.ctx():
        admin.set_admin_password(PASSWORD)
        tokens.create_token('Hermes', {'notice': 'read'})
        token_id = ApiToken.query.one().id
    resp = bare.client.post(f'/admin/api/{token_id}/rename', data={'user': ADMIN, 'name': 'X'})
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/')
    with bare.ctx():
        assert ApiToken.query.one().name == 'Hermes'


def test_name_clash_is_flagged(home):
    create(home, name='Asha', notice='read')
    home.set(family_members=['Priya', 'Dad', 'Mom', 'Asha'])
    body = home.client.get('/admin/api?user=Administrator').get_data(as_text=True)
    assert 'Writes are blocked until it is renamed' in body


def test_notice_row_and_dash_explain_themselves(home):
    body = home.client.get('/admin/api?user=Administrator').get_data(as_text=True)
    row = body.split('data-tool="notice"')[1].split('</tr>')[0]
    assert 'Read only. Only the admin can change the notice in HomeHub.' in row
    reason = 'Not available: only the admin can change the notice'
    assert f'title="{reason}"' in row and f'aria-label="{reason}"' in row


def test_every_row_has_actions_in_the_same_place(home):
    create(home, name='Short', notice='read')
    create(home, name='Long', **{t.key: 'read' for t in tokens.TOOLS})
    body = home.client.get('/admin/api?user=Administrator').get_data(as_text=True)
    assert body.count('class="flex flex-wrap items-center gap-2 token-actions"') == 2
    assert body.count('>Rename</button>') == 2


def test_delete_on_the_page(home):
    create(home, name='Hermes', notice='read')
    with home.ctx():
        token_id = ApiToken.query.one().id
    body = home.client.get('/admin/api?user=Administrator').get_data(as_text=True)
    assert f'/admin/api/{token_id}/delete' not in body  # only revoked tokens can be deleted
    resp = home.client.post(f'/admin/api/{token_id}/delete', data={'user': ADMIN}, follow_redirects=True)
    assert 'Revoke the token before deleting it' in resp.get_data(as_text=True)
    home.client.post(f'/admin/api/{token_id}/revoke', data={'user': ADMIN})
    body = home.client.get('/admin/api?user=Administrator').get_data(as_text=True)
    assert re.search(r'delete"\s+data-confirm="Delete Hermes\? This removes it from the list for good\. Its activity history is kept\."', body)
    home.client.post(f'/admin/api/{token_id}/delete', data={'user': ADMIN})
    with home.ctx():
        assert ApiToken.query.count() == 0
    assert create(home, name='Hermes', notice='read').status_code == 200


def test_delete_needs_the_unlocked_admin(bare):
    with bare.ctx():
        admin.set_admin_password(PASSWORD)
        row, _secret = tokens.create_token('Hermes', {'notice': 'read'})
        tokens.revoke_token(row)
        token_id = row.id
    for user in (ADMIN, 'Priya'):
        assert bare.client.post(f'/admin/api/{token_id}/delete', data={'user': user}).status_code == 302
    assert bare.client.get(f'/admin/api/{token_id}/delete').status_code in (302, 405)
    with bare.ctx():
        assert ApiToken.query.count() == 1


def test_delete_from_another_site_is_blocked(home):
    with home.ctx():
        row, _secret = tokens.create_token('Hermes', {'notice': 'read'})
        tokens.revoke_token(row)
        token_id = row.id
    resp = home.client.post(f'/admin/api/{token_id}/delete', data={'user': ADMIN}, headers={'Sec-Fetch-Site': 'cross-site'})
    assert resp.status_code == 403
    with home.ctx():
        assert ApiToken.query.count() == 1


def test_write_help_mentions_edits(home):
    body = home.client.get('/admin/api?user=Administrator').get_data(as_text=True)
    assert 'Add, edit and tick items' in body and 'Add and edit recipes' in body
