import base64
import os
import re
from io import BytesIO

from flask import render_template, request, redirect, url_for, current_app, send_file, flash
from flask_babel import gettext as _
import qrcode

from ..models import db, QRCode
from ..blueprints import main_bp
from ..admin import can_modify
from ..security import sanitize_text

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
STATIC_DIR = os.path.join(BASE_DIR, 'static')


def _wifi_to_qrtext(raw: str) -> str | None:
    """Parse a simple space-delimited wifi string like:
    ssid:mywifiname pass:123456789 type:wpa hidden:false
    into the standard WIFI QR encoding: WIFI:T:WPA;S:mywifiname;P:123456789;H:false;;
    Return None if not a wifi pattern; otherwise the transformed string.
    """
    if not raw:
        return None
    s = raw.strip()
    if 'ssid:' not in s or 'pass:' not in s:
        return None
    parts = {}
    for token in s.split():
        if ':' in token:
            k, v = token.split(':', 1)
            parts[k.strip().lower()] = v.strip()
    if 'ssid' not in parts or 'pass' not in parts:
        return None
    enc = (parts.get('type') or 'wpa').upper()
    if enc not in ('WPA', 'WEP', 'NOPASS'):
        enc = 'WPA'
    hidden = (parts.get('hidden') or 'false').lower() in ('1', 'true', 'yes')
    # Escape special characters per QR WIFI format: \;,
    def esc(x: str) -> str:
        return (x or '').replace('\\', r'\\').replace(';', r'\;').replace(',', r'\,')
    ssid = esc(parts['ssid'])
    pwd = esc(parts['pass'])
    return f"WIFI:T:{enc};S:{ssid};P:{pwd};H:{'true' if hidden else 'false'};;"


@main_bp.route('/qr', methods=['GET', 'POST'])
def qr_view():
    qr_img = None
    if request.method == 'POST':
        text = sanitize_text(request.form.get('qrtext', ''))
        creator = sanitize_text(request.form.get('creator', ''))
        if not text:
            return redirect(url_for('main.qr_view'))
        # Convert wifi shorthand format if applicable
        wifi_text = _wifi_to_qrtext(text)
        payload = wifi_text or text
        # Generate QR
        img = qrcode.make(payload)
        buf = BytesIO()
        img.save(buf, format='PNG')
        b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        qr_img = b64
        # Only the text is kept; the history draws the image again when it is asked for
        rec = QRCode(text=payload, original_input=text, filename='', creator=creator)
        db.session.add(rec)
        db.session.commit()
        flash(_('QR code created.'), 'success')
    history = QRCode.query.order_by(QRCode.timestamp.desc()).limit(50).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('qr.html', qr_img=qr_img, history=history, config=config)


@main_bp.route('/qr/image/<int:qr_id>.png')
def qr_image(qr_id: int):
    """A history entry's QR code, drawn from its stored text."""
    rec = db.get_or_404(QRCode, qr_id)
    buf = BytesIO()
    qrcode.make(rec.text).save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name=f'qr-{rec.id}.png',
                     as_attachment=bool(request.args.get('download')))


@main_bp.route('/qr/delete/<int:qr_id>', methods=['POST'])
def qr_delete(qr_id: int):
    rec = db.get_or_404(QRCode, qr_id)
    user = sanitize_text(request.form.get('user', ''))
    if can_modify(user, rec.creator):
        # Codes made by older versions left a PNG in static/
        try:
            if re.fullmatch(r'qr_\w+\.png', rec.filename or ''):
                path = os.path.join(STATIC_DIR, rec.filename)
                if os.path.exists(path):
                    os.remove(path)
        except Exception:
            pass
        db.session.delete(rec)
        db.session.commit()
        flash(_('QR code deleted.'), 'success')
    else:
        flash(_('Not allowed to delete QR code.'), 'error')
    return redirect(url_for('main.qr_view'))
