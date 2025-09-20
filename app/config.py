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
    # Admin name default
    config.setdefault('admin_name', 'Administrator')
    # Family members default list
    config.setdefault('family_members', [])
    return config
