import copy
import logging
import os
import re
import threading
import yaml
from werkzeug.security import generate_password_hash

log = logging.getLogger(__name__)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.yml')
EXAMPLE_CONFIG_PATH = os.path.join(BASE_DIR, 'config-example.yml')

_cache_lock = threading.Lock()
_cache = {'key': None, 'config': None}
_warned = set()


def _config_source():
    """The file to read: config.yml, or config-example.yml when config.yml is not a usable file.

    Docker creates an empty folder called config.yml when the file is missing from the
    host at first start, so a folder is treated like a missing file.
    """
    if os.path.isfile(CONFIG_PATH):
        return CONFIG_PATH
    problem = 'is a folder, not a file' if os.path.isdir(CONFIG_PATH) else 'is missing'
    fallback = EXAMPLE_CONFIG_PATH if os.path.isfile(EXAMPLE_CONFIG_PATH) else None
    if (CONFIG_PATH, problem) not in _warned:
        _warned.add((CONFIG_PATH, problem))
        log.warning(
            'config.yml %s (%s). HomeHub is running on the defaults from config-example.yml. '
            'Copy config-example.yml to config.yml, edit it, and restart.', problem, CONFIG_PATH)
    return fallback


def load_config():
    """Return the parsed config.yml, re-reading it only when the file changes."""
    source = _config_source()
    if source:
        st = os.stat(source)
        key = (source, st.st_mtime_ns, st.st_size)
    else:
        key = (None,)
    with _cache_lock:
        if _cache['key'] != key:
            _cache['config'] = _parse_config()
            _cache['key'] = key
        # Callers get their own copy, as they did when the file was parsed on every call
        return copy.deepcopy(_cache['config'])


DEFAULT_MAX_UPLOAD_MB = 1024


def upload_limit_bytes(config):
    """Request size limit from config.yml max_upload_mb (0 or less means no limit)."""
    try:
        mb = float(config.get('max_upload_mb', DEFAULT_MAX_UPLOAD_MB))
    except (TypeError, ValueError):
        mb = DEFAULT_MAX_UPLOAD_MB
    return int(mb * 1024 * 1024) if mb > 0 else None


# Theme values that shipped as defaults before the refreshed look. A config.yml that still
# carries them (copied from config-example.yml) gets the new look; custom colours are kept.
_LEGACY_SIDEBAR_BACKGROUNDS = {'#2563eb'}
_LEGACY_THEME_VALUES = {
    'background_color': {'#f7fafc'},
    'text_color': {'#333', '#333333'},
}
_LEGACY_SIDEBAR_VALUES = {
    'sidebar_text_color': {'#ffffff', '#fff'},
    'sidebar_link_color': {'rgba(255,255,255,0.95)'},
    'sidebar_link_border_color': {'rgba(255,255,255,0.18)'},
    'sidebar_active_color': {'#3b82f6'},
    'sidebar_active_text_color': {'#ffffff', '#fff'},
}
_LIGHT_SIDEBAR = {
    'sidebar_text_color': '#0f172a',
    'sidebar_link_color': '#475569',
    'sidebar_link_border_color': 'transparent',
    'sidebar_active_color': 'rgba(var(--primary-rgb), 0.10)',
    'sidebar_active_text_color': 'var(--primary-color)',
}


def _norm_colour(value):
    return str(value or '').replace(' ', '').lower()


_HEX_COLOUR = re.compile(r'^#(?:[0-9a-f]{3}|[0-9a-f]{6})$')
# An unquoted hex value ("key: #e5e7eb" or "- #abc"): YAML reads it as a comment, so the value is lost.
# Only 3, 4, 6 or 8 hex digits, then the end of the line or an inline " # comment"; the password is left alone.
_BARE_HEX_LINE = re.compile(
    r'^(?P<pre>[ \t]*(?:-[ \t]+)?(?!password[ \t]*:)[\w.-]+[ \t]*:[ \t]+|[ \t]*-[ \t]+)'
    r'(?P<hex>#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3}))'
    r'(?P<rest>(?:[ \t]+#.*)?[ \t]*)$',
    re.MULTILINE)


def _quote_bare_hex(text):
    """Quote unquoted hex colours so they reach us as written instead of as YAML comments."""
    return _BARE_HEX_LINE.sub(lambda m: f'{m["pre"]}"{m["hex"]}"{m["rest"]}', text)


