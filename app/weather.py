"""The household's weather, fetched from Open-Meteo for the place set in config.yml.

The Welcome page widget (through /api/weather) and the API (/api/v1/weather) both read it here,
so they share one cache: Open-Meteo is asked again only when the cached data is 15 minutes old.
"""
import threading
from datetime import datetime, timedelta, timezone

import requests
from flask import current_app
from flask_babel import gettext as _
from flask_babel import pgettext

FORECAST_URL = 'https://api.open-meteo.com/v1/forecast'
CACHE_SECONDS = 15 * 60
TIMEOUT_SECONDS = 10
HOURS = 24
DAYS = 7

CURRENT_FIELDS = ('is_day', 'weather_code', 'temperature_2m', 'apparent_temperature', 'relative_humidity_2m',
                  'dew_point_2m', 'precipitation', 'rain', 'showers', 'snowfall', 'cloud_cover', 'pressure_msl',
                  'visibility', 'uv_index', 'wind_speed_10m', 'wind_direction_10m', 'wind_gusts_10m')
HOURLY_FIELDS = ('is_day', 'weather_code', 'temperature_2m', 'apparent_temperature', 'relative_humidity_2m',
                 'precipitation_probability', 'precipitation', 'cloud_cover', 'visibility', 'uv_index',
                 'wind_speed_10m', 'wind_direction_10m', 'wind_gusts_10m')
DAILY_FIELDS = ('weather_code', 'temperature_2m_max', 'temperature_2m_min', 'apparent_temperature_max',
                'apparent_temperature_min', 'precipitation_probability_max', 'precipitation_sum', 'rain_sum',
                'snowfall_sum', 'precipitation_hours', 'uv_index_max', 'wind_speed_10m_max', 'wind_gusts_10m_max',
                'wind_direction_10m_dominant', 'sunrise', 'sunset', 'daylight_duration')

# Keyed by place, units and timezone: {key: (data, fetched_at)}
_cache: dict = {}
_lock = threading.Lock()


class WeatherError(Exception):
    """Weather cannot be shown; ``reason`` is 'disabled', 'no_location' or 'unavailable'."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def settings(config: dict) -> dict:
    return config.get('weather') or {}


def is_enabled(config: dict) -> bool:
    return bool(settings(config).get('enabled'))


def coordinates(config: dict) -> tuple | None:
    """(latitude, longitude) from config.yml, or None when they are missing or not valid."""
    weather = settings(config)
    try:
        lat = float(weather.get('latitude'))
        lon = float(weather.get('longitude'))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def imperial(config: dict) -> bool:
    return str(settings(config).get('units') or 'metric').lower() == 'imperial'


def request_params(config: dict) -> dict:
    """What HomeHub asks Open-Meteo for: everything the widget and the API use."""
    lat, lon = coordinates(config)
    return {
        'latitude': lat,
        'longitude': lon,
        'current': ','.join(CURRENT_FIELDS),
        'hourly': ','.join(HOURLY_FIELDS),
        'daily': ','.join(DAILY_FIELDS),
        'forecast_days': DAYS,
        'forecast_hours': HOURS,
        'temperature_unit': 'fahrenheit' if imperial(config) else 'celsius',
        'wind_speed_unit': 'mph' if imperial(config) else 'kmh',
        'precipitation_unit': 'mm',
        'timezone': str(settings(config).get('timezone') or '').strip() or 'auto',
    }


def _cache_key(params: dict) -> str:
    return f"{params['latitude']:.3f},{params['longitude']:.3f}|{params['temperature_unit']}|{params['timezone']}"


def _is_fresh(data: dict, fetched_at: datetime, now: datetime) -> bool:
    """Fresh while Open-Meteo's own time for the current conditions is less than 15 minutes old.

    Its "current" time steps every 15 minutes, so data fetched just before a step is refreshed
    at the step. A fetch that is somehow older than that by our own clock never counts as fresh.
    """
    if (now - fetched_at).total_seconds() >= CACHE_SECONDS:
        return False
    current_time = (data.get('current') or {}).get('time')
    if not current_time:
        return True
    try:
        local = datetime.fromisoformat(current_time)
    except ValueError:
        return True
    if local.tzinfo is None:
        local = (local - timedelta(seconds=data.get('utc_offset_seconds') or 0)).replace(tzinfo=timezone.utc)
    age = (now - local).total_seconds()
    return 0 <= age < CACHE_SECONDS


def forecast(config: dict, now: datetime | None = None) -> tuple:
    """(Open-Meteo's answer, when HomeHub fetched it as an aware UTC datetime). Raises WeatherError."""
    if not is_enabled(config):
        raise WeatherError('disabled')
    if coordinates(config) is None:
        raise WeatherError('no_location')
    params = request_params(config)
    key = _cache_key(params)
    now = now or datetime.now(timezone.utc)
    with _lock:
        cached = _cache.get(key)
    if cached and _is_fresh(cached[0], cached[1], now):
        return cached
    try:
        response = requests.get(FORECAST_URL, params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get('current'), dict):
            raise ValueError('no current conditions in the answer')
    except (requests.RequestException, ValueError) as exc:
        current_app.logger.warning('Weather fetch failed: %s', exc)
        raise WeatherError('unavailable')
    entry = (data, now)
    with _lock:
        _cache[key] = entry
        # Only a changed config.yml makes a new key; keep the few most recent
        for old in list(_cache)[:-5]:
            _cache.pop(old, None)
    return entry


def clear_cache() -> None:
    with _lock:
        _cache.clear()


# --- Describing it ---

def _codes() -> dict:
    # The same words as the widget (static/js/weather.js), so they are translated once
    return {
        0: pgettext('weather', 'Clear'), 1: _('Mainly Clear'), 2: _('Partly Cloudy'), 3: _('Overcast'),
        45: _('Fog'), 48: _('Freezing Fog'),
        51: _('Light Drizzle'), 53: _('Drizzle'), 55: _('Heavy Drizzle'),
        61: _('Light Rain'), 63: _('Rain'), 65: _('Heavy Rain'),
        71: _('Light Snow'), 73: _('Snow'), 75: _('Heavy Snow'), 77: _('Snow Grains'),
        80: _('Light Showers'), 81: _('Showers'), 82: _('Heavy Showers'),
        85: _('Snow Showers'), 86: _('Heavy Snow Showers'),
        95: _('Thunderstorm'), 96: _('Thunderstorm + Hail'), 99: _('Thunderstorm + Hail'),
    }


def describe(code) -> str | None:
    if code is None:
        return None
    return _codes().get(int(code), _('Unknown'))


COMPASS = ('N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW')


def compass(degrees) -> str | None:
    if degrees is None:
        return None
    return COMPASS[round(float(degrees) / 22.5) % 16]
