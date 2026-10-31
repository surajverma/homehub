"""Backups page for the admin: make, list, download and delete backups, and restore one.

Backups are built on the server into data/backups/ by the same code as ``flask backup``,
in a background thread, and then downloaded as ordinary files (so a download can resume).
A restore uses a zip uploaded for it (deleted afterwards) or a safety backup from the list.
The whole page needs an admin password, and restore asks for it again.
"""
import os

from flask import abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_babel import gettext as _, ngettext

from .. import admin
from .. import backup
from ..blueprints import main_bp
from ..security import sanitize_text

# Above this the page warns that the backup will take a while
LARGE_BACKUP_BYTES = 1024 ** 3


def _error_message(code: str) -> str:
    messages = {
        'no_database': _('There is no database to back up yet.'),
        'no_space': _('Not enough free disk space for this backup.'),
        'busy': _('A backup or restore is already running. Wait for it to finish.'),
        'not_zip': _('That file is not a zip file.'),
        'unsafe': _('This backup contains unsafe file paths and was rejected.'),
        'nothing_selected': _('Choose at least one part to restore.'),
    }
    return messages.get(code) or _('This is not a valid HomeHub backup, or it is damaged.')


def part_labels() -> dict:
    return {
        'database': _('Database & settings'),
        'uploads': _('Shared Cloud uploads'),
        'pdfs': _('PDFs'),
        'media': _('Downloaded media'),
        'config': _('config.yml'),
    }


@main_bp.app_template_filter('filesize')
def filesize_filter(num) -> str:
    size = float(num or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} TB'


def _user() -> str:
    return sanitize_text(request.values.get('user', ''))


def _require_admin():
    """None when this request comes from the unlocked admin, else the response to send instead.

    Backups hold the secret key and everyone's data, and may contain the site password, so the whole page needs
    an admin password; picking the admin name is not enough.
    """
    if not admin.admin_password_enabled():
        message = _('Backups need an admin password. Set one on the server with %(command)s. Until then, use flask backup and flask restore on the server. See the README for details: %(url)s',
                    command='docker exec -it homehub flask set-admin-password',
                    url='https://github.com/surajverma/homehub#backup-and-restore')
        if request.path.endswith('/status'):
            return jsonify({'ok': False, 'error': message}), 403
        return message, 403, {'Content-Type': 'text/plain; charset=utf-8'}
    if admin.is_admin(_user()):
        return None
    if request.path.endswith('/status'):
        return jsonify({'ok': False, 'error': _('Only admin can manage backups.')}), 403
    flash(_('Only admin can manage backups.'), 'error')
    return redirect(url_for('main.index'))


def _back():
    return redirect(url_for('main.backups_page', user=_user()))


def _report_finished_job() -> None:
    """Flash the outcome of a background job once, the first time the page is opened after it ends."""
    state = backup.jobs.take_finished()
    if not state:
        return
    if state['status'] == 'failed':
        flash(_error_message(state['error']), 'error')
    elif state['kind'] == 'backup':
        flash(_('Backup %(name)s is ready.', name=state['result']['name']), 'success')
    else:
        flash(_('Restore complete. Restart HomeHub now so it uses the restored data.'), 'success')


@main_bp.route('/admin/backups', methods=['GET'])
def backups_page():
    denied = _require_admin()
    if denied:
        return denied
    if not backup.jobs.running():
        backup.remove_stale_parts()
    _report_finished_job()
    p = backup.paths()
    backups = backup.list_backups()
    config_text = None
    if os.path.isfile(p['config_from_backup']):
        try:
            with open(p['config_from_backup'], encoding='utf-8') as f:
                config_text = backup.masked_config(f.read())
        except (OSError, UnicodeDecodeError):
            config_text = None
        if config_text is None:
            config_text = _('The config.yml from the backup could not be read.')
    return render_template(
        'backups.html',
        config=current_app.config['HOMEHUB_CONFIG'],
        user=_user(),
        backups=backups,
        total_size=sum(b['size'] for b in backups),
        sizes=backup.part_sizes(),
        part_labels=part_labels(),
        default_parts=backup.DEFAULT_PARTS,
        large_bytes=LARGE_BACKUP_BYTES,
        job=backup.jobs.status() if backup.jobs.running() else None,
        needs_restart=backup.jobs.needs_restart,
        config_from_backup=config_text,
        config_from_backup_path=os.path.join('data', backup.CONFIG_FROM_BACKUP),
        error_message=_error_message,
    )