# CSS colour names, so config.yml can say "red" as well as "#ff0000"
_NAMED_COLOURS = dict(pair.split('=') for pair in (
    'aliceblue=f0f8ff antiquewhite=faebd7 aqua=00ffff aquamarine=7fffd4 azure=f0ffff beige=f5f5dc bisque=ffe4c4 '
    'black=000000 blanchedalmond=ffebcd blue=0000ff blueviolet=8a2be2 brown=a52a2a burlywood=deb887 '
    'cadetblue=5f9ea0 chartreuse=7fff00 chocolate=d2691e coral=ff7f50 cornflowerblue=6495ed cornsilk=fff8dc '
    'crimson=dc143c cyan=00ffff darkblue=00008b darkcyan=008b8b darkgoldenrod=b8860b darkgray=a9a9a9 '
    'darkgreen=006400 darkgrey=a9a9a9 darkkhaki=bdb76b darkmagenta=8b008b darkolivegreen=556b2f '
    'darkorange=ff8c00 darkorchid=9932cc darkred=8b0000 darksalmon=e9967a darkseagreen=8fbc8f '
    'darkslateblue=483d8b darkslategray=2f4f4f darkslategrey=2f4f4f darkturquoise=00ced1 darkviolet=9400d3 '
    'deeppink=ff1493 deepskyblue=00bfff dimgray=696969 dimgrey=696969 dodgerblue=1e90ff firebrick=b22222 '
    'floralwhite=fffaf0 forestgreen=228b22 fuchsia=ff00ff gainsboro=dcdcdc ghostwhite=f8f8ff gold=ffd700 '
    'goldenrod=daa520 gray=808080 green=008000 greenyellow=adff2f grey=808080 honeydew=f0fff0 hotpink=ff69b4 '
    'indianred=cd5c5c indigo=4b0082 ivory=fffff0 khaki=f0e68c lavender=e6e6fa lavenderblush=fff0f5 '
    'lawngreen=7cfc00 lemonchiffon=fffacd lightblue=add8e6 lightcoral=f08080 lightcyan=e0ffff '
    'lightgoldenrodyellow=fafad2 lightgray=d3d3d3 lightgreen=90ee90 lightgrey=d3d3d3 lightpink=ffb6c1 '
    'lightsalmon=ffa07a lightseagreen=20b2aa lightskyblue=87cefa lightslategray=778899 lightslategrey=778899 '
    'lightsteelblue=b0c4de lightyellow=ffffe0 lime=00ff00 limegreen=32cd32 linen=faf0e6 magenta=ff00ff '
    'maroon=800000 mediumaquamarine=66cdaa mediumblue=0000cd mediumorchid=ba55d3 mediumpurple=9370db '
    'mediumseagreen=3cb371 mediumslateblue=7b68ee mediumspringgreen=00fa9a mediumturquoise=48d1cc '
    'mediumvioletred=c71585 midnightblue=191970 mintcream=f5fffa mistyrose=ffe4e1 moccasin=ffe4b5 '
    'navajowhite=ffdead navy=000080 oldlace=fdf5e6 olive=808000 olivedrab=6b8e23 orange=ffa500 '
    'orangered=ff4500 orchid=da70d6 palegoldenrod=eee8aa palegreen=98fb98 paleturquoise=afeeee '
    'palevioletred=db7093 papayawhip=ffefd5 peachpuff=ffdab9 peru=cd853f pink=ffc0cb plum=dda0dd '
    'powderblue=b0e0e6 purple=800080 rebeccapurple=663399 red=ff0000 rosybrown=bc8f8f royalblue=4169e1 '
    'saddlebrown=8b4513 salmon=fa8072 sandybrown=f4a460 seagreen=2e8b57 seashell=fff5ee sienna=a0522d '
    'silver=c0c0c0 skyblue=87ceeb slateblue=6a5acd slategray=708090 slategrey=708090 snow=fffafa '
    'springgreen=00ff7f steelblue=4682b4 tan=d2b48c teal=008080 thistle=d8bfd8 tomato=ff6347 '
    'turquoise=40e0d0 violet=ee82ee wheat=f5deb3 white=ffffff whitesmoke=f5f5f5 yellow=ffff00 '
    'yellowgreen=9acd32'
).split())
# The card background in dark mode (--card-background-color in input.css)
_DARK_CARD = '#111827'


def _parse_colour(value):
    """'#abc', '#aabbcc' or a CSS colour name as lowercase '#aabbcc', else None."""
    text = str(value or '').strip().lower()
    if text in _NAMED_COLOURS:
        return '#' + _NAMED_COLOURS[text]
    if not _HEX_COLOUR.match(text):
        return None
    if len(text) == 4:
        text = '#' + ''.join(c * 2 for c in text[1:])
    return text


def _hex_colour(value, what):
    """_parse_colour, logging anything that is not a colour."""
    if value in (None, ''):
        return None
    colour = _parse_colour(value)
    if not colour:
        log.warning('Ignoring the colour %r for %s in config.yml: use a colour name such as "teal" '
                    'or a hex colour such as "#2563eb".', value, what)
    return colour


