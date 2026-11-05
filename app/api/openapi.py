"""OpenAPI 3.1 description of /api/v1, generated from the operation registry.

``python -m app.api.openapi`` rewrites docs/openapi.json (a test checks it is up to date).
"""
import json
import os
import re

from . import registry

TAG_NAMES = {
    None: 'Overview',
    'presence': 'Who is home',
    'statuses': 'Personal status',
    'notice': 'Notice board',
    'reminders': 'Reminders',
    'shopping': 'Shopping list',
    'chores': 'Chores',
    'notes': 'Notes',
    'recipes': 'Recipes',
    'expiry': 'Expiry tracker',
    'expenses': 'Expenses',
}

DOCS_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'docs', 'openapi.json')

_ERROR_RESPONSES = {
    '400': 'The request is not valid (unknown field, wrong type, bad date, ...).',
    '401': 'Missing, wrong or revoked token.',
    '403': 'The token has no access to this tool or level, or may not act as that person.',
    '404': 'Not found, or the tool is switched off in config.yml.',
}


def _openapi_path(rule: str) -> str:
    path = re.sub(r'<(?:[^:<>]+:)?([^<>]+)>', r'{\1}', rule)
    path = path if path.startswith('/') else '/' + path
    return path.rstrip('/') or '/'


def _operation_doc(op) -> dict:
    doc = {
        'operationId': op.name,
        'summary': op.title,
        'description': op.description,
        'tags': [TAG_NAMES.get(op.tool, op.tool)],
        'x-homehub-tool': op.tool,
        'x-homehub-level': op.level,
    }
    if op.destructive:
        doc['x-homehub-needs-allow-delete'] = True
    params = []
    for name, prop in op.all_properties().items():
        where = prop.get('in', 'body')
        if where in ('path', 'query'):
            params.append({
                'name': name,
                'in': where,
                'required': where == 'path' or name in op.required,
                'description': prop.get('description', ''),
                'schema': {k: v for k, v in registry.public_schema(prop).items() if k != 'description'},
            })
    if params:
        doc['parameters'] = params
    body = op.json_schema(locations=('body',))
    if body['properties']:
        doc['requestBody'] = {
            'required': bool(body.get('required')),
            'content': {'application/json': {'schema': body}},
        }
    status = str(op.success_status)
    responses = {status: {
        'description': 'The result as JSON.' + (' Changes include a short "summary" sentence to confirm back.' if op.is_write else ''),
        'content': {'application/json': {'schema': {'type': 'object'}}},
    }}
    for code, text in _ERROR_RESPONSES.items():
        responses[code] = {'description': text, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/Error'}}}}
    if op.destructive or (op.tool == 'expenses' and op.is_write):
        responses['409'] = {'description': 'That cannot be done right now; the message says why.',
                            'content': {'application/json': {'schema': {'$ref': '#/components/schemas/Error'}}}}
    doc['responses'] = responses
    return doc


def build_openapi(operations=None, server_url: str = '/api/v1') -> dict:
    operations = registry.OPERATIONS if operations is None else operations
    paths: dict = {}
    for op in operations:
        paths.setdefault(_openapi_path(op.path), {})[op.method.lower()] = _operation_doc(op)
    paths['/openapi.json'] = {'get': {
        'operationId': 'openapi',
        'summary': 'This document',
        'description': 'The OpenAPI description of the operations this token can use.',
        'tags': ['Overview'],
        'responses': {'200': {'description': 'OpenAPI 3.1 document.', 'content': {'application/json': {'schema': {'type': 'object'}}}},
                      '401': {'description': _ERROR_RESPONSES['401'], 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/Error'}}}}},
    }}
    paths['/mcp'] = {'post': {
        'operationId': 'mcp',
        'summary': 'MCP endpoint',
        'description': ('Model Context Protocol server (Streamable HTTP, JSON responses, no sessions) for AI agents. '
                        'Offers the same operations as MCP tools, with the same token and permissions.'),
        'tags': ['Overview'],
        'requestBody': {'required': True, 'content': {'application/json': {'schema': {'type': 'object'}}}},
        'responses': {'200': {'description': 'JSON-RPC response.', 'content': {'application/json': {'schema': {'type': 'object'}}}},
                      '202': {'description': 'Notification accepted.'},
                      '401': {'description': _ERROR_RESPONSES['401'], 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/Error'}}}}},
    }}
    return {
        'openapi': '3.1.0',
        'info': {
            'title': 'HomeHub API',
            'version': '1',
            'description': ('Token-protected JSON API for HomeHub. Send the token as "Authorization: Bearer <token>". '
                            'Create tokens in HomeHub under Admin tools, API access, or with "flask api-token create". '
                            'See docs/api.md for examples.'),
        },
        'servers': [{'url': server_url}],
        'security': [{'bearerAuth': []}],
        'tags': [{'name': name} for name in TAG_NAMES.values()],
        'paths': paths,
        'components': {
            'securitySchemes': {'bearerAuth': {'type': 'http', 'scheme': 'bearer', 'description': 'A HomeHub API token (hh_...).'}},
            'schemas': {'Error': {
                'type': 'object',
                'properties': {
                    'error': {'type': 'string', 'description': 'Machine-readable code: unauthorized, forbidden, not_found, bad_request, conflict, too_large, method_not_allowed, server_error.'},
                    'message': {'type': 'string', 'description': 'What went wrong, in the household language.'},
                },
                'required': ['error', 'message'],
            }},
        },
    }


def docs_text() -> str:
    return json.dumps(build_openapi(), indent=2, ensure_ascii=False) + '\n'


if __name__ == '__main__':
    from . import load_operations
    load_operations()
    with open(DOCS_PATH, 'w', encoding='utf-8', newline='\n') as f:
        f.write(docs_text())
    print(f'Wrote {os.path.normpath(DOCS_PATH)}')
