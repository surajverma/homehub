from flask import render_template, request, redirect, url_for, current_app, flash
from ..models import db, ShortURL
from ..utils import generate_short_code
from ..blueprints import main_bp
from ..security import sanitize_text, is_http_url


@main_bp.route('/shorten', methods=['GET', 'POST'])
def shorten():
    if request.method == 'POST':
        original_url = sanitize_text(request.form['original_url'])
        creator = sanitize_text(request.form['creator'])
        if not is_http_url(original_url):
            flash('Please enter a valid http(s) URL.', 'error')
            return redirect(url_for('main.shorten'))
        short_code = generate_short_code()
        while ShortURL.query.filter_by(short_code=short_code).first():
            short_code = generate_short_code()
        short_url = ShortURL(original_url=original_url, short_code=short_code, creator=creator)
        db.session.add(short_url)
        db.session.commit()
        return redirect(url_for('main.shorten'))
    urls = ShortURL.query.order_by(ShortURL.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('shorten.html', urls=urls, config=config)


@main_bp.route('/s/<short_code>')
def redirect_short(short_code):
    short_url = ShortURL.query.filter_by(short_code=short_code).first_or_404()
    target = short_url.original_url or ''
    if not is_http_url(target):
        flash('Invalid target URL.', 'error')
        return redirect(url_for('main.shorten'))
    return redirect(target)


@main_bp.route('/shorten/delete/<int:url_id>', methods=['POST'])
def delete_short(url_id):
    su = ShortURL.query.get_or_404(url_id)
    user = sanitize_text(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == su.creator:
        db.session.delete(su)
        db.session.commit()
    return redirect(url_for('main.shorten'))
