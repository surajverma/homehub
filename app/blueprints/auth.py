from flask import current_app, request, session, redirect, url_for, render_template, flash, jsonify
from ..blueprints import main_bp
from .. import admin
from ..config import load_config, upload_limit_bytes
from ..models import db
from werkzeug.security import check_password_hash
from flask_babel import gettext as _, ngettext

# Endpoints that answer without the site password
_OPEN_ENDPOINTS = ('main.login', 'main.healthz')


@main_bp.before_app_request
def reload_config_and_auth():
    try:
        current_app.config['HOMEHUB_CONFIG'] = load_config()
        current_app.config['MAX_CONTENT_LENGTH'] = upload_limit_bytes(current_app.config['HOMEHUB_CONFIG'])
    except Exception:
        pass
    cfg = current_app.config.get('HOMEHUB_CONFIG', {})
    endpoint = request.endpoint or ''
    if cfg.get('password_hash'):
        if not session.get('authed') and not endpoint.startswith('static') and endpoint not in _OPEN_ENDPOINTS:
            return redirect(url_for('main.login'))
    else:
        if endpoint == 'main.login':
            return redirect(url_for('main.index'))


@main_bp.route('/login', methods=['GET', 'POST'])
def login():
    config = current_app.config['HOMEHUB_CONFIG']
    if not config.get('password_hash'):
        return redirect(url_for('main.index'))
    # Same lockout as the admin password, counted separately for the site login
    client = 'login:' + (request.remote_addr or 'unknown')
    if request.method == 'POST':
        if admin.unlock_wait_seconds(client):
            # Still locked: the form below says so and counts down
            pass
        elif check_password_hash(config.get('password_hash'), request.form.get('password', '')):
            admin.clear_failed_attempts(client)
            session['authed'] = True
            flash(_('Logged in successfully.'), 'success')
            return redirect(url_for('main.index'))
        else:
            admin.record_failed_attempt(client)
            flash(_('Invalid password'), 'error')
    # Also on a plain reload, so the form stays disabled for as long as the server refuses attempts
    return render_template('login.html', config=config, hide_user_ui=True,
                           lockout_seconds=admin.unlock_wait_seconds(client))


@main_bp.route('/healthz')
def healthz():
    """For Docker's HEALTHCHECK: the app is up and can reach its database."""
    try:
        db.session.execute(db.text('SELECT 1'))
    except Exception:
        current_app.logger.exception('Health check could not query the database')
        return jsonify({'ok': False}), 503
    return jsonify({'ok': True})


@main_bp.route('/admin/unlock', methods=['POST'])
def admin_unlock():
    if not admin.admin_password_enabled():
        return jsonify({'ok': True, 'password_enabled': False})
    client = request.remote_addr or 'unknown'
    wait = admin.unlock_wait_seconds(client)
    if wait:
        return jsonify({'ok': False, 'error': ngettext('Too many attempts. Try again in %(num)s second.', 'Too many attempts. Try again in %(num)s seconds.', wait)}), 429
    payload = request.get_json(silent=True) or {}
    if admin.try_unlock(str(payload.get('password', '')), client):
        return jsonify({'ok': True, 'password_enabled': True})
    return jsonify({'ok': False, 'error': _('Incorrect admin password')}), 403


@main_bp.route('/admin/lock', methods=['POST'])
def admin_lock():
    admin.lock()
    return jsonify({'ok': True})


@main_bp.route('/logout')
def logout():
    admin.lock()
    session.pop('authed', None)
    flash(_('Logged out.'), 'info')
    return redirect(url_for('main.login'))
