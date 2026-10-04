import copy
import hashlib
import os
import threading
import yaml

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.yml')

_cache_lock = threading.Lock()
_cache = {'key': None, 'config': None}


def load_config():
    """Return the parsed config.yml, re-reading it only when the file changes."""
    try:
        st = os.stat(CONFIG_PATH)
    except FileNotFoundError:
        raise FileNotFoundError(f'config.yml not found at {CONFIG_PATH}.')
    key = (st.st_mtime_ns, st.st_size)
    with _cache_lock:
        if _cache['key'] != key:
            _cache['config'] = _parse_config()
            _cache['key'] = key
        # Callers get their own copy, as they did when the file was parsed on every call
        return copy.deepcopy(_cache['config'])


DEFAULT_MAX_UPLOAD_MB = 1024


def upload_limit_bytes(config):
    """Request size limit from config.yml max_upload_mb (0 or less means no limit)."""
    try:
        mb = float(config.get('max_upload_mb', DEFAULT_MAX_UPLOAD_MB))
    except (TypeError, ValueError):
        mb = DEFAULT_MAX_UPLOAD_MB
    return int(mb * 1024 * 1024) if mb > 0 else None


def _parse_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f) or {}
    # Hash password if present
    if 'password' in config and config['password']:
        config['password_hash'] = hashlib.sha256(config['password'].encode()).hexdigest()
        del config['password']
    # Ensure feature_toggles exists
    config.setdefault('feature_toggles', {})
    # Ensure Who is Home widget is enabled by default unless explicitly disabled in config.yml
    config['feature_toggles'].setdefault('who_is_home', True)
    # Personal status feature toggle (new)
    config['feature_toggles'].setdefault('personal_status', True)
    # Homepage chores widget toggle (runtime value may be overridden in app_setting)
    config['feature_toggles'].setdefault('show_chores_on_homepage', False)
    # Reminders defaults & calendar start day (supports sunday..saturday or 0-6)
    rem = config.setdefault('reminders', {})
    # Do not overwrite existing user value
    if 'calendar_start_day' not in rem or rem.get('calendar_start_day') in (None, ''):
        rem.setdefault('calendar_start_day', 'sunday')  # default Sunday to align with expense tracker
    # Admin name default
    config.setdefault('admin_name', 'Administrator')
    # Family members default list
    config.setdefault('family_members', [])
    # Theme defaults
    theme = config.setdefault('theme', {})
    theme.setdefault('primary_color', '#1d4ed8')
    theme.setdefault('secondary_color', '#a0aec0')
    theme.setdefault('background_color', '#f7fafc')
    theme.setdefault('card_background_color', '#ffffff')
    theme.setdefault('text_color', '#333333')
    theme.setdefault('sidebar_background_color', '#2563eb')
    theme.setdefault('sidebar_text_color', '#ffffff')
    theme.setdefault('sidebar_link_color', 'rgba(255,255,255,0.95)')
    theme.setdefault('sidebar_link_border_color', 'rgba(255,255,255,0.18)')
    # Weather widget defaults
    weather = config.setdefault('weather', {})
    weather.setdefault('enabled', False)
    weather.setdefault('label', '')
    weather.setdefault('latitude', '')
    weather.setdefault('longitude', '')
    weather.setdefault('timezone', '')
    weather.setdefault('units', 'metric')
    weather.setdefault('view', 'compact')
    return config
