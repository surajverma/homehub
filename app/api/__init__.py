"""HomeHub's token-protected JSON API (/api/v1), its MCP endpoint and the ``flask api-token`` command.

The API is off until a token exists: without an active token every request answers 401.
"""
from flask import request

_loaded = []


def load_operations():
    """Import the modules that define operations, so the registry is complete."""
    if _loaded:
        return
    from . import routes  # noqa: F401 (defines the overview operation)
    from . import home  # noqa: F401
    from . import household  # noqa: F401
    from . import weather  # noqa: F401
    from . import expenses  # noqa: F401
    from . import mcp  # noqa: F401
    _loaded.append(True)


def token_opens_gate() -> bool:
    """For the site-password gate: True only for an /api/v1 request that carries a valid token.

    A token never opens any other page, nor the web's own /api/... routes: those stay behind
    the site password exactly as before.
    """
    from . import core
    if not core.is_api_path(request.path):
        return False
    endpoint = request.endpoint or ''
    if endpoint and not endpoint.startswith('api_v1.'):
        return False
    return core.current_token() is not None


def is_api_request() -> bool:
    from .core import is_api_path
    return is_api_path(request.path)


def unauthorized_response():
    """The API's 401 (JSON, WWW-Authenticate, no-store), also used by the site-password gate."""
    from .core import error_response
    return error_response(401, 'unauthorized')


def init_app(app):
    load_operations()
    from .routes import api_bp, handle_http_error, register_operations
    register_operations()
    app.register_blueprint(api_bp)
    app.register_error_handler(404, handle_http_error)
    app.register_error_handler(405, handle_http_error)
    from .cli import api_token_cli
    app.cli.add_command(api_token_cli)
