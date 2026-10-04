from flask import current_app, request, session, redirect, url_for, render_template, flash, jsonify
from ..blueprints import main_bp
from .. import admin
from ..config import load_config
import hashlib
import bleach


@main_bp.before_app_request
def reload_config_and_auth():
    try:
        current_app.config['HOMEHUB_CONFIG'] = load_config()
    except Exception:
        pass
    cfg = current_app.config.get('HOMEHUB_CONFIG', {})
    endpoint = request.endpoint or ''
    if cfg.get('password_hash'):
        if not session.get('authed') and not endpoint.startswith('static') and endpoint not in ('main.login',):
            return redirect(url_for('main.login'))
    else:
        if endpoint == 'main.login':
            return redirect(url_for('main.index'))


@main_bp.route('/login', methods=['GET', 'POST'])
def login():
    config = current_app.config['HOMEHUB_CONFIG']
    if not config.get('password_hash'):
        return redirect(url_for('main.index'))
    if request.method == 'POST':
        supplied = bleach.clean(request.form.get('password', ''))
        if hashlib.sha256(supplied.encode()).hexdigest() == config.get('password_hash'):
            session['authed'] = True
            flash('Logged in successfully.', 'success')
            return redirect(url_for('main.index'))
        flash('Invalid password', 'error')
    return render_template('login.html', config=config, hide_user_ui=True)


@main_bp.route('/admin/unlock', methods=['POST'])
def admin_unlock():
    if not admin.admin_pin_enabled():
        return jsonify({'ok': True, 'pin_enabled': False})
    client = request.remote_addr or 'unknown'
    wait = admin.unlock_wait_seconds(client)
    if wait:
        return jsonify({'ok': False, 'error': f'Too many attempts. Try again in {wait} seconds.'}), 429
    payload = request.get_json(silent=True) or {}
    if admin.try_unlock(str(payload.get('pin', '')), client):
        return jsonify({'ok': True, 'pin_enabled': True})
    return jsonify({'ok': False, 'error': 'Incorrect admin PIN'}), 403


@main_bp.route('/admin/lock', methods=['POST'])
def admin_lock():
    admin.lock()
    return jsonify({'ok': True})


@main_bp.route('/logout')
def logout():
    admin.lock()
    session.pop('authed', None)
    flash('Logged out.', 'info')
    return redirect(url_for('main.login'))