@main_bp.route('/admin/backups/status', methods=['GET'])
def backups_status():
    denied = _require_admin()
    if denied:
        return denied
    state = backup.jobs.status()
    if not state:
        return jsonify({'ok': True, 'running': False})
    return jsonify({
        'ok': True,
        'running': backup.jobs.running(),
        'kind': state['kind'],
        'done': state['done'],
        'total': state['total'],
    })


@main_bp.route('/admin/backups/create', methods=['POST'])
def create_backup():
    denied = _require_admin()
    if denied:
        return denied
    parts = [p for p in request.form.getlist('parts') if p in backup.PARTS]

    def run(progress):
        path = backup.create_backup(parts=parts, progress=progress)
        return {'name': os.path.basename(path)}

    try:
        backup.jobs.start('backup', run)
    except backup.BackupError as exc:
        flash(_error_message(exc.code), 'error')
    return _back()


@main_bp.route('/admin/backups/download/<name>', methods=['GET'])
def download_backup(name):
    denied = _require_admin()
    if denied:
        return denied
    path = backup.backup_file(name)
    if not path:
        abort(404)
    # conditional=True answers Range requests, so a cut-off download can resume
    return send_file(path, mimetype='application/zip', as_attachment=True, download_name=name,
                     conditional=True, max_age=0)


@main_bp.route('/admin/backups/delete/<name>', methods=['POST'])
def delete_backup(name):
    denied = _require_admin()
    if denied:
        return denied
    path = backup.backup_file(name)
    if not path:
        flash(_('That backup was not found.'), 'error')
        return _back()
    try:
        os.remove(path)
        flash(_('Backup %(name)s deleted.', name=name), 'success')
    except OSError:
        flash(_('Could not delete that backup.'), 'error')
    return _back()


@main_bp.route('/admin/backups/upload', methods=['POST'])
def upload_backup():
    """Take a backup zip for a restore and go straight to its restore page.

    It waits in data/restore-uploads/, not in the list, and is deleted once restored or cancelled.
    """
    denied = _require_admin()
    if denied:
        return denied
    file = request.files.get('backup')
    if not file or not file.filename:
        flash(_('No file selected.'), 'error')
        return _back()
    part = backup.upload_part_path()
    try:
        file.save(part)
        backup.validate_backup(part)
        target = backup.unique_upload_path()
        os.replace(part, target)
    except backup.BackupError as exc:
        backup.remove_quietly(part)
        flash(_error_message(exc.code), 'error')
        return _back()
    except OSError:
        backup.remove_quietly(part)
        flash(_('Could not save the uploaded backup.'), 'error')
        return _back()
    # The file's own name is only shown on the restore page, never used on disk
    return redirect(url_for('main.restore_upload_preview', name=os.path.basename(target), user=_user(),
                            file=os.path.basename(file.filename.replace(chr(92), '/'))[:120]))


def _restorable(source: str, name: str) -> str | None:
    """The zip a restore page may use: an uploaded one, or a safety backup from the list (the undo for a restore)."""
    if source == 'upload':
        return backup.uploaded_file(name)
    path = backup.backup_file(name)
    if path and backup.describe_backup(path)['kind'] == 'safety':
        return path
    return None


def _restore_urls(source: str, name: str) -> dict:
    if source == 'upload':
        return {'action': url_for('main.restore_upload', name=name),
                'cancel': url_for('main.cancel_upload', name=name),
                'preview': url_for('main.restore_upload_preview', name=name, user=_user())}
    return {'action': url_for('main.restore_backup', name=name), 'cancel': None,
            'preview': url_for('main.restore_preview', name=name, user=_user())}


