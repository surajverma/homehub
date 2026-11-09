"""The weather tool: the Welcome page's weather through the API, never the place it is for."""
import json
from datetime import datetime, timedelta, timezone

import pytest
import requests

from api_support import Home
from app import weather
from app.api import tokens
from app.blueprints import api_admin

PLACE = {'enabled': True, 'label': 'Our flat in Pune', 'latitude': '18.5204', 'longitude': '73.8567',
         'timezone': 'Asia/Kolkata', 'units': 'metric', 'view': 'compact'}


def open_meteo_answer(now=None):
    """What Open-Meteo sends back, shaped like the real thing (its current time steps every 15 minutes)."""
    now = (now or datetime.now(timezone.utc)) + timedelta(hours=5, minutes=30)
    step = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
    hours = [(step.replace(minute=0) + timedelta(hours=h)).strftime('%Y-%m-%dT%H:%M') for h in range(24)]
    days = [(step.date() + timedelta(days=d)).isoformat() for d in range(7)]
    return {
        'latitude': 18.5, 'longitude': 73.875, 'elevation': 560.0, 'timezone': 'Asia/Kolkata',
        'timezone_abbreviation': 'GMT+5:30', 'utc_offset_seconds': 19800, 'generationtime_ms': 0.1,
        'current_units': {'temperature_2m': '°C'},
        'current': {
            'time': step.strftime('%Y-%m-%dT%H:%M'), 'interval': 900, 'is_day': 1, 'weather_code': 2,
            'temperature_2m': 27.4, 'apparent_temperature': 29.1, 'relative_humidity_2m': 61, 'dew_point_2m': 19.2,
            'precipitation': 0.0, 'rain': 0.0, 'showers': 0.0, 'snowfall': 0.0, 'cloud_cover': 40,
            'pressure_msl': 1011.2, 'visibility': 24140.0, 'uv_index': 5.3, 'wind_speed_10m': 11.2,
            'wind_direction_10m': 225, 'wind_gusts_10m': 20.5,
        },
        'hourly': {
            'time': hours, 'is_day': [1] * 24, 'weather_code': [3] * 24, 'temperature_2m': [26.0] * 24,
            'apparent_temperature': [27.0] * 24, 'relative_humidity_2m': [60] * 24,
            'precipitation_probability': [10] * 24, 'precipitation': [0.0] * 24, 'cloud_cover': [80] * 24,
            'visibility': [20000.0] * 24, 'uv_index': [4.0] * 24, 'wind_speed_10m': [9.0] * 24,
            'wind_direction_10m': [90] * 24, 'wind_gusts_10m': [15.0] * 24,
        },
        'daily': {
            'time': days, 'weather_code': [61] * 7, 'temperature_2m_max': [31.2] * 7, 'temperature_2m_min': [19.6] * 7,
            'apparent_temperature_max': [33.0] * 7, 'apparent_temperature_min': [19.0] * 7,
            'precipitation_probability_max': [35] * 7, 'precipitation_sum': [1.4] * 7, 'rain_sum': [1.4] * 7,
            'snowfall_sum': [0.0] * 7, 'precipitation_hours': [2.0] * 7, 'uv_index_max': [8.1] * 7,
            'wind_speed_10m_max': [18.0] * 7, 'wind_gusts_10m_max': [30.0] * 7, 'wind_direction_10m_dominant': [270] * 7,
            'sunrise': [f'{d}T06:42' for d in days], 'sunset': [f'{d}T17:58' for d in days],
            'daylight_duration': [40560.0] * 7,
        },
    }


class FakeResponse:
    def __init__(self, data, status=200):
        self.data = data
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f'{self.status}')

    def json(self):
        return self.data


@pytest.fixture()
def calls(monkeypatch):
    """Open-Meteo stand-in: records each request's parameters."""
    made = []

    def fake_get(url, params=None, timeout=None):
        made.append({'url': url, 'params': dict(params or {})})
        return FakeResponse(open_meteo_answer())

    monkeypatch.setattr(weather.requests, 'get', fake_get)
    weather.clear_cache()
    yield made
    weather.clear_cache()


