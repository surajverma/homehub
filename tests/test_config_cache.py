import io
import os

import pytest

from app import create_app, db
from app import config as config_module


def test_load_config_rereads_only_when_file_changes(tmp_path, monkeypatch):
    cfg = tmp_path / 'config.yml'
    cfg.write_text('instance_name: "One"\npassword: "secret"\n', encoding='utf-8')
    monkeypatch.setattr(config_module, 'CONFIG_PATH', str(cfg))
    monkeypatch.setattr(config_module, '_cache', {'key': None, 'config': None})
    calls = []
    real_parse = config_module._parse_config
    monkeypatch.setattr(config_module, '_parse_config', lambda: calls.append(1) or real_parse())

    first = config_module.load_config()
    second = config_module.load_config()
    assert first['instance_name'] == 'One'
    assert 'password' not in first and first['password_hash']
    assert len(calls) == 1
    # Each caller gets its own copy
    second['instance_name'] = 'changed'
    assert config_module.load_config()['instance_name'] == 'One'

    cfg.write_text('instance_name: "Two, longer"\n', encoding='utf-8')
    st = os.stat(cfg)
    os.utime(cfg, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert config_module.load_config()['instance_name'] == 'Two, longer'
    assert len(calls) == 2


def test_upload_limit_bytes():
    assert config_module.upload_limit_bytes({}) == 1024 * 1024 * 1024
    assert config_module.upload_limit_bytes({'max_upload_mb': 5}) == 5 * 1024 * 1024
    assert config_module.upload_limit_bytes({'max_upload_mb': 0}) is None
    assert config_module.upload_limit_bytes({'max_upload_mb': 'junk'}) == 1024 * 1024 * 1024


@pytest.fixture()
def client():
    app = create_app({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite://',
        'SECRET_KEY': 'test',
    })
    with app.app_context():
        db.create_all()
    return app.test_client()


def test_too_large_upload_redirects_with_message(client, monkeypatch):
    # The before-request hook re-applies the limit from config.yml, so set it there
    monkeypatch.setattr('app.blueprints.auth.upload_limit_bytes', lambda cfg: 1024)
    resp = client.post('/upload', data={
        'creator': 'Alice',
        'files': (io.BytesIO(b'x' * 4096), 'big.bin'),
    }, content_type='multipart/form-data', headers={'Referer': '/upload'})
    assert resp.status_code == 302
    page = client.get('/upload').get_data(as_text=True)
    assert 'Upload is too large' in page


def test_theme_defaults_upgrade_old_example_values_but_keep_custom_colours():
    old_example = config_module._apply_theme_defaults({
        'background_color': '#f7fafc', 'text_color': '#333',
        'sidebar_background_color': '#2563eb', 'sidebar_text_color': '#ffffff',
        'sidebar_link_color': 'rgba(255,255,255,0.95)', 'sidebar_active_color': '#3b82f6',
    })
    assert old_example['sidebar_background_color'] == '#ffffff'
    assert old_example['sidebar_link_color'] == '#475569'
    assert old_example['background_color'] == '#f8fafc'
    assert old_example['text_color'] == '#0f172a'

    custom = config_module._apply_theme_defaults({'sidebar_background_color': '#7c3aed', 'background_color': '#fff7ed'})
    assert custom['sidebar_background_color'] == '#7c3aed'
    assert custom['sidebar_link_color'] == 'rgba(255,255,255,0.95)'
    assert custom['sidebar_active_text_color'] == '#ffffff'
    assert custom['background_color'] == '#fff7ed'
