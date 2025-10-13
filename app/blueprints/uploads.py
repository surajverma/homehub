import os
from flask import render_template, request, redirect, url_for, send_from_directory, current_app
from werkzeug.utils import secure_filename
from ..models import db, File
from ..blueprints import main_bp
from ..security import sanitize_text


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')


@main_bp.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        files = request.files.getlist('files') or ([request.files['file']] if 'file' in request.files else [])
        creator = sanitize_text(request.form['creator'])
        for file in files:
            if not file or not getattr(file, 'filename', ''):
                continue
            filename = secure_filename(file.filename)
            file.save(os.path.join(UPLOAD_FOLDER, filename))
            db_file = File(filename=filename, creator=creator)
            db.session.add(db_file)
        db.session.commit()
        return redirect(url_for('main.upload'))
    files = File.query.order_by(File.upload_time.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('upload.html', files=files, config=config)


@main_bp.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


@main_bp.route('/upload/delete/<int:file_id>', methods=['POST'])
def delete_file(file_id):
    db_file = File.query.get_or_404(file_id)
    user = sanitize_text(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == db_file.creator:
        try:
            os.remove(os.path.join(UPLOAD_FOLDER, db_file.filename))
        except Exception:
            pass
        db.session.delete(db_file)
        db.session.commit()
    return redirect(url_for('main.upload'))
