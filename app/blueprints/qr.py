import base64
import os
from io import BytesIO

from flask import render_template, request, redirect, url_for, current_app
import qrcode

from ..models import db, QRCode
from ..blueprints import main_bp
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
        # Persist a file for history download
        os.makedirs(STATIC_DIR, exist_ok=True)
        filename = f"qr_{len(text)}_{abs(hash(payload)) % (10**8)}.png"
        out_path = os.path.join(STATIC_DIR, filename)
        img.save(out_path)
        # Save DB row
        rec = QRCode(text=payload, original_input=text, filename=filename, creator=creator)
        db.session.add(rec)
        db.session.commit()
    history = QRCode.query.order_by(QRCode.timestamp.desc()).limit(50).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('qr.html', qr_img=qr_img, history=history, config=config)


@main_bp.route('/qr/delete/<int:qr_id>', methods=['POST'])
def qr_delete(qr_id: int):
    rec = QRCode.query.get_or_404(qr_id)
    user = sanitize_text(request.form.get('user', ''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == rec.creator:
        try:
            path = os.path.join(STATIC_DIR, rec.filename)
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        db.session.delete(rec)
        db.session.commit()
    return redirect(url_for('main.qr_view'))