@pytest.fixture()
def home(tmp_path, monkeypatch, calls):
    return Home(tmp_path, monkeypatch, weather=dict(PLACE))


def test_weather_reads_everything(home, calls):
    secret = home.token('Hermes', weather='read')
    resp = home.get('/weather', secret)
    assert resp.status_code == 200 and resp.headers['Cache-Control'] == 'no-store'
    data = resp.get_json()
    assert set(data) == {'fetched_at', 'units', 'current', 'hourly', 'daily', 'summary'}
    current = data['current']
    assert current['description'] == 'Partly Cloudy' and current['weather_code'] == 2 and current['is_day'] is True
    assert (current['temperature'], current['feels_like'], current['humidity']) == (27.4, 29.1, 61)
    assert (current['uv_index'], current['wind_speed'], current['wind_gusts']) == (5.3, 11.2, 20.5)
    assert current['wind_direction'] == 225 and current['wind_direction_compass'] == 'SW'
    assert current['time'].endswith('+05:30')
    assert len(data['hourly']) == 24 and data['hourly'][0]['precipitation_probability'] == 10
    assert data['hourly'][0]['description'] == 'Overcast'
    assert len(data['daily']) == 7
    day = data['daily'][0]
    assert (day['temperature_max'], day['temperature_min'], day['precipitation_probability']) == (31.2, 19.6, 35)
    assert (day['precipitation'], day['uv_index_max'], day['wind_speed_max']) == (1.4, 8.1, 18.0)
    assert day['sunrise'].endswith('T06:42+05:30') and day['sunset'].endswith('T17:58+05:30')
    assert day['description'] == 'Light Rain' and day['daylight_hours'] == 11.3
    assert data['units'] == {'temperature': '°C', 'wind_speed': 'km/h', 'wind_direction': '°', 'precipitation': 'mm',
                             'snowfall': 'cm', 'humidity': '%', 'cloud_cover': '%', 'precipitation_probability': '%',
                             'pressure': 'hPa', 'visibility': 'm'}
    assert data['summary'] == 'Partly Cloudy, 27°C (feels like 29°C). Today 20 to 31°C, 35% chance of rain.'
    assert data['fetched_at'].endswith('Z')


def test_weather_never_tells_where_home_is(home, calls):
    secret = home.token('Hermes', weather='read')
    text = home.get('/weather', secret).get_data(as_text=True)
    for leak in ('18.5', '73.8', 'Pune', 'Kolkata', 'latitude', 'longitude', 'elevation', 'label', 'timezone'):
        assert leak not in text
    # Open-Meteo is asked for the configured place, with everything both readers need
    params = calls[0]['params']
    assert (params['latitude'], params['longitude']) == (18.5204, 73.8567)
    assert params['forecast_days'] == 7 and params['forecast_hours'] == 24 and params['timezone'] == 'Asia/Kolkata'
    assert {'uv_index', 'apparent_temperature'} <= set(params['current'].split(','))
    assert {'sunrise', 'precipitation_probability_max', 'uv_index_max'} <= set(params['daily'].split(','))


def test_weather_takes_no_location(home, calls):
    secret = home.token('Hermes', weather='read')
    for query in ('latitude=1&longitude=2', 'city=Paris'):
        resp = home.get(f'/weather?{query}', secret)
        assert resp.status_code == 400 and 'not a known field' in resp.get_json()['message']
    assert calls == []


def test_api_and_widget_share_one_fetch(home, calls):
    secret = home.token('Hermes', weather='read')
    assert home.get('/weather', secret).status_code == 200
    widget = home.client.get('/api/weather')
    assert widget.status_code == 200 and widget.get_json()['current']['temperature_2m'] == 27.4
    assert 'daily' in widget.get_json()
    assert home.get('/weather', secret).status_code == 200
    assert len(calls) == 1


