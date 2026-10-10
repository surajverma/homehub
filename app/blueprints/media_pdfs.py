import os, re, secrets, shutil, subprocess
from flask_babel import gettext as _
from threading import Thread
from flask import render_template, request, redirect, url_for, send_from_directory, jsonify, current_app, flash
from ..models import db, Media, PDF
from ..blueprints import main_bp
from ..clock import utcnow
from ..admin import can_modify
from ..security import sanitize_text, is_url_safe_for_fetch
from werkzeug.utils import secure_filename
from ..storage import unique_name


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MEDIA_FOLDER = os.path.join(BASE_DIR, 'media')
PDF_FOLDER = os.path.join(BASE_DIR, 'pdfs')


@main_bp.route('/media', methods=['GET', 'POST'])
def media():
    if request.method == 'POST':
        url = sanitize_text(request.form['url'])
        creator = sanitize_text(request.form['creator'])
        if not is_url_safe_for_fetch(url):
            flash(_('Invalid or disallowed URL. Only external http(s) URLs are allowed.'), 'error')
            return redirect(url_for('main.media'))
        fmt = sanitize_text(request.form.get('format', 'mp4'))
        quality = sanitize_text(request.form.get('quality', 'best'))
        base = f"media_{int(utcnow().timestamp())}"
        output_tmpl = os.path.join(MEDIA_FOLDER, base + ".%(ext)s")
        media_obj = Media(title=url, url=url, creator=creator, filepath='', status='pending')
        db.session.add(media_obj)
        db.session.commit()
        flash(_('Download queued. You can switch tabs; refresh to check status.'), 'info')
        cmd = ["yt-dlp", "-o", output_tmpl]
        if fmt == 'mp3':
            cmd += ["-x", "--audio-format", "mp3"]
        else:
            selected = quality or 'best'
            if selected == 'best':
                fmt_string = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
            else:
                fmt_string = f"{selected}/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best"
            cmd += ["-f", fmt_string, "--merge-output-format", "mp4"]
        cmd += [url]

        app_obj = current_app._get_current_object()

        def worker(app, mid: int, base_prefix: str, command: list):
            with app.app_context():
                m = db.session.get(Media, mid)
                try:
                    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                    last_percent = -1
                    for line in proc.stdout:
                        try:
                            m = db.session.get(Media, mid)
                            if not m:
                                continue
                            match = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", line)
                            if match:
                                p = int(float(match.group(1)))
                                if p != last_percent and p % 5 == 0:
                                    m.progress = f"{p}%"
                                    db.session.commit()
                                    last_percent = p
                        except Exception:
                            pass
                    ret = proc.wait()
                    if ret != 0:
                        raise RuntimeError(f"yt-dlp exited with {ret}")
                    saved = None
                    for fname in os.listdir(MEDIA_FOLDER):
                        if fname.startswith(base_prefix):
                            saved = fname
                            break
                    m.filepath = saved or ''
                    m.status = 'done'
                except Exception:
                    m.status = 'error'
                finally:
                    m.progress = None
                    db.session.commit()

        Thread(target=worker, args=(app_obj, media_obj.id, base, cmd), daemon=True).start()
        return redirect(url_for('main.media'))
    media_list = Media.query.order_by(Media.download_time.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('media.html', media_list=media_list, config=config)


@main_bp.route('/media/status/<int:media_id>')
def media_status(media_id):
    m = db.get_or_404(Media, media_id)
    return jsonify({'status': m.status, 'progress': m.progress, 'filepath': m.filepath})


@main_bp.route('/media/<filename>')
def serve_media(filename):
    return send_from_directory(MEDIA_FOLDER, filename, as_attachment=True)


@main_bp.route('/media/preview/<filename>')
def preview_media(filename):
    """Serve media file for preview (inline) with security headers"""
    # Add security headers to prevent script execution
    from flask import make_response
    response = make_response(send_from_directory(MEDIA_FOLDER, filename, as_attachment=False))
    response.headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; media-src 'self'"
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    return response


@main_bp.route('/media/delete/<int:media_id>', methods=['POST'])
def delete_media(media_id):
    m = db.get_or_404(Media, media_id)
    user = sanitize_text(request.form['user'])
    if can_modify(user, m.creator):
        try:
            if m.filepath:
                base = m.filepath.rsplit('.', 1)[0]
                for fname in os.listdir(MEDIA_FOLDER):
                    if fname.startswith(base):
                        os.remove(os.path.join(MEDIA_FOLDER, fname))
        except Exception:
            pass
        db.session.delete(m)
        db.session.commit()
        flash(_('Download deleted.'), 'success')
    else:
        flash(_('Not allowed to delete download.'), 'error')
    return redirect(url_for('main.media'))


@main_bp.route('/pdfs', methods=['GET', 'POST'])
def pdfs():
    if request.method == 'POST':
        pdf_file = request.files['pdf']
        creator = sanitize_text(request.form['creator'])
        filename = pdf_file.filename
        if not filename:
            return redirect(url_for('main.pdfs'))
        # Only allow .pdf uploads
        if not filename.lower().endswith('.pdf'):
            flash(_('Only PDF files are allowed.'), 'error')
            return redirect(url_for('main.pdfs'))
        # Normalize and secure the user-provided filename to avoid traversal or odd chars
        safe_name = secure_filename(os.path.basename(filename))
        if not safe_name:
            flash(_('Invalid filename.'), 'error')
            return redirect(url_for('main.pdfs'))
        # The upload is only needed while it is being compressed, so it gets a throwaway name
        input_path = os.path.join(PDF_FOLDER, f"upload_{secrets.token_hex(8)}.pdf")
        pdf_file.save(input_path)
        # Compressing another PDF with the same name must not replace the earlier result
        compressed_path = unique_name(PDF_FOLDER, f"compressed_{safe_name}")
        output_path = os.path.join(PDF_FOLDER, compressed_path)
        try:
            try:
                gs_cmd = [
                    'gs', '-sDEVICE=pdfwrite', '-dCompatibilityLevel=1.4',
                    '-dPDFSETTINGS=/ebook', '-dNOPAUSE', '-dQUIET', '-dBATCH',
                    f'-sOutputFile={output_path}', input_path
                ]
                subprocess.run(gs_cmd, check=True)
            except Exception:
                shutil.copy(input_path, output_path)
        finally:
            try:
                os.remove(input_path)
            except OSError:
                pass
        pdf_obj = PDF(filename=safe_name, creator=creator, compressed_path=compressed_path)
        db.session.add(pdf_obj)
        db.session.commit()
        flash(_('PDF compressed.'), 'success')
        return redirect(url_for('main.pdfs'))
    pdfs = PDF.query.order_by(PDF.upload_time.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('pdfs.html', pdfs=pdfs, config=config)


@main_bp.route('/pdfs/<filename>')
def serve_pdf(filename):
    row = PDF.query.filter_by(compressed_path=filename).first()
    return send_from_directory(PDF_FOLDER, filename, as_attachment=True,
                               download_name=f"compressed_{row.filename}" if row and row.filename else None)


@main_bp.route('/pdfs/preview/<filename>')
def preview_pdf(filename):
    """Serve PDF file for preview (inline) with security headers"""
    from flask import make_response
    response = make_response(send_from_directory(PDF_FOLDER, filename, as_attachment=False))
    response.headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'"
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    return response


@main_bp.route('/pdfs/delete/<int:pdf_id>', methods=['POST'])
def delete_pdf(pdf_id):
    p = db.get_or_404(PDF, pdf_id)
    user = sanitize_text(request.form['user'])
    if can_modify(user, p.creator):
        try:
            if p.compressed_path:
                os.remove(os.path.join(PDF_FOLDER, p.compressed_path))
        except Exception:
            pass
        # Older uploads also left the uncompressed original behind; remove it unless
        # that name is another row's compressed file
        try:
            original = secure_filename(p.filename or '')
            if original and not PDF.query.filter(PDF.compressed_path == original, PDF.id != p.id).first():
                os.remove(os.path.join(PDF_FOLDER, original))
        except Exception:
            pass
        db.session.delete(p)
        db.session.commit()
        flash(_('PDF deleted.'), 'success')
    else:
        flash(_('Not allowed to delete PDF.'), 'error')
    return redirect(url_for('main.pdfs'))
