"""API tokens: the tools they can reach, and creating, checking and revoking them.

The admin page (/admin/api) and the ``flask api-token`` command both use these functions,
so the rules are the same whichever one makes a token.
"""
import hashlib
import html
import json
import secrets
from dataclasses import dataclass

import bleach
from datetime import timedelta

from flask import current_app, g, has_request_context
from flask_babel import gettext as _

from ..clock import utcnow
from ..models import ApiActivity, ApiToken, db

TOKEN_PREFIX = 'hh_'
# 'hh_' and four characters of the random part: enough to tell tokens apart in the list
SHOWN_PREFIX_LENGTH = 7
NAME_MAX_LENGTH = 40
# Changes kept per token, so one busy client cannot push another client's history out
ACTIVITY_KEEP = 200
LAST_USED_EVERY = timedelta(minutes=1)
LEVELS = ('read', 'write')


def N_(text):
    """Marks a string for translation (pybabel extracts N_); it is translated when shown."""
    return text


@dataclass(frozen=True)
class Tool:
    key: str
    # Key under feature_toggles in config.yml, "section.key" for a switch elsewhere in config.yml
    # (weather.enabled), or None for a tool that is always on
    feature: str | None
    # What a missing feature_toggles line means (the same defaults as the sidebar in base.html)
    default_on: bool
    label_text: str
    levels: tuple
    read_help_text: str
    write_help_text: str | None = None
    # Why there is no Read & write level (shown on the dash in the token page's table)
    no_write_reason_text: str | None = None
    # What the API says when the tool is switched off; without it, a plain "Not found."
    switched_off_text: str | None = None

    @property
    def label(self) -> str:
        return _(self.label_text)

    @property
    def read_help(self) -> str:
        return _(self.read_help_text)

    @property
    def write_help(self) -> str | None:
        return _(self.write_help_text) if self.write_help_text else None

    @property
    def switched_off(self) -> str | None:
        return _(self.switched_off_text) if self.switched_off_text else None

    @property
    def no_write_reason(self) -> str:
        return _(self.no_write_reason_text) if self.no_write_reason_text else _('Not available for this tool')


# In the order the token page lists them
TOOLS = (
    Tool('presence', 'who_is_home', True, N_('Who is home'), ('read', 'write'),
         N_("Everyone's Home or Away"), N_('Set a person Home or Away (needs Act as family members)')),
    Tool('statuses', 'personal_status', True, N_('Personal status'), ('read', 'write'),
         N_("Everyone's status text"), N_("Set or clear a person's status (needs Act as family members)")),
    Tool('notice', None, True, N_('Notice board'), ('read',),
         N_('Read only. Only the admin can change the notice in HomeHub.'), None,
         N_('Not available: only the admin can change the notice')),
    Tool('weather', 'weather.enabled', False, N_('Weather'), ('read',),
         N_('Current weather, the next 24 hours and 7 days, for the place set in config.yml'), None,
         N_('Not available: weather can only be read'), N_('Weather is switched off in config.yml.')),
    Tool('reminders', 'calendar', True, N_('Reminders'), ('read', 'write'),
         N_('Reminders for a day, a week or a range'), N_('Add and edit reminders, mark them done or not done')),
    Tool('shopping', 'shopping_list', False, N_('Shopping list'), ('read', 'write'),
         N_('Items with their tags'), N_('Add, edit and tick items')),
    Tool('chores', 'chores', False, N_('Chores'), ('read', 'write'),
         N_('Chores with due dates and tags'), N_('Add and edit chores, mark them done or not done')),
    Tool('notes', 'notes', False, N_('Notes'), ('read', 'write'),
         N_('Shared notes'), N_('Add a note')),
    Tool('recipes', 'recipes', False, N_('Recipes'), ('read', 'write'),
         N_('Recipes with their tags'), N_('Add and edit recipes')),
    Tool('expiry', 'expiry_tracker', False, N_('Expiry tracker'), ('read', 'write'),
         N_('Items with expiry dates'), N_('Add an item')),
    Tool('expenses', 'expense_tracker', True, N_('Expenses'), ('read', 'write'),
         N_('Recurring expenses and entries'), N_('Skip or restore a recurring day, change its quantity, add an expense')),
)
TOOLS_BY_KEY = {t.key: t for t in TOOLS}
# Tools whose writes change a person, so they only make sense when the token can act as family members
NEEDS_ACT_AS = ('presence', 'statuses')


class TokenError(ValueError):
    """A request to make or change a token that breaks the rules; the message is shown to the admin."""


def _config() -> dict:
    return current_app.config.get('HOMEHUB_CONFIG') or {}


def tool_enabled(key: str, config: dict | None = None) -> bool:
    tool = TOOLS_BY_KEY.get(key)
    if tool is None:
        return False
    if tool.feature is None:
        return True
    config = config if config is not None else _config()
    section, dot, key = tool.feature.partition('.')
    if dot:
        return bool((config.get(section) or {}).get(key, tool.default_on))
    toggles = config.get('feature_toggles') or {}
    return bool(toggles.get(tool.feature, tool.default_on))


