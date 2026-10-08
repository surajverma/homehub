import os
import re
import threading

import click
from markupsafe import escape
from babel.dates import get_day_names, get_month_names
from flask import current_app
from flask.cli import AppGroup
from flask_babel import Babel, get_translations, gettext, ngettext, npgettext, pgettext

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
TRANSLATIONS_DIR = os.path.join(BASE_DIR, 'translations')
JS_DIR = os.path.join(BASE_DIR, 'static', 'js')

DEFAULT_LANGUAGE = 'en'
# Google Fonts family for scripts Inter does not cover; loaded only for these languages
LANGUAGE_FONTS = {'hi': 'Noto Sans Devanagari'}
# gettext joins a message's context and text with this character
CONTEXT_SEPARATOR = '\x04'

_LANGUAGE_RE = re.compile(r'^[a-z]{2,3}(_[A-Za-z0-9]{2,8})?$')

# Calls page scripts make for a translated string: t('text'), th('text'), tn('one', 'other', n), tc('context', 'text').
# A plain pattern rather than a JavaScript parser, so it also finds calls inside nested template literals.
_JS_STRING = r"""'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*\""""
_JS_CALL_RE = re.compile(r'(?<![\w$.])(t|th|tn|tc)\(\s*(%s)(?:\s*,\s*(%s))?' % (_JS_STRING, _JS_STRING))
_JS_ESCAPE_RE = re.compile(r'\\(u[0-9a-fA-F]{4}|.)')
_JS_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r'}

babel = Babel()

_js_lock = threading.Lock()
_js_messages = []


def active_language():
    """Language from config.yml; English unless a compiled catalog exists for the configured one."""
    cfg = current_app.config.get('HOMEHUB_CONFIG') or {}
    language = str(cfg.get('language') or DEFAULT_LANGUAGE).strip().replace('-', '_')
    if language == DEFAULT_LANGUAGE or not _LANGUAGE_RE.match(language):
        return DEFAULT_LANGUAGE
    if not os.path.isfile(os.path.join(TRANSLATIONS_DIR, language, 'LC_MESSAGES', 'messages.mo')):
        return DEFAULT_LANGUAGE
    return language


def _js_string_value(literal):
    def unescape(match):
        code = match.group(1)
        if code[0] == 'u' and len(code) == 5:
            return chr(int(code[1:], 16))
        return _JS_ESCAPES.get(code, code)
    return _JS_ESCAPE_RE.sub(unescape, literal[1:-1])


def find_js_messages(source):
    """Yield (line, kind, context, text, plural) for each translation call in a page script."""
    for match in _JS_CALL_RE.finditer(source):
        name, first, second = match.group(1), match.group(2), match.group(3)
        line = source.count('\n', 0, match.start()) + 1
        first = _js_string_value(first)
        second = _js_string_value(second) if second else None
        if name == 'tn':
            if second is not None:
                yield line, 'plural', '', first, second
        elif name == 'tc':
            if second is not None:
                yield line, 'single', first, second, None
        else:
            yield line, 'single', '', first, None


def extract_js(fileobj, keywords, comment_tags, options):
    """Babel extraction method for page scripts (see babel.cfg)."""
    source = fileobj.read().decode(options.get('encoding', 'utf-8'))
    for line, kind, context, text, plural in find_js_messages(source):
        if kind == 'plural':
            yield line, 'ngettext', (text, plural), []
        elif context:
            yield line, 'pgettext', (context, text), []
        else:
            yield line, 'gettext', text, []


def _scan_js_messages():
    """Strings page scripts ask for. Read once per process; the files ship with the image."""
    with _js_lock:
        if _js_messages:
            return _js_messages[0]
        found = set()
        for root, _dirs, files in os.walk(JS_DIR):
            for name in files:
                if not name.endswith('.js'):
                    continue
                try:
                    with open(os.path.join(root, name), encoding='utf-8') as f:
                        for _line, kind, context, text, _plural in find_js_messages(f.read()):
                            found.add((kind, context, text))
                except (OSError, UnicodeDecodeError):
                    current_app.logger.exception('Could not read translatable strings from %s', name)
        _js_messages.append(sorted(found))
        return _js_messages[0]


