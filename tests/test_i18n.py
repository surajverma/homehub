import io
import os
import re

import pytest
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

from app import create_app, db
from app import config as config_module
from app import i18n


@pytest.fixture(scope='module', autouse=True)
def compiled_catalogs():
    # .mo files are build output (not in git), so compile them from the .po files for the tests
    for language in os.listdir(i18n.TRANSLATIONS_DIR):
        po_path = os.path.join(i18n.TRANSLATIONS_DIR, language, 'LC_MESSAGES', 'messages.po')
        if not os.path.isfile(po_path):
            continue
        with open(po_path, 'rb') as f:
            catalog = read_po(f, locale=language)
        with open(po_path[:-3] + '.mo', 'wb') as f:
            write_mo(f, catalog)


def make_client(tmp_path, monkeypatch, language, password='secret'):
    cfg = tmp_path / 'config.yml'
    lines = ['instance_name: "Test Hub"', f'password: "{password}"']
    if language is not None:
        lines.append(f'language: {language}')
    cfg.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    monkeypatch.setattr(config_module, 'CONFIG_PATH', str(cfg))
    monkeypatch.setattr(config_module, '_cache', {'key': None, 'config': None})
    app = create_app({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite://',
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'test',
    })
    with app.app_context():
        db.create_all()
    return app.test_client()


def test_login_page_in_hindi(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, 'hi')
    html = client.get('/login').get_data(as_text=True)
    assert '<html lang="hi">' in html
    assert 'साइन इन करें' in html
    assert 'placeholder="पासवर्ड डालें"' in html
    assert 'Sign in' not in html
    assert 'family=Noto+Sans+Devanagari:wght@400;600;700&display=swap' in html
    # Strings used by page scripts reach the browser as JSON
    assert '"lang": "hi"' in html
    assert '"Could not reach the server."' in html

    resp = client.post('/login', data={'password': 'wrong'})
    assert 'गलत पासवर्ड' in resp.get_data(as_text=True)


@pytest.mark.parametrize('language', [None, 'en', 'xx', '../hi', ''])
def test_english_output_is_unchanged(tmp_path, monkeypatch, language):
    client = make_client(tmp_path, monkeypatch, language)
    html = client.get('/login').get_data(as_text=True)
    assert '<html lang="en">' in html
    assert '<h1 class="page-title mb-1">Sign in</h1>' in html
    assert '<p class="text-sm text-gray-500 mb-5">This instance requires a password to access.</p>' in html
    assert '<input type="password" name="password" aria-label="Password" class="w-full" placeholder="Enter password" required>' in html
    assert 'title="Welcome"><i class="fa-solid fa-house w-5 text-center"></i><span class="nav-label">Welcome</span></a>' in html
    assert ('            If you found this useful, consider <a href="https://ko-fi.com/skv" target="_blank" '
            'rel="noopener noreferrer" class="inline-flex items-center gap-1 hover:underline" '
            'style="color: var(--primary-text);"><i class="fa-solid fa-mug-hot" aria-hidden="true"></i> '
            'buying me a coffee</a>\n        </footer>') in html.replace('\r\n', '\n')
    assert '<p class="text-sm text-gray-600 mb-3">Enter the admin password to continue as Administrator.</p>' in html
    assert '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">' in html
    assert 'Noto Sans' not in html
    # English sends no dictionary: t() falls back to the key
    assert 'window.I18N = {"lang": "en", "messages": {}};' in html

    resp = client.post('/login', data={'password': 'wrong'})
    assert 'Invalid password' in resp.get_data(as_text=True)


def test_service_worker_cache_name_follows_language(tmp_path, monkeypatch):
    monkeypatch.setenv('SW_CACHE_VERSION', '9')
    from app import blueprints
    monkeypatch.setattr(blueprints, '_sw_version_cache', [])
    hindi = make_client(tmp_path, monkeypatch, 'hi', password='').get('/sw.js').get_data(as_text=True)
    assert "const CACHE_NAME = 'homehub-v9-hi';" in hindi
    english = make_client(tmp_path, monkeypatch, 'en', password='').get('/sw.js').get_data(as_text=True)
    assert "const CACHE_NAME = 'homehub-v9-en';" in english
    assert 'You are offline' in english


def test_hindi_catalog_is_complete():
    po_path = os.path.join(i18n.TRANSLATIONS_DIR, 'hi', 'LC_MESSAGES', 'messages.po')
    with open(po_path, 'rb') as f:
        catalog = read_po(f, locale='hi')
    untranslated = [m.id for m in catalog if m.id and (not m.string or m.fuzzy)]
    assert untranslated == []
    assert [str(e) for m in catalog if m.id for e in m.check(catalog)] == []


