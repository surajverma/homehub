import os
from flask import render_template, request, redirect, url_for, send_from_directory, current_app, make_response, flash
from flask_babel import gettext as _, ngettext
from ..models import db, File
from ..storage import safe_name, unique_name
from ..blueprints import main_bp
from ..admin import can_modify
from ..security import sanitize_text


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')


@main_bp.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        files = request.files.getlist('files') or ([request.files['file']] if 'file' in request.files else [])
        creator = sanitize_text(request.form['creator'])
        saved = 0
        for file in files:
            if not file or not getattr(file, 'filename', ''):
                continue
            filename = safe_name(file.filename)
            # A second file with the same name gets its own name on disk instead of replacing the first
            stored_name = unique_name(UPLOAD_FOLDER, filename)
            file.save(os.path.join(UPLOAD_FOLDER, stored_name))
            db_file = File(filename=filename, stored_name=stored_name, creator=creator)
            db.session.add(db_file)
            saved += 1
        db.session.commit()
        if saved:
            flash(ngettext('%(num)s file uploaded.', '%(num)s files uploaded.', saved), 'success')
        else:
            flash(_('No file selected.'), 'error')
        return redirect(url_for('main.upload'))
    files = File.query.order_by(File.upload_time.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('upload.html', files=files, config=config)


@main_bp.route('/uploads/<filename>')
def uploaded_file(filename):
    # Download under the name it was uploaded with, whatever it is called on disk
    row = File.query.filter_by(stored_name=filename).first()
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True,
                               download_name=row.filename if row else None)


@main_bp.route('/uploads/preview/<filename>')
def preview_file(filename):
    """Serve file for preview (inline) - restricted to safe types only"""
    # Only allow preview for known-safe file types to prevent XSS attacks
    # HTML, SVG, XML and other executable content must be downloaded, not previewed
    safe_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.ico','.pdf', '.txt', '.docx', '.xlsx', '.pptx', '.odt', '.ods', '.odp','.mp4', '.mp3', '.wav', '.ogg', '.webm'}
    ext = os.path.splitext(filename)[1].lower()
    
    if ext not in safe_extensions:
        # Force download for potentially dangerous files (HTML, SVG, JS, etc.)
        return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)
    
    # Add security headers to prevent script execution even for allowed types
    response = make_response(send_from_directory(UPLOAD_FOLDER, filename, as_attachment=False))
    response.headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; media-src 'self'"
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    return response


@main_bp.route('/upload/delete/<int:file_id>', methods=['POST'])
def delete_file(file_id):
    db_file = db.get_or_404(File, file_id)
    user = sanitize_text(request.form['user'])
    if can_modify(user, db_file.creator):
        disk_name = db_file.disk_name
        # Older uploads with the same name share one file on disk; keep it while another row needs it
        shared = any(other.id != db_file.id and other.disk_name == disk_name
                     for other in File.query.filter((File.filename == disk_name) | (File.stored_name == disk_name)))
        if not shared:
            try:
                os.remove(os.path.join(UPLOAD_FOLDER, disk_name))
            except Exception:
                pass
        db.session.delete(db_file)
        db.session.commit()
        flash(_('File deleted.'), 'success')
    else:
        flash(_('Not allowed to delete file.'), 'error')
    return redirect(url_for('main.upload'))
