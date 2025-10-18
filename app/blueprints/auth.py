from flask import current_app, request, session, redirect, url_for, render_template, flash
from ..blueprints import main_bp
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


@main_bp.route('/logout')
def logout():
    session.pop('authed', None)
    flash('Logged out.', 'info')
    return redirect(url_for('main.login'))
