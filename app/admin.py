"""Admin identity helpers.

Admin is still picked by name in the user switcher. When the server owner has
set an admin password (``flask set-admin-password``), the name alone is no longer enough:
the browser session must also have been unlocked with that password. Without a password
the app behaves as it always has.
"""
import time

from flask import current_app, g, has_request_context, session
from werkzeug.security import check_password_hash, generate_password_hash

from . import db

ADMIN_PASSWORD_KEY = 'admin_password_hash'
ADMIN_PASSWORD_MIN_LENGTH = 4
SESSION_KEY = 'admin_unlocked'

# Failed unlock throttling (per client address, in memory)
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60
_failed_attempts: dict[str, tuple[int, float]] = {}


def _ensure_app_setting_table():
    db.session.execute(db.text("CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY, value TEXT)"))


def get_admin_password_hash() -> str | None:
    if has_request_context() and hasattr(g, '_admin_password_hash'):
        return g._admin_password_hash
    value = None
    try:
        _ensure_app_setting_table()
        row = db.session.execute(
            db.text("SELECT value FROM app_setting WHERE key=:k"), {"k": ADMIN_PASSWORD_KEY}
        ).fetchone()
        value = (row[0] or None) if row else None
    except Exception:
        value = None
    if has_request_context():
        g._admin_password_hash = value
    return value


def set_admin_password(password: str) -> None:
    _ensure_app_setting_table()
    db.session.execute(
        db.text("INSERT INTO app_setting(key,value) VALUES(:k, :v) ON CONFLICT(key) DO UPDATE SET value=excluded.value"),
        {"k": ADMIN_PASSWORD_KEY, "v": generate_password_hash(password, method='pbkdf2:sha256')},
    )
    db.session.commit()


def clear_admin_password() -> None:
    _ensure_app_setting_table()
    db.session.execute(db.text("DELETE FROM app_setting WHERE key=:k"), {"k": ADMIN_PASSWORD_KEY})
    db.session.commit()


def admin_password_enabled() -> bool:
    return bool(get_admin_password_hash())


def admin_aliases() -> set[str]:
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    return {admin_name, 'Administrator', 'admin'}


def _fingerprint(password_hash: str) -> str:
    # Ties an unlocked session to the current password so a reset locks everyone out
    return password_hash[-16:]


def admin_unlocked() -> bool:
    password_hash = get_admin_password_hash()
    if not password_hash:
        return False
    return session.get(SESSION_KEY) == _fingerprint(password_hash)


def is_admin(user: str | None) -> bool:
    """True when ``user`` is the admin and, if a password is set, this session unlocked it."""
    if user not in admin_aliases():
        return False
    if not admin_password_enabled():
        return True
    return admin_unlocked()


def unlock_wait_seconds(client: str) -> int:
    count, until = _failed_attempts.get(client, (0, 0.0))
    remaining = until - time.time()
    return int(remaining) + 1 if count >= MAX_FAILED_ATTEMPTS and remaining > 0 else 0


def try_unlock(password: str, client: str) -> bool:
    password_hash = get_admin_password_hash()
    if password_hash and check_password_hash(password_hash, password or ''):
        _failed_attempts.pop(client, None)
        session[SESSION_KEY] = _fingerprint(password_hash)
        return True
    count, until = _failed_attempts.get(client, (0, 0.0))
    if count >= MAX_FAILED_ATTEMPTS and until <= time.time():
        count = 0
    _failed_attempts[client] = (count + 1, time.time() + LOCKOUT_SECONDS)
    return False


def lock() -> None:
    session.pop(SESSION_KEY, None)