def _luminance(hex_colour):
    def channel(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _shade_for(hex_colour, card, towards):
    """The colour mixed towards white (255) or black (0) just enough to stand out on the card
    (3:1, as for chart marks); unchanged when it already does."""
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    colour = hex_colour
    for step in range(1, 21):
        if _contrast(colour, card) >= 3:
            break
        mix = step / 20
        colour = '#' + ''.join(f'{round(c + (towards - c) * mix):02x}' for c in (r, g, b))
    return colour


def dark_variant(hex_colour):
    """The colour as shown in dark mode: lightened if needed for the dark card."""
    return _shade_for(hex_colour, _DARK_CARD, 255)


def light_variant(hex_colour, card='#ffffff'):
    """The colour as shown in light mode: darkened if needed, so a pale pick still shows on the card."""
    return _shade_for(hex_colour, card, 0)


def _apply_member_colours(config):
    """family_members entries are names or {name, color}; keep plain names and collect the colours.

    Everything else reads config['family_members'] as a list of names, so the colours go to
    config['member_colors'] ({name: {'light', 'dark', 'var'}}, 'var' being the CSS variable base.html
    sets), with the admin's from admin_color.
    """
    names, colours = [], {}
    members = config.get('family_members')
    for entry in members if isinstance(members, list) else []:
        colour = None
        if isinstance(entry, dict):
            name = str(entry.get('name') or '').strip()
            if not name:
                log.warning('Ignoring a family_members entry without a name in config.yml: %r', entry)
                continue
            if 'color' in entry and entry['color'] in (None, ''):
                log.warning('The colour for %s in config.yml is empty, so the default is used. '
                            'Write a colour name or a hex colour, for example color: "#7c3aed".', name)
            colour = _hex_colour(entry.get('color'), name)
        else:
            name = str(entry).strip()
            if not name:
                continue
        names.append(name)
        if colour:
            colours[name] = colour
    config['family_members'] = names
    admin_colour = _hex_colour(config.pop('admin_color', None), 'admin_color')
    if admin_colour:
        colours[config['admin_name']] = admin_colour
    # Light mode is checked against the configured card colour (white unless the theme changes it)
    card = _parse_colour((config.get('theme') or {}).get('card_background_color')) or '#ffffff'
    config['member_colors'] = {
        name: {'light': light_variant(c, card), 'dark': dark_variant(c), 'var': f'--member-{i}'}
        for i, (name, c) in enumerate(colours.items(), start=1)
    }


def _apply_theme_defaults(theme):
    for key, legacy in _LEGACY_THEME_VALUES.items():
        if _norm_colour(theme.get(key)) in legacy:
            theme.pop(key)
    theme.setdefault('primary_color', '#1d4ed8')
    theme.setdefault('secondary_color', '#a0aec0')
    theme.setdefault('background_color', '#f8fafc')
    theme.setdefault('card_background_color', '#ffffff')
    theme.setdefault('text_color', '#0f172a')
    custom_sidebar = bool(theme.get('sidebar_background_color')) and \
        _norm_colour(theme.get('sidebar_background_color')) not in _LEGACY_SIDEBAR_BACKGROUNDS
    if custom_sidebar:
        # A coloured sidebar the user picked: keep the previous light-on-colour link styling
        theme.setdefault('sidebar_text_color', '#ffffff')
        theme.setdefault('sidebar_link_color', 'rgba(255,255,255,0.95)')
        theme.setdefault('sidebar_link_border_color', 'rgba(255,255,255,0.18)')
        theme.setdefault('sidebar_active_color', '#3b82f6')
        theme.setdefault('sidebar_active_text_color', '#ffffff')
    else:
        # New light sidebar. Keys still set to the old example values get the new defaults;
        # anything else the user customised is kept.
        theme['sidebar_background_color'] = '#ffffff'
        for key, default in _LIGHT_SIDEBAR.items():
            value = theme.get(key)
            if not value or _norm_colour(value) in _LEGACY_SIDEBAR_VALUES.get(key, set()):
                theme[key] = default
    return theme


def _parse_config():
    source = _config_source()
    config = {}
    if source:
        with open(source, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(_quote_bare_hex(f.read())) or {}
    # config.yml holds the site password as written; in memory only a salted hash is kept
    password = config.pop('password', None)
    if password:
        config['password_hash'] = generate_password_hash(str(password), method='pbkdf2:sha256')
    # Ensure feature_toggles exists
    config.setdefault('feature_toggles', {})
    # Ensure Who is Home widget is enabled by default unless explicitly disabled in config.yml
    config['feature_toggles'].setdefault('who_is_home', True)
    # Personal status feature toggle (new)
    config['feature_toggles'].setdefault('personal_status', True)
    # Homepage chores widget toggle (runtime value may be overridden in app_setting)
    config['feature_toggles'].setdefault('show_chores_on_homepage', False)
    # Reminders defaults & calendar start day (supports sunday..saturday or 0-6)
    rem = config.setdefault('reminders', {})
    # Do not overwrite existing user value
    if 'calendar_start_day' not in rem or rem.get('calendar_start_day') in (None, ''):
        rem.setdefault('calendar_start_day', 'sunday')  # default Sunday to align with expense tracker
    # Admin name default
    config.setdefault('admin_name', 'Administrator')
    # Family members default list; entries may carry a chart colour
    config.setdefault('family_members', [])
    # UI language; anything without a translation falls back to English
    config.setdefault('language', 'en')
    _apply_theme_defaults(config.setdefault('theme', {}))
    _apply_member_colours(config)
    # Weather widget defaults
    weather = config.setdefault('weather', {})
    weather.setdefault('enabled', False)
    weather.setdefault('label', '')
    weather.setdefault('latitude', '')
    weather.setdefault('longitude', '')
    weather.setdefault('timezone', '')
    weather.setdefault('units', 'metric')
    weather.setdefault('view', 'compact')
    return config
