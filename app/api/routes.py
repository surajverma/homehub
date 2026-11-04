"""Flask routes for /api/v1, built from the operation registry."""
from flask import Blueprint, request
from werkzeug.exceptions import HTTPException

from . import core, registry, tokens
from .core import ApiError, error_response, json_response

api_bp = Blueprint('api_v1', __name__, url_prefix=core.API_PREFIX)


@api_bp.after_request
def _no_store(response):
    return core.no_store(response)


def visible_operations(token, config=None) -> list:
    """Operations this token may call now (switched-off features left out), in registry order."""
    allowed = tokens.effective_permissions(token, config)
    out = []
    for op in registry.OPERATIONS:
        if op.tool is None:
            out.append(op)
        elif tokens.level_allows(allowed.get(op.tool), op.level):
            if op.destructive and not token.allow_delete:
                continue
            out.append(op)
    return out


def overview_payload(token) -> dict:
    return {
        'token': token.name,
        'act_as_members': bool(token.act_as_members),
        'allow_delete': bool(token.allow_delete),
        'tools': tokens.effective_permissions(token),
        'family_members': [m for m in tokens.family_members() if not tokens.is_admin_name(m)],
        'version': core.API_VERSION,
    }


@registry.operation(
    'homehub_overview', tool=None, level='read', method='GET', path='/',
    title='Overview',
    description=('What this token can do: its name, whether it may act as family members or delete, the tools '
                 'it can use with their level (read or write), and the family members it can act as. '
                 'Call this first.'),
    idempotent=True)
def overview(ctx, _args):
    return overview_payload(ctx.token)


def _make_view(op):
    path_names = {n for n, p in op.properties.items() if p.get('in') == 'path'}
    query_names = {n for n, p in op.properties.items() if p.get('in') == 'query'}

    def view(**path_args):
        try:
            token = core.current_token()
            core.check_access(token, op.tool, op.level)
            args = dict(path_args)
            for key in request.args:
                values = request.args.getlist(key)
                if key not in query_names or key in args:
                    raise ApiError(400, 'bad_request', _unknown_field(key))
                if len(values) > 1:
                    raise ApiError(400, 'bad_request', _unknown_field(key))
                args[key] = values[0]
            if op.method != 'GET':
                body = core.read_json_body()
                for key, value in body.items():
                    if key in path_names or key in query_names or key in args:
                        raise ApiError(400, 'bad_request', _unknown_field(key))
                    args[key] = value
            status, payload = core.run_operation(op, token, args, from_query=query_names)
        except ApiError as exc:
            return error_response(exc.status, exc.code, exc.message)
        return json_response(payload, status)

    view.__name__ = op.name
    return view


def _unknown_field(key: str) -> str:
    from flask_babel import gettext as _
    return _('%(field)s is not a known field here.', field=key)


@api_bp.route('/openapi.json', methods=['GET'])
@core.require_api()
def openapi_json():
    from .openapi import build_openapi
    token = core.current_token()
    server = request.host_url.rstrip('/') + core.API_PREFIX
    return json_response(build_openapi(visible_operations(token), server_url=server))


_registered = []


def register_operations():
    """Add a route for every operation in the registry (once per process, before the blueprint is first registered)."""
    if _registered:
        return
    for op in registry.OPERATIONS:
        api_bp.add_url_rule(op.path, endpoint=op.name, view_func=_make_view(op), methods=[op.method])
    _registered.append(True)


def handle_http_error(exc: HTTPException):
    """JSON errors for /api/v1 (unknown paths, wrong methods); every other page keeps Flask's own."""
    if not core.is_api_path(request.path):
        return exc
    if core.current_token() is None:
        return error_response(401, 'unauthorized')
    code = {404: 'not_found', 405: 'method_not_allowed', 413: 'too_large'}.get(exc.code, 'bad_request')
    return error_response(exc.code or 400, code)
