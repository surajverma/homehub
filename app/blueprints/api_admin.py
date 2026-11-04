"""Admin tools > API access: create, regenerate and revoke tokens for the JSON API.

Protected like the Backups page: it needs an admin password, set and unlocked in this browser.
A new token is shown once, in the answer to the POST that made it. It is never kept in the
session, flashed into a later page or put in a URL, and the page is sent with no-store.
"""
from flask import current_app, flash, make_response, redirect, render_template, request, url_for
from flask_babel import gettext as _

from .. import admin
from ..api import tokens
from ..blueprints import main_bp
from ..models import ApiToken, db
from ..security import sanitize_text

API_GUIDE_URL = 'https://github.com/surajverma/homehub/blob/main/docs/api.md'


def presets(enabled_keys) -> dict:
    """The preset buttons: {key: {'permissions': {tool: level}, 'act_as': bool, 'allow_delete': bool}}."""
    enabled = [t for t in tokens.TOOLS if t.key in enabled_keys]
    home_automation = {'presence': 'write', 'notice': 'read', 'reminders': 'read'}
    return {
        'read_all': {'permissions': {t.key: 'read' for t in enabled}, 'act_as': False, 'allow_delete': False},
        'home_automation': {'permissions': {k: v for k, v in home_automation.items() if k in enabled_keys},
                            'act_as': True, 'allow_delete': False},
        'ai_assistant': {'permissions': {t.key: ('write' if 'write' in t.levels else 'read') for t in enabled if t.key != 'notice'}
                         | ({'notice': 'read'} if 'notice' in enabled_keys else {}),
                         'act_as': True, 'allow_delete': False},
    }


def _user() -> str:
    return sanitize_text(request.values.get('user', ''))


def _no_store(response):
    response = make_response(response)
    response.headers['Cache-Control'] = 'no-store'
    return response


def _require_admin():
    """None for the unlocked admin, else the response to send instead (same rules as the Backups page)."""
    if not admin.admin_password_enabled():
        return _no_store((render_template('api_access_locked.html', config=current_app.config['HOMEHUB_CONFIG'],
                                          guide_url=API_GUIDE_URL), 403))
    if admin.is_admin(_user()):
        return None
    flash(_('Only admin can manage API tokens.'), 'error')
    return redirect(url_for('main.index'))


def _summary(token: ApiToken, config) -> dict:
    """What the list shows for a token: access in table order, flags, and whether it reaches anything now."""
    stored = token.permission_map
    access = []
    for tool in tokens.TOOLS:
        level = stored.get(tool.key)
        if level not in tool.levels:
            continue
        access.append({
            'label': str(tool.label),
            'level': _('Read & write') if level == 'write' else _('Read'),
            'enabled': tokens.tool_enabled(tool.key, config),
        })
    reachable = tokens.effective_permissions(token, config)
    return {
        'token': token,
        'access': access,
        'reaches_nothing': not reachable,
        'can_delete': bool(token.allow_delete) and tokens.can_delete(stored),
        'activity': tokens.recent_activity(token, 20),
    }


def _render(new_secret=None, new_name=None, form=None, error=None, status=200):
    config = current_app.config['HOMEHUB_CONFIG']
    enabled = tokens.enabled_tools(config)
    enabled_keys = [t.key for t in enabled]
    return _no_store((render_template(
        'api_access.html',
        config=config,
        user=_user(),
        tools=enabled,
        presets=presets(enabled_keys),
        tokens_list=[_summary(t, config) for t in tokens.all_tokens()],
        new_secret=new_secret,
        new_name=new_name,
        base_url=request.host_url.rstrip('/'),
        form=form or {},
        error=error,
        guide_url=API_GUIDE_URL,
        name_max=tokens.NAME_MAX_LENGTH,
    ), status))


@main_bp.route('/admin/api', methods=['GET'])
def api_access_page():
    denied = _require_admin()
    if denied:
        return denied
    return _render()


@main_bp.route('/admin/api/create', methods=['POST'])
def api_access_create():
    denied = _require_admin()
    if denied:
        return denied
    config = current_app.config['HOMEHUB_CONFIG']
    permissions = {}
    for tool in tokens.enabled_tools(config):
        level = request.form.get(f'perm_{tool.key}', 'none')
        permissions[tool.key] = level if level in tool.levels else 'none'
    act_as = request.form.get('act_as') in ('1', 'on', 'true')
    allow_delete = request.form.get('allow_delete') in ('1', 'on', 'true')
    form = {'name': request.form.get('name', ''), 'permissions': permissions, 'act_as': act_as, 'allow_delete': allow_delete}
    if not any(level != 'none' for level in permissions.values()):
        return _render(form=form, error=_('Give the token access to at least one tool.'), status=400)
    try:
        token, secret = tokens.create_token(form['name'], permissions, act_as, allow_delete)
    except tokens.TokenError as exc:
        return _render(form=form, error=str(exc), status=400)
    return _render(new_secret=secret, new_name=token.name)


def _get_token(token_id):
    return db.session.get(ApiToken, token_id)


@main_bp.route('/admin/api/<int:token_id>/regenerate', methods=['POST'])
def api_access_regenerate(token_id):
    denied = _require_admin()
    if denied:
        return denied
    token = _get_token(token_id)
    if token is None:
        flash(_('That token was not found.'), 'error')
        return redirect(url_for('main.api_access_page', user=_user()))
    secret = tokens.regenerate_token(token)
    return _render(new_secret=secret, new_name=token.name)


@main_bp.route('/admin/api/<int:token_id>/revoke', methods=['POST'])
def api_access_revoke(token_id):
    denied = _require_admin()
    if denied:
        return denied
    token = _get_token(token_id)
    if token is None:
        flash(_('That token was not found.'), 'error')
    else:
        tokens.revoke_token(token)
        flash(_('Token %(name)s revoked. Anything that used it can no longer reach HomeHub.', name=token.name), 'success')
    return redirect(url_for('main.api_access_page', user=_user()))
