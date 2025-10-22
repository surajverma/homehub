from flask import render_template, request, redirect, url_for, current_app, jsonify
from ..models import db, Chore
from ..blueprints import main_bp
from ..security import sanitize_text
import json


@main_bp.route('/chores', methods=['GET', 'POST'])
def chores():
    if request.method == 'POST':
        description = sanitize_text(request.form['description'])
        creator = sanitize_text(request.form['creator'])
        raw_tags = request.form.get('tags', '').strip()
        tags_list = []
        if raw_tags:
            try:
                tags_list = json.loads(raw_tags)
                if not isinstance(tags_list, list):
                    tags_list = []
            except Exception:
                tags_list = [t.strip() for t in raw_tags.split(',') if t.strip()]
        tags_list = [sanitize_text(t) for t in tags_list if isinstance(t, str) and t.strip()]
        chore = Chore(description=description, creator=creator, tags=json.dumps(tags_list))
        db.session.add(chore)
        db.session.commit()
        return redirect(url_for('main.chores'))
    # Filter and sort (unchecked first)
    filter_tags = request.args.get('tags')
    chores = Chore.query.order_by(Chore.done.asc(), Chore.timestamp.desc()).all()
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
                chores = [i for i in chores if match(i.tags)]
        except Exception:
            pass
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('chores.html', chores=chores, config=config)


@main_bp.route('/chores/toggle/<int:chore_id>', methods=['POST'])
def toggle_chore(chore_id):
    chore = Chore.query.get_or_404(chore_id)
    chore.done = not getattr(chore, 'done', False)
    db.session.commit()
    return redirect(url_for('main.chores'))


@main_bp.route('/chores/delete/<int:chore_id>', methods=['POST'])
def delete_chore(chore_id):
    chore = Chore.query.get_or_404(chore_id)
    user = sanitize_text(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == chore.creator:
        db.session.delete(chore)
        db.session.commit()
    return redirect(url_for('main.chores'))


@main_bp.route('/api/chores/<int:chore_id>/tags', methods=['POST'])
def update_chore_tags(chore_id):
    chore = Chore.query.get_or_404(chore_id)
    try:
        data = request.get_json(force=True) or {}
        tags = data.get('tags', [])
        if not isinstance(tags, list):
            tags = []
        cleaned = []
        for t in tags:
            if isinstance(t, str):
                cleaned.append(sanitize_text(t))
        chore.tags = json.dumps(cleaned)
        db.session.commit()
        return jsonify({"ok": True, "id": chore.id, "tags": cleaned})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@main_bp.route('/api/chores', methods=['GET'])
def api_get_chores():
    tags = request.args.get('tags')
    items = Chore.query.order_by(Chore.done.asc(), Chore.timestamp.desc()).all()
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
    def to_dict(i):
        try:
            tg = json.loads(i.tags or '[]')
        except Exception:
            tg = []
        return {"id": i.id, "description": i.description, "done": i.done, "creator": i.creator, "timestamp": i.timestamp.isoformat(), "tags": tg}
    return jsonify([to_dict(i) for i in items])


@main_bp.route('/api/chores/<int:chore_id>', methods=['PUT'])
def api_update_chore(chore_id):
    chore = Chore.query.get_or_404(chore_id)
    try:
        data = request.get_json(force=True) or {}
        desc = data.get('description')
        raw_tags = data.get('tags', [])
        if isinstance(desc, str):
            chore.description = sanitize_text(desc)
        tags = []
        if isinstance(raw_tags, list):
            for t in raw_tags:
                if isinstance(t, str):
                    tags.append(sanitize_text(t))
        chore.tags = json.dumps(tags)
        db.session.commit()
        return jsonify({"ok": True, "item": {"id": chore.id, "description": chore.description, "tags": tags}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
