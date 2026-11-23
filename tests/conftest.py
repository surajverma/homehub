import os
import shutil

import flask
import pytest
from jinja2 import BytecodeCache
from werkzeug import security as werkzeug_security
from werkzeug.routing import Rule

from app import config as config_module

EXAMPLE_CONFIG = os.path.join(config_module.BASE_DIR, 'config-example.yml')

# --- Speed ---
# Most of the suite's time went on two things that say nothing about HomeHub itself:

# 1. Password hashing. Werkzeug's default is 1,000,000 PBKDF2 rounds, about 60 ms a hash, and the
#    login, admin and backup tests hash hundreds of times. The tests check that the right password
#    opens and the wrong one does not, not how slow a guess is, so they hash with fewer rounds. The
#    round count is stored in each hash, so the app code and the checks run unchanged; real installs
#    keep Werkzeug's default.
werkzeug_security.DEFAULT_PBKDF2_ITERATIONS = 1000


# 2. Compiling the Jinja templates. Each test builds its own app, and each app compiled every page it
#    rendered from scratch. One in-memory bytecode cache for the whole run compiles each template once;
#    the cache is keyed on the template's source, so an edited template is still compiled again.
class _SharedBytecodeCache(BytecodeCache):
    def __init__(self):
        self._store = {}

    def load_bytecode(self, bucket):
        code = self._store.get(bucket.key)
        if code is not None:
            bucket.bytecode_from_string(code)

    def dump_bytecode(self, bucket):
        self._store[bucket.key] = bucket.bytecode_to_string()


_BYTECODE_CACHE = _SharedBytecodeCache()
_create_jinja_environment = flask.Flask.create_jinja_environment


def _jinja_environment_with_cache(self):
    env = _create_jinja_environment(self)
    env.bytecode_cache = _BYTECODE_CACHE
    return env


flask.Flask.create_jinja_environment = _jinja_environment_with_cache


# 3. Compiling the URL rules. Werkzeug turns every route into a small Python function (by building and
#    compiling its syntax tree) each time an app is made, and that was most of create_app's time. The
#    function depends only on the rule itself (its text, the defaults folded into it and whether unknown
#    arguments are appended), never on the app, so the same rule reuses it in the next test's app.
_compiled_builders = {}
_compile_builder = Rule._compile_builder


def _cached_compile_builder(self, append_unknown=True):
    defaults = self.defaults or {}
    key = (self.rule, self.subdomain, self.host, self.merge_slashes, append_unknown, tuple(self._trace),
           tuple(sorted((str(k), type(self._converters.get(k)), repr(v)) for k, v in defaults.items())))
    builder = _compiled_builders.get(key)
    if builder is None:
        builder = _compiled_builders[key] = _compile_builder(self, append_unknown)
    return builder


Rule._compile_builder = _cached_compile_builder


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
