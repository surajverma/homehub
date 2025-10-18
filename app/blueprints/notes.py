from flask import render_template, request, redirect, url_for, current_app
from ..models import db, Note
from ..blueprints import main_bp
from ..security import sanitize_text, sanitize_html


@main_bp.route('/notes', methods=['GET', 'POST'])
def notes():
    if request.method == 'POST':
        note_id = request.form.get('note_id')
        content = sanitize_html(request.form['content'])
        creator = sanitize_text(request.form['creator'])
        if note_id:
            n = Note.query.get_or_404(int(note_id))
            admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
            admin_aliases = {admin_name, 'Administrator', 'admin'}
            if creator in admin_aliases or creator == n.creator:
                n.content = content
                db.session.commit()
        else:
            note = Note(content=content, creator=creator)
            db.session.add(note)
            db.session.commit()
        return redirect(url_for('main.notes'))
    notes = Note.query.order_by(Note.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('notes.html', notes=notes, config=config)


@main_bp.route('/notes/delete/<int:note_id>', methods=['POST'])
def delete_note(note_id):
    note = Note.query.get_or_404(note_id)
    user = sanitize_text(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == note.creator:
        db.session.delete(note)
        db.session.commit()
    return redirect(url_for('main.notes'))
