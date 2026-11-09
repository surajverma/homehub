from flask import render_template, request, redirect, url_for, current_app, flash
from flask_babel import gettext as _
from datetime import datetime, date
from ..models import db, ExpiryItem
from ..blueprints import main_bp
from ..admin import can_modify
from ..security import sanitize_text
from ..undo import row_restorer, trash_info, trash_row


def days_left(item: ExpiryItem, today: date):
    """Days until the item expires (negative once past), or None without a date."""
    try:
        return (item.expiry_date - today).days
    except Exception:
        return None


@main_bp.route('/expiry', methods=['GET', 'POST'])
def expiry():
    if request.method == 'POST':
        name = sanitize_text(request.form['name'])
        expiry_date = request.form['expiry_date']
        creator = sanitize_text(request.form['creator'])
        expiry_item = ExpiryItem(name=name, expiry_date=datetime.strptime(expiry_date, '%Y-%m-%d').date(), creator=creator)
        db.session.add(expiry_item)
        db.session.commit()
        flash(_('Item added.'), 'success')
        return redirect(url_for('main.expiry'))
    items = ExpiryItem.query.order_by(ExpiryItem.expiry_date.asc()).all()
    today = date.today()
    # Annotate with days_left for template logic
    enriched = []
    for it in items:
        enriched.append((it, days_left(it, today)))
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('expiry.html', items=enriched, today=today, config=config)


@main_bp.route('/expiry/delete/<int:item_id>', methods=['POST'])
def delete_expiry(item_id):
    it = db.get_or_404(ExpiryItem, item_id)
    user = sanitize_text(request.form['user'])
    if can_modify(user, it.creator):
        trash_expiry_item(it, user, _('%(item)s moved to the trash.', item=it.name))
    else:
        flash(_('Not allowed to delete item.'), 'error')
    return redirect(url_for('main.expiry'))


def trash_expiry_item(item: ExpiryItem, deleted_by: str, message: str | None = None) -> str:
    """Move an expiry tracker item to the trash (restorable for 7 days). Commits."""
    return trash_row('expiry_item', item, trash_info('expiry', item.name, item.creator, deleted_by), message)


row_restorer('expiry_item', ExpiryItem)
