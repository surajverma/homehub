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
