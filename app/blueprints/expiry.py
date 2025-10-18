from flask import render_template, request, redirect, url_for, current_app
from datetime import datetime, date
from ..models import db, ExpiryItem
from ..blueprints import main_bp
from ..security import sanitize_text


@main_bp.route('/expiry', methods=['GET', 'POST'])
def expiry():
    if request.method == 'POST':
        name = sanitize_text(request.form['name'])
        expiry_date = request.form['expiry_date']
        creator = sanitize_text(request.form['creator'])
        expiry_item = ExpiryItem(name=name, expiry_date=datetime.strptime(expiry_date, '%Y-%m-%d').date(), creator=creator)
        db.session.add(expiry_item)
        db.session.commit()
        return redirect(url_for('main.expiry'))
    items = ExpiryItem.query.order_by(ExpiryItem.expiry_date.asc()).all()
    today = date.today()
    # Annotate with days_left for template logic
    enriched = []
    for it in items:
        try:
            days_left = (it.expiry_date - today).days
        except Exception:
            days_left = None
        enriched.append((it, days_left))
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('expiry.html', items=enriched, today=today, config=config)


@main_bp.route('/expiry/delete/<int:item_id>', methods=['POST'])
def delete_expiry(item_id):
    it = ExpiryItem.query.get_or_404(item_id)
    user = sanitize_text(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == it.creator:
        db.session.delete(it)
        db.session.commit()
    return redirect(url_for('main.expiry'))
