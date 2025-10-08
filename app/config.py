import yaml
import os
import hashlib

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.yml')

def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f'config.yml not found at {CONFIG_PATH}.')
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
    return config
