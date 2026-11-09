"""``flask api-token``: create, list, regenerate and revoke API tokens from the server.

Works whether or not an admin password is set. In Docker: docker exec -it homehub flask api-token ...
"""
import click
from flask.cli import AppGroup

from . import tokens

api_token_cli = AppGroup('api-token', help='Manage tokens for the HomeHub API (/api/v1).')


def _fresh_config():
    """Read config.yml now, as every web request does, so the name rules see the current family."""
    from flask import current_app
    from ..config import load_config
    current_app.config['HOMEHUB_CONFIG'] = load_config()


def _parse_tools(values) -> dict:
    permissions = {}
    for value in values:
        key, sep, level = value.partition('=')
        if not sep:
            raise click.BadParameter(f'{value!r}: write it as TOOL=LEVEL, for example presence=write.', param_hint='--tool')
        permissions[key.strip().lower()] = level.strip().lower()
    return permissions


def _summary(token) -> str:
    perms = token.permission_map
    parts = [f'{t.key}={perms[t.key]}' for t in tokens.TOOLS if t.key in perms]
    return ', '.join(parts) or 'no tools'


def _print_token(secret: str):
    click.echo('')
    click.echo('Copy this token now. HomeHub keeps only a fingerprint of it and cannot show it again:')
    click.echo('')
    click.echo(f'  {secret}')
    click.echo('')
    click.echo('Use it as the header "Authorization: Bearer <token>". See docs/api.md for examples.')


@api_token_cli.command('create')
@click.argument('name')
@click.option('--tool', 'tool_values', multiple=True, metavar='TOOL=LEVEL',
              help='Give access to a tool: read or write. Repeat for each tool. Tools: '
                   + ', '.join(t.key for t in tokens.TOOLS) + '.')
@click.option('--act-as-members', is_flag=True, help='Let the token act as family members (the "by" field). Never the admin.')
@click.option('--allow-delete', is_flag=True, help='Let the token move reminders to the trash (needs reminders=write).')
def create_command(name, tool_values, act_as_members, allow_delete):
    """Create a token called NAME and print it once."""
    _fresh_config()
    try:
        permissions = _parse_tools(tool_values)
        if allow_delete and not tokens.can_delete(tokens.clean_permissions(permissions, act_as_members)):
            raise tokens.TokenError('--allow-delete needs --tool reminders=write: it only moves reminders to the trash.')
        token, secret = tokens.create_token(name, permissions, act_as_members, allow_delete)
    except tokens.TokenError as exc:
        raise click.ClickException(str(exc))
    click.echo(f'Token {token.name} created: {_summary(token)}'
               + ('; acts as family members' if token.act_as_members else '')
               + ('; can move reminders to the trash' if token.allow_delete else '') + '.')
    disabled = [k for k in token.permission_map if not tokens.tool_enabled(k)]
    if disabled:
        click.echo('Switched off in config.yml, so unreachable until turned on: ' + ', '.join(disabled) + '.')
    _print_token(secret)


@api_token_cli.command('list')
def list_command():
    """List tokens (the tokens themselves cannot be shown)."""
    _fresh_config()
    rows = tokens.all_tokens()
    if not rows:
        click.echo('No API tokens. The API is off until you create one.')
        return
    for token in rows:
        state = f'revoked {token.revoked_at:%Y-%m-%d %H:%M}' if token.revoked_at else 'active'
        used = f'{token.last_used_at:%Y-%m-%d %H:%M} UTC' if token.last_used_at else 'not used yet'
        flags = [f for f, on in (('act as family members', token.act_as_members), ('allow deleting', token.allow_delete)) if on]
        if tokens.name_taken_by_person(token.name):
            flags.append('same name as a person: writes blocked until renamed')
        click.echo(f'{token.name} ({token.prefix}…, {state}): {_summary(token)}'
                   + (f' [{", ".join(flags)}]' if flags else '') + f'; last used: {used}')


@api_token_cli.command('regenerate')
@click.argument('name')
def regenerate_command(name):
    """Give token NAME a new value; whatever uses the old one stops working. Also re-activates a revoked token."""
    _fresh_config()
    token = tokens.find_by_name(name)
    if token is None:
        raise click.ClickException(f'No token called {name}.')
    secret = tokens.regenerate_token(token)
    click.echo(f'Token {token.name} regenerated. The old token no longer works.')
    _print_token(secret)


@api_token_cli.command('revoke')
@click.argument('name')
def revoke_command(name):
    """Revoke token NAME; whatever uses it stops working at once."""
    _fresh_config()
    token = tokens.find_by_name(name)
    if token is None:
        raise click.ClickException(f'No token called {name}.')
    if token.revoked_at:
        click.echo(f'Token {token.name} was already revoked.')
        return
    tokens.revoke_token(token)
    click.echo(f'Token {token.name} revoked.')


@api_token_cli.command('rename')
@click.argument('old')
@click.argument('new')
def rename_command(old, new):
    """Rename token OLD to NEW; the token itself and its access stay the same."""
    _fresh_config()
    token = tokens.find_by_name(old)
    if token is None:
        raise click.ClickException(f'No token called {old}.')
    previous = token.name
    try:
        name = tokens.rename_token(token, new)
    except tokens.TokenError as exc:
        raise click.ClickException(str(exc))
    click.echo(f'Token {previous} renamed to {name}.')


@api_token_cli.command('delete')
@click.argument('name')
@click.option('--yes', is_flag=True, help='Do not ask for confirmation.')
def delete_command(name, yes):
    """Delete revoked token NAME for good, with its activity history. Revoke it first."""
    _fresh_config()
    token = tokens.find_by_name(name)
    if token is None:
        raise click.ClickException(f'No token called {name}.')
    if not token.revoked_at:
        raise click.ClickException(f'Token {token.name} is still active. Revoke it first: flask api-token revoke "{token.name}"')
    if not yes:
        click.confirm(f'Delete {token.name}? Its activity history is deleted too.', abort=True)
    deleted = token.name
    tokens.delete_token(token)
    click.echo(f'Token {deleted} deleted.')
