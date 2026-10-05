from flask import Flask, session
from flask_sqlalchemy import SQLAlchemy
from .config import load_config, upload_limit_bytes
import logging
import os
import secrets
import click
from datetime import datetime, timezone

db = SQLAlchemy()


def _load_or_create_secret_key(data_dir: str) -> str:
    path = os.path.join(data_dir, 'secret_key')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            existing = f.read().strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass
    except OSError:
        logging.getLogger(__name__).warning('Could not read %s; using a temporary secret key', path)
        return secrets.token_hex(32)
    secret = secrets.token_hex(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(secret)
    except OSError:
        logging.getLogger(__name__).warning('Could not write %s; sessions will reset on restart', path)
    return secret


def create_app(test_config: dict | None = None):
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    templates_dir = os.path.join(base_dir, 'templates')
    static_dir = os.path.join(base_dir, 'static')

    app = Flask(
        __name__,
        template_folder=templates_dir,
        static_folder=static_dir,
    )

    # Paths
    data_dir = os.path.join(base_dir, 'data')
    uploads_dir = os.path.join(base_dir, 'uploads')
    media_dir = os.path.join(base_dir, 'media')
    pdfs_dir = os.path.join(base_dir, 'pdfs')
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(uploads_dir, exist_ok=True)
    os.makedirs(media_dir, exist_ok=True)
    os.makedirs(pdfs_dir, exist_ok=True)

    # SQLite DB file at an absolute path to avoid driver path issues
    db_path = os.path.join(base_dir, 'data', 'app.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    # SECRET_KEY from env, else one generated once and kept in data/ so restarts don't log everyone out
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or _load_or_create_secret_key(data_dir)
    # Explicitly disable CSRF (forms are simple and app runs on home network)
    app.config['WTF_CSRF_ENABLED'] = False

    # Load config.yml
    app.config['HOMEHUB_CONFIG'] = load_config()
    app.config['MAX_CONTENT_LENGTH'] = upload_limit_bytes(app.config['HOMEHUB_CONFIG'])

    # Allow tests to override configuration (database, testing flag, etc.)
    if test_config:
        app.config.update(test_config)

    db.init_app(app)

    # Ensure models are imported before creating tables
    with app.app_context():
        from . import models  # noqa: F401 ensures model metadata is registered
        db.create_all()
        # Upgrade older SQLite files in place (adds missing columns and tables).
        # Skipped in testing so tests never touch the real database file.
        if not app.config.get('TESTING'):
            from . import migrations
            migrations.run(db_path)

    from .blueprints import main_bp
    # Register modular route modules to attach endpoints to main_bp
    from .blueprints import auth  # noqa: F401
    from .blueprints import dashboard  # noqa: F401
    from .blueprints import notes  # noqa: F401
    from .blueprints import uploads  # noqa: F401
    from .blueprints import shortener  # noqa: F401
    from .blueprints import shopping  # noqa: F401
    from .blueprints import recipes  # noqa: F401
    from .blueprints import expiry  # noqa: F401
    from .blueprints import media_pdfs  # noqa: F401
    from .blueprints import expenses  # noqa: F401
    from .blueprints import chores  # noqa: F401
    from .blueprints import qr  # noqa: F401
    from .blueprints import weather  # noqa: F401
    app.register_blueprint(main_bp)

    @app.errorhandler(413)
    def upload_too_large(_err):
        from flask import flash, jsonify, redirect, request
        limit_mb = (app.config.get('MAX_CONTENT_LENGTH') or 0) // (1024 * 1024)
        message = f'Upload is too large. The limit is {limit_mb} MB.'
        if request.is_json or request.path.startswith('/api/'):
            return jsonify({'ok': False, 'error': message}), 413
        flash(message, 'error')
        # Only go back to a page on this site; an outside Referer would make this an open redirect
        from urllib.parse import urlsplit
        ref = urlsplit(request.referrer or '')
        if ref.netloc and ref.netloc != request.host:
            return redirect('/')
        back = (ref.path or '/') + (f'?{ref.query}' if ref.query else '')
        return redirect(back if back.startswith('/') and not back.startswith('//') else '/')

    @app.context_processor
    def inject_auth_state():
        from . import admin as _admin
        return {
            'is_authed': bool(session.get('authed')),
            'admin_password_enabled': _admin.admin_password_enabled(),
            'admin_unlocked': _admin.admin_unlocked(),
        }

    @app.cli.command('set-admin-password')
    @click.option('--clear', is_flag=True, help='Remove the admin password so admin is open to everyone again.')
    def set_admin_password_command(clear):
        """Set or reset the password required to act as admin."""
        from . import admin as _admin
        if clear:
            _admin.clear_admin_password()
            click.echo('Admin password removed. Anyone can switch to the admin user again.')
            return
        password = click.prompt('New admin password', hide_input=True, confirmation_prompt=True)
        if len(password) < _admin.ADMIN_PASSWORD_MIN_LENGTH:
            raise click.ClickException(f'Password must be at least {_admin.ADMIN_PASSWORD_MIN_LENGTH} characters.')
        _admin.set_admin_password(password)
        click.echo('Admin password saved. Switching to the admin user now asks for it.')
    
    # Static URLs carry the file's mtime so the service worker and browsers fetch fresh copies after an update
    @app.template_global('asset_url')
    def asset_url(filename):
        from flask import url_for
        try:
            version = int(os.path.getmtime(os.path.join(app.static_folder, filename)))
        except OSError:
            return url_for('static', filename=filename)
        return url_for('static', filename=filename, v=version)

    # Timestamps are stored as naive UTC; show them in the server's local time (set with TZ)
    @app.template_filter('localtime')
    def localtime_filter(value, fmt='%Y-%m-%d %H:%M'):
        if not isinstance(value, datetime):
            return value
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone().strftime(fmt)

    # Add Jinja2 filter for JSON parsing
    @app.template_filter('from_json')
    def from_json_filter(s):
        import json
        try:
            return json.loads(s) if s else []
        except (ValueError, TypeError):
            return []

    return app
