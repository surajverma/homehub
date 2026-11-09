from flask import render_template, request, redirect, url_for, current_app, flash
from flask_babel import gettext as _
from ..models import db, Note
from ..blueprints import main_bp
from ..admin import can_modify
from ..security import sanitize_text, sanitize_html
from ..undo import row_restorer, trash_info, trash_row


@main_bp.route('/notes', methods=['GET', 'POST'])
def notes():
    if request.method == 'POST':
        note_id = request.form.get('note_id')
        content = sanitize_html(request.form['content'])
        creator = sanitize_text(request.form['creator'])
        if note_id:
            n = db.get_or_404(Note, int(note_id))
            if can_modify(creator, n.creator):
                n.content = content
                db.session.commit()
                flash(_('Note updated.'), 'success')
            else:
                flash(_('Not allowed to edit note.'), 'error')
        else:
            note = Note(content=content, creator=creator)
            db.session.add(note)
            db.session.commit()
            flash(_('Note added.'), 'success')
        return redirect(url_for('main.notes'))
    notes = Note.query.order_by(Note.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('notes.html', notes=notes, config=config)


@main_bp.route('/notes/delete/<int:note_id>', methods=['POST'])
def delete_note(note_id):
    note = db.get_or_404(Note, note_id)
    user = sanitize_text(request.form['user'])
    if can_modify(user, note.creator):
        trash_note(note, user, _('Note moved to the trash.'))
    else:
        flash(_('Not allowed to delete note.'), 'error')
    return redirect(url_for('main.notes'))


def trash_note(note: Note, deleted_by: str, message: str | None = None) -> str:
    """Move a note to the trash (restorable for 7 days). Commits."""
    return trash_row('note', note, trash_info('notes', note.content, note.creator, deleted_by), message)


row_restorer('note', Note)