def _preview(source: str, name: str):
    denied = _require_admin()
    if denied:
        return denied
    path = _restorable(source, name)
    if not path:
        abort(404)
    info = backup.describe_backup(path)
    if not info['error']:
        # The list only reads the manifest; the preview checks the database as well
        try:
            backup.validate_backup(path)
        except backup.BackupError as exc:
            info['error'] = exc.code
    shown_name = sanitize_text(request.args.get('file', ''))[:120] if source == 'upload' else ''
    return render_template('backup_restore.html', config=current_app.config['HOMEHUB_CONFIG'], user=_user(), info=info, part_labels=part_labels(),
                           error_message=_error_message, source=source, urls=_restore_urls(source, name),
                           shown_name=shown_name or info['name'],
                           lockout_seconds=admin.unlock_wait_seconds(request.remote_addr or 'unknown'))


def _restore(source: str, name: str):
    denied = _require_admin()
    if denied:
        return denied
    path = _restorable(source, name)
    if not path:
        abort(404)
    client = request.remote_addr or 'unknown'
    back_to_preview = redirect(_restore_urls(source, name)['preview'])
    wait = admin.unlock_wait_seconds(client)
    if wait:
        flash(ngettext('Too many attempts. Try again in %(num)s second.', 'Too many attempts. Try again in %(num)s seconds.', wait), 'error')
        return back_to_preview
    # Asked again even when this browser is already unlocked; wrong tries count toward the lockout.
    # The password is the confirmation, so the page does not ask a second time.
    if not admin.try_unlock(request.form.get('password', ''), client):
        flash(_('Incorrect admin password'), 'error')
        return back_to_preview
    parts = [p for p in request.form.getlist('parts') if p in backup.PARTS]
    if not parts:
        flash(_error_message('nothing_selected'), 'error')
        return back_to_preview

    def run(progress):
        from .. import migrations
        try:
            result = backup.restore_backup(path, parts=parts, progress=progress)
        finally:
            # An uploaded zip has done its job (or failed); it is not kept on the server
            if source == 'upload':
                backup.remove_quietly(path)
        p = backup.paths()
        if 'database' in parts:
            migrations.run(p['db'], pdf_dir=p['pdfs'])
        backup.jobs.needs_restart = True
        return {
            'safety_backup': os.path.basename(result['safety_backup']) if result['safety_backup'] else None,
            'restored': result['restored'],
        }

    try:
        backup.jobs.start('restore', run)
    except backup.BackupError as exc:
        flash(_error_message(exc.code), 'error')
        return back_to_preview
    return _back()


@main_bp.route('/admin/backups/restore/<name>', methods=['GET'])
def restore_preview(name):
    return _preview('saved', name)


@main_bp.route('/admin/backups/restore/<name>', methods=['POST'])
def restore_backup(name):
    return _restore('saved', name)


@main_bp.route('/admin/backups/restore-upload/<name>', methods=['GET'])
def restore_upload_preview(name):
    return _preview('upload', name)


@main_bp.route('/admin/backups/restore-upload/<name>', methods=['POST'])
def restore_upload(name):
    return _restore('upload', name)


@main_bp.route('/admin/backups/restore-upload/<name>/cancel', methods=['POST'])
def cancel_upload(name):
    denied = _require_admin()
    if denied:
        return denied
    path = backup.uploaded_file(name)
    if path:
        backup.remove_quietly(path)
    flash(_('Restore cancelled. The uploaded file was removed from the server.'), 'success')
    return _back()


@main_bp.route('/admin/backups/config-from-backup/dismiss', methods=['POST'])
def dismiss_config_from_backup():
    denied = _require_admin()
    if denied:
        return denied
    backup.remove_quietly(backup.paths()['config_from_backup'])
    return _back()
