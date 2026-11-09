"""Helpers for the API tests: an app with its own config.yml, and tokens to call it with."""
import yaml

from app import config as config_module
from app import create_app, db

BASE_CONFIG = {
    'instance_name': 'Test Home',
    'admin_name': 'Administrator',
    'family_members': ['Priya', 'Dad', 'Mom'],
    'feature_toggles': {
        'notes': True, 'shopping_list': True, 'calendar': True, 'chores': True, 'recipes': True,
        'expiry_tracker': True, 'expense_tracker': True, 'who_is_home': True, 'personal_status': True,
    },
    'reminders': {'categories': [{'key': 'health', 'label': 'Health', 'color': '#dc2626'}]},
}


class Home:
    """An app, a test client and a config.yml that tests can change between requests."""

    def __init__(self, tmp_path, monkeypatch, **overrides):
        self.path = tmp_path / 'config.yml'
        self.config = {**BASE_CONFIG, **overrides}
        self.write()
        monkeypatch.setattr(config_module, 'CONFIG_PATH', str(self.path))
        monkeypatch.setattr(config_module, '_cache', {'key': None, 'config': None})
        self.app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite://', 'SECRET_KEY': 'test'})
        with self.app.app_context():
            db.create_all()
            self.app.config['HOMEHUB_CONFIG'] = config_module.load_config()
        self.client = self.app.test_client()

    def write(self):
        # The cache key is the file's mtime and size; a rewrite in the same tick could look unchanged
        import os
        import time
        self.path.write_text(yaml.safe_dump(self.config), encoding='utf-8')
        stamp = time.time() + getattr(self, '_bump', 0)
        self._bump = getattr(self, '_bump', 0) + 1
        os.utime(self.path, (stamp, stamp))

    def set(self, **changes):
        self.config.update(changes)
        self.write()

    def toggle(self, feature, on):
        toggles = dict(self.config.get('feature_toggles') or {})
        toggles[feature] = on
        self.set(feature_toggles=toggles)

    def token(self, name='Hermes', act_as=False, allow_delete=False, **perms):
        """Create a token and return its secret. perms: tool=level."""
        from app.api import tokens
        with self.app.app_context():
            self.app.config['HOMEHUB_CONFIG'] = config_module.load_config()
            _row, secret = tokens.create_token(name, perms, act_as, allow_delete)
        return secret

    def all_tools(self, name='Agent', level='write', act_as=True, allow_delete=False):
        from app.api import tokens
        perms = {t.key: (level if level in t.levels else 'read') for t in tokens.TOOLS}
        return self.token(name, act_as=act_as, allow_delete=allow_delete, **perms)

    def call(self, method, path, secret=None, json=None, headers=None, **kwargs):
        headers = dict(headers or {})
        if secret:
            headers['Authorization'] = f'Bearer {secret}'
        return self.client.open('/api/v1' + path, method=method, json=json, headers=headers, **kwargs)

    def get(self, path, secret=None, **kw):
        return self.call('GET', path, secret, **kw)

    def post(self, path, secret=None, json=None, **kw):
        return self.call('POST', path, secret, json=json, **kw)

    def put(self, path, secret=None, json=None, **kw):
        return self.call('PUT', path, secret, json=json, **kw)

    def patch(self, path, secret=None, json=None, **kw):
        return self.call('PATCH', path, secret, json=json, **kw)

    def delete(self, path, secret=None, json=None, **kw):
        return self.call('DELETE', path, secret, json=json, **kw)

    def ctx(self):
        return self.app.app_context()
