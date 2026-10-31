import json
import logging
import re

import pytest

from app import create_app, db
from app import config as config_module
from app.config import _contrast, _DARK_CARD, _quote_bare_hex, dark_variant, light_variant


def write_config(tmp_path, monkeypatch, text):
    cfg = tmp_path / 'config.yml'
    cfg.write_text(text, encoding='utf-8')
    monkeypatch.setattr(config_module, 'CONFIG_PATH', str(cfg))
    monkeypatch.setattr(config_module, '_cache', {'key': None, 'config': None})
    return config_module.load_config()


MIXED = '''
admin_name: "Boss"
admin_color: "#0D9488"
family_members:
  - Mom
  - name: Dad
    color: "#7c3aed"
  - name: Kid
    color: "#36f"
  - Gran
'''


def test_family_members_stay_plain_names_and_colours_are_collected(tmp_path, monkeypatch):
    config = write_config(tmp_path, monkeypatch, MIXED)
    assert config['family_members'] == ['Mom', 'Dad', 'Kid', 'Gran']
    colours = config['member_colors']
    assert set(colours) == {'Dad', 'Kid', 'Boss'}
    assert colours['Dad']['light'] == '#7c3aed'
    # Short hex is expanded and everything is lowercase
    assert colours['Kid']['light'] == '#3366ff'
    assert colours['Boss']['light'] == '#0d9488'
    assert len({c['var'] for c in colours.values()}) == 3
    assert 'admin_color' not in config


def test_plain_config_has_no_colours(tmp_path, monkeypatch):
    config = write_config(tmp_path, monkeypatch, 'family_members:\n  - Mom\n  - Dad\n')
    assert config['family_members'] == ['Mom', 'Dad']
    assert config['member_colors'] == {}


def test_bad_entries_are_ignored_with_a_warning(tmp_path, monkeypatch, caplog):
    text = '''
admin_color: "tealish"
family_members:
  - name: Dad
    color: "blurple"
  - name: Kid
    color: "#12345"
  - color: "#123456"
  - Mom
'''
    with caplog.at_level(logging.WARNING, logger='app.config'):
        config = write_config(tmp_path, monkeypatch, text)
    # A bad colour keeps the person, without a colour; an entry without a name is dropped
    assert config['family_members'] == ['Dad', 'Kid', 'Mom']
    assert config['member_colors'] == {}
    messages = ' '.join(r.getMessage() for r in caplog.records)
    assert 'blurple' in messages and '#12345' in messages and 'tealish' in messages and 'without a name' in messages


def test_colour_names_and_unquoted_hex_are_accepted(tmp_path, monkeypatch):
    # Unquoted "#..." would be a YAML comment; it is read as the colour instead
    text = '''
admin_color: #0D9488   # the admin
family_members:
  - name: Dad
    color: #7c3aed
  - name: Kid
    color: Red
  - name: Gran
    color: 'teal'
theme:
  primary_color: #1d4ed8
  sidebar_link_color: "rgba(255,255,255,0.95)"
'''
    config = write_config(tmp_path, monkeypatch, text)
    colours = {name: c['light'] for name, c in config['member_colors'].items()}
    assert colours == {'Dad': '#7c3aed', 'Kid': '#ff0000', 'Gran': '#008080', 'Administrator': '#0d9488'}
    assert config['theme']['primary_color'] == '#1d4ed8'


def test_suraj_config_lines_work_quoted_or_not(tmp_path, monkeypatch):
    for dad in ('#e5e7eb', '"#e5e7eb"'):
        text = f'family_members:\n  - Mom\n  - name: Dad\n    color: {dad}\n  - name: Dipanshu\n    color: red  \n'
        colours = write_config(tmp_path, monkeypatch, text)['member_colors']
        assert set(colours) == {'Dad', 'Dipanshu'}
        # A very pale pick is darkened in light mode so it still shows on the white card
        assert colours['Dad']['light'] != '#e5e7eb' and _contrast(colours['Dad']['light'], '#ffffff') >= 3
        assert colours['Dad']['dark'] == '#e5e7eb'
        assert colours['Dipanshu']['light'] == '#ff0000'


