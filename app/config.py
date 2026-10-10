import copy
import logging
import os
import threading
import yaml
from werkzeug.security import generate_password_hash

log = logging.getLogger(__name__)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.yml')
EXAMPLE_CONFIG_PATH = os.path.join(BASE_DIR, 'config-example.yml')

_cache_lock = threading.Lock()
_cache = {'key': None, 'config': None}
_warned = set()


def _config_source():
    """The file to read: config.yml, or config-example.yml when config.yml is not a usable file.

    Docker creates an empty folder called config.yml when the file is missing from the
    host at first start, so a folder is treated like a missing file.
    """
    if os.path.isfile(CONFIG_PATH):
        return CONFIG_PATH
    problem = 'is a folder, not a file' if os.path.isdir(CONFIG_PATH) else 'is missing'
    fallback = EXAMPLE_CONFIG_PATH if os.path.isfile(EXAMPLE_CONFIG_PATH) else None
    if (CONFIG_PATH, problem) not in _warned:
        _warned.add((CONFIG_PATH, problem))
        log.warning(
            'config.yml %s (%s). HomeHub is running on the defaults from config-example.yml. '
            'Copy config-example.yml to config.yml, edit it, and restart.', problem, CONFIG_PATH)
    return fallback


def load_config():
    """Return the parsed config.yml, re-reading it only when the file changes."""
    source = _config_source()
    if source:
        st = os.stat(source)
        key = (source, st.st_mtime_ns, st.st_size)
    else:
        key = (None,)
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


# Theme values that shipped as defaults before the refreshed look. A config.yml that still
# carries them (copied from config-example.yml) gets the new look; custom colours are kept.
_LEGACY_SIDEBAR_BACKGROUNDS = {'#2563eb'}
_LEGACY_THEME_VALUES = {
    'background_color': {'#f7fafc'},
    'text_color': {'#333', '#333333'},
}
_LEGACY_SIDEBAR_VALUES = {
    'sidebar_text_color': {'#ffffff', '#fff'},
    'sidebar_link_color': {'rgba(255,255,255,0.95)'},
    'sidebar_link_border_color': {'rgba(255,255,255,0.18)'},
    'sidebar_active_color': {'#3b82f6'},
    'sidebar_active_text_color': {'#ffffff', '#fff'},
}
_LIGHT_SIDEBAR = {
    'sidebar_text_color': '#0f172a',
    'sidebar_link_color': '#475569',
    'sidebar_link_border_color': 'transparent',
    'sidebar_active_color': 'rgba(var(--primary-rgb), 0.10)',
    'sidebar_active_text_color': 'var(--primary-color)',
}


def _norm_colour(value):
    return str(value or '').replace(' ', '').lower()


def _apply_theme_defaults(theme):
    for key, legacy in _LEGACY_THEME_VALUES.items():
        if _norm_colour(theme.get(key)) in legacy:
            theme.pop(key)
    theme.setdefault('primary_color', '#1d4ed8')
    theme.setdefault('secondary_color', '#a0aec0')
    theme.setdefault('background_color', '#f8fafc')
    theme.setdefault('card_background_color', '#ffffff')
    theme.setdefault('text_color', '#0f172a')
    custom_sidebar = bool(theme.get('sidebar_background_color')) and \
        _norm_colour(theme.get('sidebar_background_color')) not in _LEGACY_SIDEBAR_BACKGROUNDS
    if custom_sidebar:
        # A coloured sidebar the user picked: keep the previous light-on-colour link styling
        theme.setdefault('sidebar_text_color', '#ffffff')
        theme.setdefault('sidebar_link_color', 'rgba(255,255,255,0.95)')
        theme.setdefault('sidebar_link_border_color', 'rgba(255,255,255,0.18)')
        theme.setdefault('sidebar_active_color', '#3b82f6')
        theme.setdefault('sidebar_active_text_color', '#ffffff')
    else:
        # New light sidebar. Keys still set to the old example values get the new defaults;
        # anything else the user customised is kept.
        theme['sidebar_background_color'] = '#ffffff'
        for key, default in _LIGHT_SIDEBAR.items():
            value = theme.get(key)
            if not value or _norm_colour(value) in _LEGACY_SIDEBAR_VALUES.get(key, set()):
                theme[key] = default
    return theme


def _parse_config():
    source = _config_source()
    config = {}
    if source:
        with open(source, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    # config.yml holds the site password as written; in memory only a salted hash is kept
    password = config.pop('password', None)
    if password:
        config['password_hash'] = generate_password_hash(str(password), method='pbkdf2:sha256')
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
    # UI language; anything without a translation falls back to English
    config.setdefault('language', 'en')
    _apply_theme_defaults(config.setdefault('theme', {}))
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