def test_cache_runs_out_after_15_minutes(home, calls, monkeypatch):
    secret = home.token('Hermes', weather='read')
    home.get('/weather', secret)
    key = next(iter(weather._cache))
    data, fetched_at = weather._cache[key]
    weather._cache[key] = (data, fetched_at - timedelta(minutes=16))
    home.get('/weather', secret)
    assert len(calls) == 2


def test_imperial_units(home, calls):
    home.set(weather={**PLACE, 'units': 'imperial'})
    secret = home.token('Hermes', weather='read')
    data = home.get('/weather', secret).get_json()
    assert data['units']['temperature'] == '°F' and data['units']['wind_speed'] == 'mph'
    assert calls[0]['params']['temperature_unit'] == 'fahrenheit' and calls[0]['params']['wind_speed_unit'] == 'mph'


def test_weather_switched_off(home, calls):
    secret = home.token('Hermes', weather='read', notice='read')
    home.set(weather={**PLACE, 'enabled': False})
    resp = home.get('/weather', secret)
    assert resp.status_code == 404 and resp.get_json()['message'] == 'Weather is switched off in config.yml.'
    assert 'weather' not in home.get('/', secret).get_json()['tools']
    assert calls == []


def test_weather_without_coordinates(home, calls):
    secret = home.token('Hermes', weather='read')
    home.set(weather={**PLACE, 'latitude': '', 'longitude': ''})
    resp = home.get('/weather', secret)
    assert resp.status_code == 409 and 'no latitude and longitude' in resp.get_json()['message']
    assert calls == []


def test_open_meteo_down(home, monkeypatch):
    def broken(*args, **kwargs):
        raise requests.ConnectionError('down')
    monkeypatch.setattr(weather.requests, 'get', broken)
    secret = home.token('Hermes', weather='read')
    resp = home.get('/weather', secret)
    assert resp.status_code == 502 and resp.get_json()['error'] == 'upstream_error'


def test_weather_needs_the_tool(home):
    secret = home.token('Hermes', notice='read')
    assert home.get('/weather', secret).status_code == 403


def test_weather_is_read_only(home):
    with home.ctx():
        with pytest.raises(tokens.TokenError):
            tokens.create_token('X', {'weather': 'write'})
    assert tokens.TOOLS_BY_KEY['weather'].levels == ('read',)


def test_weather_tool_follows_weather_enabled(home):
    assert tokens.tool_enabled('weather', {'weather': {'enabled': True}})
    assert not tokens.tool_enabled('weather', {'weather': {'enabled': False}})
    assert not tokens.tool_enabled('weather', {})


def test_presets_include_weather(home):
    presets = api_admin.presets([t.key for t in tokens.TOOLS])
    for name in ('read_all', 'home_automation', 'ai_assistant'):
        assert presets[name]['permissions']['weather'] == 'read'
    assert 'weather' not in api_admin.presets(['notice'])['home_automation']['permissions']


def test_weather_over_mcp(home, calls):
    secret = home.token('Hermes', weather='read')
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
    headers = {'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-06-18'}
    tools = {t['name']: t for t in home.post('/mcp', secret, json=body, headers=headers).get_json()['result']['tools']}
    assert tools['weather_get']['annotations']['readOnlyHint'] is True
    assert tools['weather_get']['inputSchema']['properties'] == {}
    body = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'weather_get', 'arguments': {}}}
    result = home.post('/mcp', secret, json=body, headers=headers).get_json()['result']
    assert result['isError'] is False
    assert json.loads(result['content'][0]['text'])['current']['description'] == 'Partly Cloudy'


def test_widget_path_still_proxies_other_places(home, calls):
    resp = home.client.get('/api/weather?latitude=48.85&longitude=2.35&current=temperature_2m')
    assert resp.status_code == 200
    assert calls[0]['params']['latitude'] == '48.85'
    # Half a place is still refused
    assert home.client.get('/api/weather?latitude=48.85').status_code == 400


def test_widget_without_place_when_switched_off(home, calls):
    home.set(weather={**PLACE, 'enabled': False})
    assert home.client.get('/api/weather').status_code == 404
