from flask import render_template, request, redirect, url_for, current_app
from datetime import datetime, timedelta
from ..models import db, ShoppingItem, GroceryHistory
from ..blueprints import main_bp
from ..security import sanitize_text


@main_bp.route('/shopping', methods=['GET', 'POST'])
def shopping():
    if request.method == 'POST':
        item = sanitize_text(request.form['item'])
        creator = sanitize_text(request.form['creator'])
        shopping_item = ShoppingItem(item=item, creator=creator)
        db.session.add(shopping_item)
        db.session.add(GroceryHistory(item=item, creator=creator))
        db.session.commit()
        return redirect(url_for('main.shopping'))
    items = ShoppingItem.query.order_by(ShoppingItem.timestamp.desc()).all()
    cutoff = datetime.utcnow() - timedelta(days=90)
    existing = {i.item.lower() for i in items}
    rows = db.session.execute(db.text("""
        SELECT item, COUNT(*) as cnt
        FROM grocery_history
        WHERE timestamp >= :cutoff
        GROUP BY item
        ORDER BY cnt DESC
        LIMIT 20
    """), {"cutoff": cutoff}).fetchall()
    suggestions = [r[0] for r in rows if r[0].lower() not in existing][:10]
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('shopping.html', items=items, suggestions=suggestions, config=config)


@main_bp.route('/shopping/check/<int:item_id>', methods=['POST'])
def check_shopping(item_id):
    item = ShoppingItem.query.get_or_404(item_id)
    item.checked = not item.checked
    db.session.commit()
    return redirect(url_for('main.shopping'))


@main_bp.route('/shopping/delete/<int:item_id>', methods=['POST'])
def delete_shopping(item_id):
    item = ShoppingItem.query.get_or_404(item_id)
    user = sanitize_text(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == item.creator:
        db.session.delete(item)
        db.session.commit()
    return redirect(url_for('main.shopping'))
