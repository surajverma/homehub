from flask import render_template, request, redirect, url_for, current_app, jsonify, flash
from flask_babel import gettext as _
from datetime import timedelta
from ..models import db, ShoppingItem, GroceryHistory
from ..blueprints import main_bp, json_error_response
from ..clock import utcnow
from ..admin import can_modify
from ..security import sanitize_text, sanitize_tags
from ..undo import row_restorer, trash_info, trash_row
import json


def add_shopping_item(item: str, creator: str, tags: list) -> ShoppingItem:
    """Add an item (already sanitised) and remember it for suggestions; commits."""
    shopping_item = ShoppingItem(item=item, creator=creator, tags=json.dumps(tags))
    db.session.add(shopping_item)
    db.session.add(GroceryHistory(item=item, creator=creator))
    db.session.commit()
    return shopping_item


def edited_tags(raw_tags: list) -> list:
    """Tags sent with an edit, as the edit routes store them: text entries, each sanitised."""
    return [sanitize_text(t) for t in raw_tags if isinstance(t, str)]


def edit_shopping_item(item: ShoppingItem, new_item, raw_tags) -> list:
    """Change an item's text (when given as text) and, unless raw_tags is None, replace its tags; commits.

    Returns the item's tags as stored.
    """
    if isinstance(new_item, str):
        item.item = sanitize_text(new_item)
    if raw_tags is not None:
        item.tags = json.dumps(edited_tags(raw_tags))
    db.session.commit()
    return item_tags(item.tags)


def item_tags(raw) -> list:
    try:
        tags = json.loads(raw or '[]')
    except Exception:
        tags = []
    return tags


def shopping_item_dict(i: ShoppingItem) -> dict:
    return {"id": i.id, "item": i.item, "checked": i.checked, "creator": i.creator, "timestamp": i.timestamp.isoformat(), "tags": item_tags(i.tags)}


@main_bp.route('/shopping', methods=['GET', 'POST'])
def shopping():
    if request.method == 'POST':
        item = sanitize_text(request.form['item'])
        creator = sanitize_text(request.form['creator'])
        raw_tags = request.form.get('tags', '').strip()
        tags_list = []
        if raw_tags:
            try:
                # Expect JSON array from enhanced UI
                tags_list = json.loads(raw_tags)
                if not isinstance(tags_list, list):
                    tags_list = []
            except Exception:
                # Fallback: comma separated
                tags_list = [t.strip() for t in raw_tags.split(',') if t.strip()]
        # sanitize each tag
        tags_list = sanitize_tags(tags_list)
        add_shopping_item(item, creator, tags_list)
        flash(_('Item added.'), 'success')
        return redirect(url_for('main.shopping'))
    # Filtering by tags (if provided)
    filter_tags = request.args.get('tags')
    q = ShoppingItem.query
    items = q.order_by(ShoppingItem.checked.asc(), ShoppingItem.timestamp.desc()).all()
    if filter_tags:
        try:
            selected = json.loads(filter_tags)
            if isinstance(selected, list) and selected:
                def match(item_tags):
                    try:
                        arr = json.loads(item_tags or '[]')
                    except Exception:
                        arr = []
                    return any(t in arr for t in selected)
                items = [i for i in items if match(i.tags)]
        except Exception:
            pass
    cutoff = utcnow() - timedelta(days=90)
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
    item = db.get_or_404(ShoppingItem, item_id)
    item.checked = not item.checked
    db.session.commit()
    return redirect(url_for('main.shopping'))


@main_bp.route('/shopping/delete/<int:item_id>', methods=['POST'])
def delete_shopping(item_id):
    item = db.get_or_404(ShoppingItem, item_id)
    user = sanitize_text(request.form['user'])
    if can_modify(user, item.creator):
        trash_shopping_item(item, user, _('%(item)s moved to the trash.', item=item.item))
    else:
        flash(_('Not allowed to delete item.'), 'error')
    return redirect(url_for('main.shopping'))


def trash_shopping_item(item: ShoppingItem, deleted_by: str, message: str | None = None) -> str:
    """Move an item to the trash (restorable for 7 days). Commits."""
    return trash_row('shopping_item', item, trash_info('shopping', item.item, item.creator, deleted_by), message)


row_restorer('shopping_item', ShoppingItem)


@main_bp.route('/api/shopping/<int:item_id>/tags', methods=['POST'])
def update_shopping_tags(item_id):
    item = db.get_or_404(ShoppingItem, item_id)
    try:
        data = request.get_json(force=True) or {}
        user = sanitize_text(str(data.get('user', '')))
        if not can_modify(user, item.creator or ''):
            return jsonify({"ok": False, "error": "not allowed"}), 403
        tags = data.get('tags', [])
        if not isinstance(tags, list):
            tags = []
        # sanitize each tag (keep simple strings)
        cleaned = []
        for t in tags:
            if isinstance(t, str):
                cleaned.append(sanitize_text(t))
        item.tags = json.dumps(cleaned)
        db.session.commit()
        return jsonify({"ok": True, "id": item.id, "tags": cleaned})
    except Exception:
        return json_error_response('Could not update tags for shopping item %s', item_id)


@main_bp.route('/api/shopping', methods=['GET'])
def api_get_shopping():
    tags = request.args.get('tags')
    items = ShoppingItem.query.order_by(ShoppingItem.checked.asc(), ShoppingItem.timestamp.desc()).all()
    if tags:
        try:
            sel = json.loads(tags)
            if isinstance(sel, list) and sel:
                def match(item_tags):
                    try:
                        arr = json.loads(item_tags or '[]')
                    except Exception:
                        arr = []
                    return any(t in arr for t in sel)
                items = [i for i in items if match(i.tags)]
        except Exception:
            pass
    return jsonify([shopping_item_dict(i) for i in items])


@main_bp.route('/api/shopping/<int:item_id>', methods=['PUT'])
def api_update_shopping(item_id):
    item = db.get_or_404(ShoppingItem, item_id)
    try:
        data = request.get_json(force=True) or {}
        user = sanitize_text(str(data.get('user', '')))
        if not can_modify(user, item.creator or ''):
            return jsonify({"ok": False, "error": "not allowed"}), 403
        new_item = data.get('item')
        raw_tags = data.get('tags', [])
        tags = edit_shopping_item(item, new_item, raw_tags if isinstance(raw_tags, list) else [])
        return jsonify({"ok": True, "item": {"id": item.id, "item": item.item, "tags": tags}})
    except Exception:
        return json_error_response('Could not update shopping item %s', item_id)


@main_bp.route('/api/shopping/history', methods=['DELETE'])
def api_delete_shopping_history_item():
    try:
        data = request.get_json(force=True) or {}
        it = sanitize_text(str(data.get('item', '')).strip())
        if not it:
            return jsonify({"ok": False, "error": "missing item"}), 400
        # Delete one most recent matching history row (best effort)
        row = db.session.execute(db.text("""
            SELECT id FROM grocery_history WHERE item = :item ORDER BY timestamp DESC LIMIT 1
        """), {"item": it}).fetchone()
        if row:
            db.session.execute(db.text("DELETE FROM grocery_history WHERE id = :id"), {"id": row[0]})
            db.session.commit()
        return jsonify({"ok": True})
    except Exception:
        return json_error_response('Could not delete shopping history item')
