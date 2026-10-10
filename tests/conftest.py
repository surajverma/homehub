import os
import shutil

import pytest

from app import config as config_module

EXAMPLE_CONFIG = os.path.join(config_module.BASE_DIR, 'config-example.yml')


@pytest.fixture(scope='session')
def example_config_path(tmp_path_factory):
    path = tmp_path_factory.mktemp('config') / 'config.yml'
    shutil.copyfile(EXAMPLE_CONFIG, path)
    return str(path)


@pytest.fixture(autouse=True)
def isolated_config(example_config_path, monkeypatch):
    """Run every test against a copy of config-example.yml, never the developer's own config.yml.

    The app re-reads config.yml on each request, so a local site password or language would
    otherwise change what the tests see; a fresh checkout has no config.yml at all.
    Tests that need other settings point CONFIG_PATH at their own file.
    """
    monkeypatch.setattr(config_module, 'CONFIG_PATH', example_config_path)
    monkeypatch.setattr(config_module, '_cache', {'key': None, 'config': None})


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """Uploads and PDFs made by tests go to a temp folder, not the developer's own uploads/ and pdfs/."""
    from app.blueprints import media_pdfs, uploads
    for module, name in ((uploads, 'UPLOAD_FOLDER'), (media_pdfs, 'PDF_FOLDER')):
        folder = tmp_path / name.lower()
        folder.mkdir()
        monkeypatch.setattr(module, name, str(folder))


@pytest.fixture(autouse=True)
def no_leftover_lockouts():
    """Failed password attempts are counted per process; start each test with none."""
    from app import admin
    admin._failed_attempts.clear()