def js_catalog():
    """Translations for the strings page scripts use, keyed by the English text.

    Plural entries are a list of forms; tc() entries are keyed context + separator + text, as gettext does.
    English needs no entries: t() falls back to the key.
    """
    language = active_language()
    messages = {}
    if language != DEFAULT_LANGUAGE:
        catalog = getattr(get_translations(), '_catalog', {})
        for kind, context, text in _scan_js_messages():
            key = context + CONTEXT_SEPARATOR + text if context else text
            if kind == 'plural':
                forms = []
                while (key, len(forms)) in catalog:
                    forms.append(catalog[(key, len(forms))])
                if forms and all(forms):
                    messages[key] = forms
            else:
                translated = catalog.get(key)
                if translated:
                    messages[key] = translated
    return {'lang': language.replace('_', '-'), 'messages': messages}


_NAMED_DATE_PARTS_RE = re.compile(r'(%[aAbB])')


def format_local_datetime(value, fmt):
    """strftime, with month and weekday names in the active language (digits are left as they are)."""
    language = active_language()
    if language == DEFAULT_LANGUAGE:
        return value.strftime(fmt)
    names = {
        '%a': get_day_names('abbreviated', locale=language)[value.weekday()],
        '%A': get_day_names('wide', locale=language)[value.weekday()],
        '%b': get_month_names('abbreviated', locale=language)[value.month],
        '%B': get_month_names('wide', locale=language)[value.month],
    }
    return ''.join(names.get(part) or value.strftime(part) for part in _NAMED_DATE_PARTS_RE.split(fmt) if part)


def html_params(text, **params):
    """Template filter: escape translated text, then put HTML (a link, a tag) into its %(name)s placeholders.

    {{ _('Made by %(link)s')|html_params(link=some_markup) }}; values that are not Markup are escaped too.
    """
    result = escape(text)
    for name, value in params.items():
        result = result.replace('%%(%s)s' % name, escape(value))
    return result


translations_cli = AppGroup('translations', help='Maintain the translation catalogs in translations/.')


def _pybabel(*args):
    from babel.messages.frontend import CommandLineInterface
    CommandLineInterface().run(['pybabel', *args])


@translations_cli.command('update')
def update_translations():
    """Collect the strings marked in the code and bring every language's .po file up to date."""
    pot_path = os.path.join('translations', 'messages.pot')
    previous = os.getcwd()
    os.chdir(BASE_DIR)
    try:
        _pybabel('extract', '-F', 'babel.cfg', '--no-wrap', '--sort-by-file', '--add-location=file',
                 '--project=HomeHub', '--copyright-holder=HomeHub contributors', '-o', pot_path, '.')
        # Drop the placeholder lines Babel writes so the template stays tidy in git
        with open(pot_path, encoding='utf-8') as f:
            lines = f.readlines()
        with open(pot_path, 'w', encoding='utf-8', newline='\n') as f:
            f.writelines(
                '#\n' if line.startswith('# FIRST AUTHOR') else line.replace('HomeHub VERSION', 'HomeHub')
                for line in lines
                if not line.startswith(('"Report-Msgid-Bugs-To:', '"Last-Translator:'))
            )
        _pybabel('update', '-i', pot_path, '-d', 'translations', '--no-wrap', '--no-fuzzy-matching')
    finally:
        os.chdir(previous)
    click.echo('Catalogs updated. Translate the empty msgstr entries, then rebuild the image.')


@translations_cli.command('compile')
def compile_translations():
    """Compile the .po files into the .mo files the app reads (the Docker build does this for you)."""
    _pybabel('compile', '-d', TRANSLATIONS_DIR)


def init_app(app):
    app.config.setdefault('BABEL_DEFAULT_LOCALE', DEFAULT_LANGUAGE)
    app.config.setdefault('BABEL_TRANSLATION_DIRECTORIES', TRANSLATIONS_DIR)
    babel.init_app(app, locale_selector=active_language)
    # Templates get plain strings from _(), so Jinja escapes translated text like any other value
    app.jinja_env.install_gettext_callables(gettext, ngettext, newstyle=False, pgettext=pgettext, npgettext=npgettext)
    app.cli.add_command(translations_cli)
    app.add_template_filter(html_params)

    @app.context_processor
    def inject_i18n():
        catalog = js_catalog()
        return {
            'html_lang': catalog['lang'],
            'i18n_js': catalog,
            'language_font': LANGUAGE_FONTS.get(active_language()),
        }
