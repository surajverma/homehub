"""Request handling shared by the REST routes and the MCP endpoint: access checks, the acting
person (``by``), running an operation, and the JSON error shape."""
import functools
import json

from flask import current_app, jsonify, request
from flask_babel import gettext as _

from ..models import db
from ..security import sanitize_text
from . import registry, tokens

API_PREFIX = '/api/v1'
MAX_BODY_BYTES = 64 * 1024
API_VERSION = '1'


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str | None = None):
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message


def default_message(code: str) -> str:
    return {
        'unauthorized': _('A valid API token is required. Send it as "Authorization: Bearer <token>".'),
        'forbidden': _('This token does not have access to that.'),
        'not_found': _('Not found.'),
        'bad_request': _('The request is not valid.'),
        'conflict': _('That cannot be done right now.'),
        'too_large': _('The request is too large.'),
        'method_not_allowed': _('That method is not allowed here.'),
        'server_error': _('Something went wrong. Please try again.'),
    }.get(code, code)


def no_store(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


def json_response(payload, status: int = 200):
    response = jsonify(payload)
    response.status_code = status
    return no_store(response)


def error_response(status: int, code: str, message: str | None = None):
    response = json_response({'error': code, 'message': message or default_message(code)}, status)
    if status == 401:
        response.headers['WWW-Authenticate'] = 'Bearer realm="HomeHub"'
    return response


def is_api_path(path: str) -> bool:
    return path == API_PREFIX or path.startswith(API_PREFIX + '/')


def current_token():
    return tokens.request_token(request)


# --- Access ---

def check_access(token, tool: str | None, level: str) -> None:
    """Raise ApiError unless ``token`` may use ``tool`` at ``level``.

    A tool whose feature is switched off answers 404 whatever the token was given, so it is
    as if the tool were not there.
    """
    if token is None:
        raise ApiError(401, 'unauthorized')
    if tool is None:
        return
    if not tokens.tool_enabled(tool):
        raise ApiError(404, 'not_found')
    granted = tokens.effective_permissions(token).get(tool)
    if not tokens.level_allows(granted, level):
        label = str(tokens.TOOLS_BY_KEY[tool].label)
        if granted == 'read':
            message = _('This token can read %(tool)s but not change it.', tool=label)
        else:
            message = _('This token has no access to %(tool)s.', tool=label)
        raise ApiError(403, 'forbidden', message)


def require_api(tool: str | None = None, level: str = 'read'):
    """Decorator for API views: a valid Bearer token with access to ``tool`` at ``level``."""
    def wrap(view):
        @functools.wraps(view)
        def inner(*args, **kwargs):
            try:
                check_access(current_token(), tool, level)
            except ApiError as exc:
                return error_response(exc.status, exc.code, exc.message)
            return view(*args, **kwargs)
        return inner
    return wrap


# --- Acting person ---

def acting_name(token, by) -> tuple:
    """(name a write is made as, True when ``by`` named a family member); else the token's own name."""
    if by is None:
        return _token_as_author(token), False
    if not token.act_as_members:
        raise ApiError(400, 'bad_request', _('This token cannot act as family members, so "by" is not allowed.'))
    wanted = sanitize_text(str(by)).strip()
    if not wanted:
        return _token_as_author(token), False
    if tokens.is_admin_name(wanted):
        raise ApiError(400, 'bad_request', _('The API cannot act as the admin.'))
    for member in tokens.family_members():
        if member.casefold() == wanted.casefold():
            if tokens.is_admin_name(member):
                raise ApiError(400, 'bad_request', _('The API cannot act as the admin.'))
            return member, True
    raise ApiError(400, 'bad_request', _('%(name)s is not a family member.', name=wanted))


def _token_as_author(token) -> str:
    """The token's name, unless a family member or the admin now has that name: then its changes would look like theirs."""
    if tokens.name_taken_by_person(token.name):
        raise ApiError(409, 'conflict', _('This token is called %(name)s, the same as a person in HomeHub, so its changes would look like theirs. Ask the admin to rename the token under Admin tools > API access.', name=token.name))
    return token.name


class Context:
    """What a handler knows about the call: the token, who it acts as, and what to log."""

    def __init__(self, token, op, acting_as: str | None, acting_given: bool):
        self.token = token
        self.op = op
        self.user = acting_as
        # True when "by" named a family member (not just the token name)
        self.acting_given = acting_given
        self.target = None

    def require_member(self, message: str | None = None) -> str:
        """The family member this call acts for; "by" is needed for changes that belong to a person."""
        if not self.acting_given:
            raise ApiError(400, 'bad_request', message or _('Say which family member this is for with "by".'))
        return self.user


# --- Running an operation ---

def run_operation(op, token, args: dict, *, from_query=()):
    """Check access, validate, run the handler and log the change. Returns (status, payload).

    Both the REST route and MCP tools/call come through here.
    """
    check_access(token, op.tool, op.level)
    if op.destructive and not token.allow_delete:
        raise ApiError(403, 'forbidden', _('This token is not allowed to delete.'))
    try:
        cleaned = registry.validate(op, args, from_query=from_query)
    except registry.ArgumentError as exc:
        raise ApiError(400, 'bad_request', _('%(field)s %(problem)s.', field=exc.name, problem=exc.problem))
    ctx = Context(token, op, None, False)
    if op.is_write:
        ctx.user, ctx.acting_given = acting_name(token, cleaned.pop('by', None))
    try:
        payload = op.handler(ctx, cleaned)
        if op.is_write:
            tokens.log_activity(token, ctx.user, op.name, ctx.target or payload.get('summary', ''))
    except ApiError:
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        current_app.logger.exception('API operation %s failed', op.name)
        raise ApiError(500, 'server_error')
    return op.success_status, payload


def read_json_body_raw() -> bytes:
    """The request body as bytes; at most 64 KB."""
    from werkzeug.exceptions import RequestEntityTooLarge
    request.max_content_length = MAX_BODY_BYTES
    try:
        return request.get_data(cache=True)
    except RequestEntityTooLarge:
        raise ApiError(413, 'too_large', _('The request body can be at most 64 KB.'))


def read_json_body() -> dict:
    """The request body as a JSON object (empty when there is no body); at most 64 KB."""
    raw = read_json_body_raw()
    if not raw.strip():
        return {}
    try:
        body = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise ApiError(400, 'bad_request', _('The request body must be JSON.'))
    if not isinstance(body, dict):
        raise ApiError(400, 'bad_request', _('The request body must be a JSON object.'))
    return body
