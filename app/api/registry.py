"""The one list of API operations.

Each operation is defined once here (tool, level, input schema, handler). The Flask routes,
the OpenAPI document and the MCP tools are all built from this list, so they cannot drift apart.

Input schemas are flat: every argument is a property of one object, and ``in`` says where a
REST client sends it (``path``, ``query`` or ``body``). MCP clients send them all as tool arguments.
"""
import copy
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

MAX_TAGS = 20
TAG_MAX_LENGTH = 64

BY_PROPERTY = {
    'type': 'string', 'in': 'body', 'maxLength': 64,
    'description': ('The family member this change is made for: the name of the person who asked. '
                    'Only accepted when the token may act as family members. Without it the token '
                    'name is used.'),
}


@dataclass
class Operation:
    name: str                      # MCP tool name and OpenAPI operationId
    tool: str | None               # None: any valid token may call it
    level: str                     # 'read' or 'write'
    method: str
    path: str                      # Flask rule below /api/v1
    title: str
    description: str
    handler: Callable
    properties: dict = field(default_factory=dict)
    required: tuple = ()
    idempotent: bool = False
    destructive: bool = False      # needs Allow deleting
    success_status: int = 200

    @property
    def is_write(self) -> bool:
        return self.level == 'write'

    def all_properties(self) -> dict:
        props = dict(self.properties)
        if self.is_write and 'by' not in props:
            props['by'] = BY_PROPERTY
        return props

    def json_schema(self, locations=None, include_by=True) -> dict:
        """A JSON Schema (2020-12) object for the arguments, optionally only those sent in ``locations``."""
        props = {}
        for name, prop in self.all_properties().items():
            if name == 'by' and not include_by:
                continue
            if locations and prop.get('in', 'body') not in locations:
                continue
            props[name] = public_schema(prop)
        schema = {'type': 'object', 'properties': props, 'additionalProperties': False}
        required = [r for r in self.required if r in props]
        if required:
            schema['required'] = required
        return schema


OPERATIONS: list[Operation] = []
_BY_NAME: dict[str, Operation] = {}


def operation(name, *, tool, level, method, path, title, description, properties=None, required=(),
              idempotent=False, destructive=False, success_status=200):
    """Decorator that adds a handler to the registry."""
    def register(handler):
        if name in _BY_NAME:
            raise ValueError(f'API operation {name} is defined twice')
        op = Operation(name=name, tool=tool, level=level, method=method.upper(), path=path, title=title,
                       description=description, handler=handler, properties=properties or {},
                       required=tuple(required), idempotent=idempotent, destructive=destructive,
                       success_status=success_status)
        OPERATIONS.append(op)
        _BY_NAME[name] = op
        return handler
    return register


def get(name: str) -> Operation | None:
    return _BY_NAME.get(name)


def public_schema(prop: dict) -> dict:
    """A property schema without the keys only HomeHub reads."""
    out = copy.deepcopy(prop)
    for key in ('in', 'x-casefold'):
        out.pop(key, None)
    if out.pop('x-nullable', False):
        out['type'] = [out['type'], 'null']
    return out


# --- Validation ---

class ArgumentError(ValueError):
    """An argument that does not fit the schema; the message names it."""

    def __init__(self, name: str, problem: str):
        super().__init__(f'{name}: {problem}')
        self.name = name
        self.problem = problem


_TIME_RE = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _check(name: str, prop: dict, value, from_query: bool):
    kind = prop.get('type')
    if kind == 'string':
        if not isinstance(value, str):
            raise ArgumentError(name, 'must be text')
        if prop.get('x-casefold'):
            value = value.strip().lower()
        if 'maxLength' in prop and len(value) > prop['maxLength']:
            raise ArgumentError(name, f"can be at most {prop['maxLength']} characters")
        if 'minLength' in prop and len(value.strip()) < prop['minLength']:
            raise ArgumentError(name, 'cannot be empty')
        if 'enum' in prop and value not in prop['enum']:
            raise ArgumentError(name, 'must be one of: ' + ', '.join(prop['enum']))
        fmt = prop.get('format')
        if fmt == 'date':
            if not _DATE_RE.match(value):
                raise ArgumentError(name, 'must be a date as YYYY-MM-DD')
            try:
                return datetime.strptime(value, '%Y-%m-%d').date()
            except ValueError:
                raise ArgumentError(name, 'must be a date as YYYY-MM-DD')
        if fmt == 'time' or prop.get('pattern') == _TIME_RE.pattern:
            if not _TIME_RE.match(value):
                raise ArgumentError(name, 'must be a time as HH:MM (24-hour)')
        return value
    if kind == 'integer':
        if from_query and isinstance(value, str) and re.fullmatch(r'-?\d{1,12}', value):
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ArgumentError(name, 'must be a whole number')
        return value
    if kind == 'number':
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ArgumentError(name, 'must be a number')
        if 'exclusiveMinimum' in prop and value <= prop['exclusiveMinimum']:
            raise ArgumentError(name, f"must be greater than {prop['exclusiveMinimum']:g}")
        if 'maximum' in prop and value > prop['maximum']:
            raise ArgumentError(name, f"can be at most {prop['maximum']:g}")
        return float(value)
    if kind == 'boolean':
        if not isinstance(value, bool):
            raise ArgumentError(name, 'must be true or false')
        return value
    if kind == 'array':
        if not isinstance(value, list):
            raise ArgumentError(name, 'must be a list')
        if len(value) > prop.get('maxItems', MAX_TAGS):
            raise ArgumentError(name, f"can have at most {prop.get('maxItems', MAX_TAGS)} entries")
        return [_check(f'{name}[{i}]', prop.get('items') or {'type': 'string'}, item, False)
                for i, item in enumerate(value)]
    return value


def validate(op: Operation, args: dict, *, from_query=()) -> dict:
    """Check ``args`` against the operation's schema; returns the values to use (dates as date objects)."""
    props = op.all_properties()
    cleaned = {}
    for name, value in args.items():
        prop = props.get(name)
        if prop is None:
            raise ArgumentError(name, 'is not a known field')
        if value is None:
            # null clears a field that allows it (x-nullable); elsewhere it counts as not sent
            if prop.get('x-nullable'):
                cleaned[name] = None
            continue
        cleaned[name] = _check(name, prop, value, name in from_query)
    for name in op.required:
        # A missing "by" is reported by the handler (ctx.require_member), with a message that says what it is for
        if name not in cleaned and name != 'by':
            raise ArgumentError(name, 'is required')
    return cleaned


def tags_property(description='Tags, for example ["Costco", "Dairy"].'):
    return {'type': 'array', 'in': 'body', 'items': {'type': 'string', 'maxLength': TAG_MAX_LENGTH},
            'maxItems': MAX_TAGS, 'description': description}


def iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec='seconds')
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