@pytest.mark.parametrize('line, expected', [
    ('color: #e5e7eb', 'color: "#e5e7eb"'),
    ('  - #abc', '  - "#abc"'),
    ('  primary_color: #1D4ED8   # brand blue', '  primary_color: "#1D4ED8"   # brand blue'),
    ('    color: #abcd', '    color: "#abcd"'),
    ('key: #11223344', 'key: "#11223344"'),
    ('  - name: Dad', '  - name: Dad'),
    ('# note', '# note'),
    ('key: # note', 'key: # note'),
    ('key: #bad idea', 'key: #bad idea'),
    ('key: #12345', 'key: #12345'),
    ('key: #abcdefg', 'key: #abcdefg'),
    ('key: "#abc"', 'key: "#abc"'),
    ("key: '#abc'", "key: '#abc'"),
    ('key: #abc#def', 'key: #abc#def'),
    ('password: #abc', 'password: #abc'),
])
def test_quote_bare_hex(line, expected):
    assert _quote_bare_hex(line) == expected


def test_empty_colour_logs_a_hint(tmp_path, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger='app.config'):
        config = write_config(tmp_path, monkeypatch, 'family_members:\n  - name: Dad\n    color:\n')
    assert config['member_colors'] == {}
    assert any('empty' in r.getMessage() for r in caplog.records)


def test_family_members_that_is_not_a_list_does_not_crash(tmp_path, monkeypatch):
    config = write_config(tmp_path, monkeypatch, 'family_members: "Mom"\n')
    assert config['family_members'] == []


@pytest.mark.parametrize('colour', ['#000000', '#1e3a8a', '#7c3aed', '#008300', '#2563eb', '#dc2626'])
def test_dark_variant_is_visible_on_the_dark_card(colour):
    assert _contrast(dark_variant(colour), _DARK_CARD) >= 3


@pytest.mark.parametrize('colour', ['#fbbf24', '#93c5fd', '#ffffff'])
def test_dark_variant_keeps_colours_that_already_stand_out(colour):
    assert dark_variant(colour) == colour


@pytest.mark.parametrize('colour', ['#e5e7eb', '#ffffff', '#fde68a', '#00ff00'])
def test_light_variant_is_visible_on_the_white_card(colour):
    assert _contrast(light_variant(colour), '#ffffff') >= 3


@pytest.mark.parametrize('colour', ['#7c3aed', '#1e3a8a', '#000000', '#dc2626'])
def test_light_variant_keeps_colours_that_already_stand_out(colour):
    assert light_variant(colour) == colour


def make_client():
    app = create_app({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite://',
        'WTF_CSRF_ENABLED': False,
        'SECRET_KEY': 'test',
    })
    with app.app_context():
        db.create_all()
    return app.test_client()


def test_pages_get_plain_names_and_the_colour_variables(tmp_path, monkeypatch):
    write_config(tmp_path, monkeypatch, MIXED + 'feature_toggles:\n  expense_tracker: true\n')
    html = make_client().get('/expenses').get_data(as_text=True)
    family = json.loads(re.search(r'<script id="familyData" type="application/json">(.*?)</script>', html).group(1))
    assert family == ['Mom', 'Dad', 'Kid', 'Gran']
    colours = json.loads(re.search(r'<script id="memberColors" type="application/json">(.*?)</script>', html).group(1))
    assert colours['Dad']['light'] == '#7c3aed'
    style = re.search(r'<style id="member-colour-vars">(.*?)</style>', html, re.S).group(1)
    light, dark = style.strip().split('\n')
    assert f"{colours['Dad']['var']}: #7c3aed;" in light
    assert f"{colours['Dad']['var']}: {colours['Dad']['dark']};" in dark and dark.lstrip().startswith('[data-theme="dark"]')


def test_pages_without_colours_have_no_colour_variables(tmp_path, monkeypatch):
    write_config(tmp_path, monkeypatch, 'family_members:\n  - Mom\nfeature_toggles:\n  expense_tracker: true\n')
    html = make_client().get('/expenses').get_data(as_text=True)
    assert 'member-colour-vars' not in html and 'id="memberColors"' not in html


def test_charts_card_folds_from_its_header():
    html = make_client().get('/expenses').get_data(as_text=True)
    assert 'id="charts-toggle"' in html and 'aria-controls="charts-body"' in html
    # The summary line and budgets sit outside the part that folds away
    body_start = html.index('id="charts-body"')
    assert html.index('id="summary-line"') < body_start < html.index('id="budgets-card"')