def enabled_tools(config: dict | None = None) -> list:
    config = config if config is not None else _config()
    return [t for t in TOOLS if tool_enabled(t.key, config)]


def family_members(config: dict | None = None) -> list:
    config = config if config is not None else _config()
    return list(dict.fromkeys(str(m) for m in config.get('family_members') or []))


def admin_names(config: dict | None = None) -> set:
    config = config if config is not None else _config()
    return {str(config.get('admin_name') or 'Administrator'), 'Administrator', 'admin'}


def is_admin_name(name: str, config: dict | None = None) -> bool:
    folded = (name or '').casefold()
    return any(folded == a.casefold() for a in admin_names(config))


# --- Tokens ---

def generate_secret() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    return hashlib.sha256((secret or '').encode('utf-8')).hexdigest()


def clean_name(raw) -> str:
    """Plain text: tags removed (like other user text, see sanitize_text), spaces collapsed, no control characters."""
    text = html.unescape(bleach.clean(str(raw or ''), tags=set(), strip=True))
    text = ''.join(ch for ch in text if ch.isprintable())
    return ' '.join(text.split())


def check_name(raw, exclude_id: int | None = None) -> str:
    """The token name to store, or TokenError saying what is wrong with it."""
    name = clean_name(raw)
    if not name:
        raise TokenError(_('Give the token a name.'))
    if len(name) > NAME_MAX_LENGTH:
        raise TokenError(_('The name can be at most %(num)s characters.', num=NAME_MAX_LENGTH))
    folded = name.casefold()
    if is_admin_name(name):
        raise TokenError(_('The name cannot be the admin name.'))
    if any(folded == m.casefold() for m in family_members()):
        raise TokenError(_('The name cannot be the name of a family member.'))
    for other in ApiToken.query.all():
        if other.id != exclude_id and other.name.casefold() == folded:
            if other.revoked_at:
                raise TokenError(_('A revoked token is already called %(name)s. Regenerate it to use it again, or pick another name.', name=other.name))
            raise TokenError(_('A token called %(name)s already exists.', name=other.name))
    return name


def clean_permissions(raw: dict, act_as_members: bool) -> dict:
    """{tool: level} with "none" left out; TokenError for an unknown tool or level."""
    permissions = {}
    for key, level in (raw or {}).items():
        key = str(key).strip().lower()
        level = str(level or '').strip().lower()
        tool = TOOLS_BY_KEY.get(key)
        if tool is None:
            raise TokenError(_('Unknown tool: %(name)s.', name=key))
        if level in ('', 'none'):
            continue
        if level not in tool.levels:
            raise TokenError(_('%(tool)s cannot be given the level %(level)s.', tool=str(tool.label), level=level))
        permissions[key] = level
    if not act_as_members:
        for key in NEEDS_ACT_AS:
            if permissions.get(key) == 'write':
                raise TokenError(_('%(tool)s with Read & write needs Act as family members.', tool=str(TOOLS_BY_KEY[key].label)))
    return {t.key: permissions[t.key] for t in TOOLS if t.key in permissions}


def can_delete(permissions: dict) -> bool:
    """Allow deleting only covers moving reminders to the trash, so it needs Reminders Read & write."""
    return permissions.get('reminders') == 'write'


def create_token(name, permissions: dict, act_as_members: bool = False, allow_delete: bool = False):
    """Make a token. Returns (row, secret); the secret is never stored and cannot be shown again."""
    name = check_name(name)
    act_as_members = bool(act_as_members)
    permissions = clean_permissions(permissions, act_as_members)
    secret = generate_secret()
    token = ApiToken(
        name=name,
        token_hash=hash_secret(secret),
        prefix=secret[:SHOWN_PREFIX_LENGTH],
        permissions=json.dumps(permissions),
        act_as_members=act_as_members,
        allow_delete=bool(allow_delete) and can_delete(permissions),
        created_at=utcnow(),
    )
    db.session.add(token)
    db.session.commit()
    return token, secret


def name_taken_by_person(name: str, config: dict | None = None) -> bool:
    """True when ``name`` is now a family member's or an admin name (for example after config.yml changed)."""
    folded = (name or '').casefold()
    return is_admin_name(name, config) or any(folded == m.casefold() for m in family_members(config))


def rename_token(token: ApiToken, new_name) -> str:
    """Give the token a new name (same rules as a new token); its value and access stay the same."""
    name = check_name(new_name, exclude_id=token.id)
    token.name = name
    db.session.commit()
    return name


def regenerate_token(token: ApiToken) -> str:
    """Give the token a new secret; the old one stops working at once. A revoked token becomes active again."""
    secret = generate_secret()
    token.token_hash = hash_secret(secret)
    token.prefix = secret[:SHOWN_PREFIX_LENGTH]
    token.revoked_at = None
    token.last_used_at = None
    token.created_at = utcnow()
    db.session.commit()
    return secret


def delete_token(token: ApiToken) -> None:
    """Remove a revoked token for good, with its activity history, so its name can be used again.

    Revoking keeps the history; deleting removes it too (download the CSV first to keep a copy).
    """
    if not token.revoked_at:
        raise TokenError(_('Revoke the token before deleting it.'))
    ApiActivity.query.filter(ApiActivity.token_id == token.id).delete(synchronize_session=False)
    db.session.delete(token)
    db.session.commit()


