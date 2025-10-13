from flask import render_template, request, redirect, url_for, current_app
from ..models import db, Chore
from ..blueprints import main_bp
from ..security import sanitize_text


@main_bp.route('/chores', methods=['GET', 'POST'])
def chores():
    if request.method == 'POST':
        description = sanitize_text(request.form['description'])
        creator = sanitize_text(request.form['creator'])
        chore = Chore(description=description, creator=creator)
        db.session.add(chore)
        db.session.commit()
        return redirect(url_for('main.chores'))
    chores = Chore.query.order_by(Chore.timestamp.desc()).all()
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