def test_home_page_sentence_with_instance_name(tmp_path, monkeypatch):
    hindi = make_client(tmp_path, monkeypatch, 'hi', password='').get('/').get_data(as_text=True)
    assert 'Test Hub में आपका स्वागत है</h1>' in hindi
    # Stored status values stay English; only the label is translated
    assert '<option value="Home">घर पर</option>' in hindi
    english = make_client(tmp_path, monkeypatch, 'en', password='').get('/').get_data(as_text=True)
    assert 'Welcome to Test Hub</h1>' in english
    assert '<option value="Home">Home</option>' in english
    assert '<span><span id="bulkCount">0</span> selected</span>' in english


def test_page_script_strings_are_found_inside_template_literals():
    source = (
        "row.innerHTML = `<b>${th('Edit')}</b>${ok ? `<i title=\\\"${th(\"Today's list\")}\\\">` : ''}`;\n"
        "toast(tn('{count} item', '{count} items', n)); x = tc('weather', 'Clear'); obj.t('not this');\n"
    )
    found = [(kind, context, text, plural) for _line, kind, context, text, plural in i18n.find_js_messages(source)]
    assert found == [
        ('single', '', 'Edit', None),
        ('single', '', "Today's list", None),
        ('plural', '', '{count} item', '{count} items'),
        ('single', 'weather', 'Clear', None),
    ]


def test_counts_use_singular_and_plural(tmp_path, monkeypatch):
    from datetime import date, timedelta
    from app.models import ExpiryItem
    for language, one, many in (('en', '1 day left', '5 days left'), ('hi', '1 दिन बाकी', '5 दिन बाकी')):
        client = make_client(tmp_path, monkeypatch, language, password='')
        with client.application.app_context():
            db.session.add(ExpiryItem(name='Milk', expiry_date=date.today() + timedelta(days=1), creator='Mom'))
            db.session.add(ExpiryItem(name='Rice', expiry_date=date.today() + timedelta(days=5), creator='Mom'))
            db.session.commit()
        html = client.get('/expiry').get_data(as_text=True)
        assert one in html and many in html
        assert '1 days left' not in html


def _catalog_languages():
    return sorted(
        name for name in os.listdir(i18n.TRANSLATIONS_DIR)
        if os.path.isfile(os.path.join(i18n.TRANSLATIONS_DIR, name, 'LC_MESSAGES', 'messages.po'))
    )


_PLACEHOLDER_RE = re.compile(r'%\(\w+\)[sd]|\{\w+\}')
# Things a translation has no reason to add: markup, links, email addresses, phone-like numbers
_SUSPECT_RE = re.compile(r'<[A-Za-z/!][^>]*>?|https?://\S+|www\.\S+|[\w.+-]+@[\w-]+\.[\w.-]+|\+?\d[\d\s().-]{7,}\d')


@pytest.mark.parametrize('language', _catalog_languages())
def test_translations_are_safe_to_merge(language):
    """Checks a translation file mechanically, so a reviewer who cannot read the language still gets a signal."""
    po_path = os.path.join(i18n.TRANSLATIONS_DIR, language, 'LC_MESSAGES', 'messages.po')
    with open(po_path, 'rb') as f:
        catalog = read_po(f, locale=language)
    write_mo(io.BytesIO(), catalog)
    problems = []
    for message in catalog:
        if not message.id:
            continue
        ids = message.id if isinstance(message.id, (tuple, list)) else (message.id,)
        strings = message.string if isinstance(message.string, (tuple, list)) else (message.string,)
        source = '\n'.join(ids)
        expected = set(_PLACEHOLDER_RE.findall(ids[-1]))
        problems += [f'{ids[0]!r}: {error}' for error in message.check(catalog)]
        for text in strings:
            if not text:
                continue  # untranslated text falls back to English
            found = set(_PLACEHOLDER_RE.findall(text))
            # A plural form may leave the number out ("one day left"), but nothing else
            optional = {'%(num)s', '%(num)d', '{count}'} if len(ids) > 1 else set()
            if found - expected or (expected - found) - optional:
                problems.append(f'{ids[0]!r}: placeholders {sorted(found)} do not match {sorted(expected)}')
            added = [m for m in _SUSPECT_RE.findall(text) if m not in source]
            if added:
                problems.append(f'{ids[0]!r}: adds {added} that the English text does not have')
    assert problems == []