def revoke_token(token: ApiToken) -> None:
    if not token.revoked_at:
        token.revoked_at = utcnow()
        db.session.commit()


def find_by_name(name) -> ApiToken | None:
    folded = clean_name(name).casefold()
    if not folded:
        return None
    for token in ApiToken.query.all():
        if token.name.casefold() == folded:
            return token
    return None


def all_tokens() -> list:
    """Active tokens first (newest first), then revoked ones."""
    rows = ApiToken.query.all()
    return sorted(rows, key=lambda t: (t.revoked_at is not None, -(t.created_at.timestamp() if t.created_at else 0)))


def any_active() -> bool:
    return db.session.query(ApiToken.id).filter(ApiToken.revoked_at.is_(None)).first() is not None


def lookup(secret: str | None) -> ApiToken | None:
    """The active token for this secret, or None. Notes when it was last used (at most once a minute)."""
    if not secret or not secret.startswith(TOKEN_PREFIX) or len(secret) > 200:
        return None
    token = ApiToken.query.filter_by(token_hash=hash_secret(secret)).first()
    if token is None or token.revoked_at is not None:
        return None
    now = utcnow()
    if token.last_used_at is None or now - token.last_used_at >= LAST_USED_EVERY:
        token.last_used_at = now
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Could not note when API token %s was last used', token.id)
    return token


def effective_permissions(token: ApiToken, config: dict | None = None) -> dict:
    """{tool: level} for the tools this token can reach now: switched-off features are left out."""
    config = config if config is not None else _config()
    stored = token.permission_map
    return {
        t.key: stored[t.key]
        for t in TOOLS
        if stored.get(t.key) in t.levels and tool_enabled(t.key, config)
    }


def level_allows(granted: str | None, wanted: str) -> bool:
    """Read & write includes read."""
    if granted == 'write':
        return wanted in LEVELS
    return granted == 'read' and wanted == 'read'


# --- Request helpers ---

def bearer_secret(request) -> str | None:
    """The token from "Authorization: Bearer ...". Never read from the query string, a cookie or a form."""
    header = request.headers.get('Authorization', '')
    scheme, _sep, value = header.partition(' ')
    if scheme.lower() != 'bearer':
        return None
    value = value.strip()
    return value or None


def request_token(request) -> ApiToken | None:
    """The token this request carries, looked up once per request."""
    if has_request_context() and '_api_token' in g:
        return g._api_token
    token = lookup(bearer_secret(request))
    if has_request_context():
        g._api_token = token
    return token


# --- Activity log ---

def log_activity(token: ApiToken, acting_as: str, action: str, target: str) -> None:
    db.session.add(ApiActivity(
        token_id=token.id,
        token_name=token.name,
        acting_as=(acting_as or '')[:64],
        action=(action or '')[:64],
        target=(target or '')[:200],
        created_at=utcnow(),
    ))
    db.session.flush()
    keep = (db.session.query(ApiActivity.id).filter(ApiActivity.token_id == token.id)
            .order_by(ApiActivity.id.desc()).offset(ACTIVITY_KEEP - 1).limit(1).scalar())
    if keep is not None:
        ApiActivity.query.filter(ApiActivity.token_id == token.id, ApiActivity.id < keep).delete(synchronize_session=False)
    db.session.commit()


def recent_activity(token: ApiToken, limit: int = 20) -> list:
    return (ApiActivity.query.filter_by(token_id=token.id)
            .order_by(ApiActivity.id.desc()).limit(limit).all())


def activity_count(token: ApiToken) -> int:
    return ApiActivity.query.filter_by(token_id=token.id).count()


def all_activity(token: ApiToken) -> list:
    """Every stored change made with the token, newest first (at most ACTIVITY_KEEP)."""
    return recent_activity(token, ACTIVITY_KEEP)


ACTIVITY_CSV_COLUMNS = ('time_utc', 'token', 'acting_as', 'action', 'target')


def activity_csv(token: ApiToken) -> str:
    """The token's activity as CSV text, newest first, with a BOM so spreadsheets read it as UTF-8."""
    import csv
    import io
    from ..blueprints.expenses import _csv_text
    out = io.StringIO()
    out.write('\ufeff')
    writer = csv.writer(out)
    writer.writerow(ACTIVITY_CSV_COLUMNS)
    for a in all_activity(token):
        writer.writerow([
            a.created_at.strftime('%Y-%m-%dT%H:%M:%SZ') if a.created_at else '',
            _csv_text(a.token_name), _csv_text(a.acting_as), _csv_text(a.action), _csv_text(a.target),
        ])
    return out.getvalue()


def activity_filename(token: ApiToken) -> str:
    """A file name for the token's CSV: letters, digits and dashes only."""
    import re
    slug = re.sub(r'[^A-Za-z0-9]+', '-', token.name).strip('-').lower() or f'token-{token.id}'
    return f'homehub-api-activity-{slug}.csv'
