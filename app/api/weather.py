"""API operation for the weather: the same data as the Welcome page widget, never the place it is for."""
from datetime import timezone

from flask_babel import gettext as _

from .. import weather
from . import tokens
from .core import ApiError
from .registry import operation


def _unavailable(reason: str) -> ApiError:
    if reason == 'disabled':
        return ApiError(404, 'not_found', tokens.TOOLS_BY_KEY['weather'].switched_off)
    if reason == 'no_location':
        return ApiError(409, 'conflict', _('Weather has no latitude and longitude in config.yml, so only a browser can look it up for its own location.'))
    return ApiError(502, 'upstream_error', _('The weather service could not be reached. Try again in a few minutes.'))


def _local_time(value, offset: int):
    """Open-Meteo's local time ("2026-11-09T14:00") with its UTC offset, so it names one moment."""
    if not value:
        return None
    sign = '+' if offset >= 0 else '-'
    hours, minutes = divmod(abs(int(offset)) // 60, 60)
    return f'{value}{sign}{hours:02d}:{minutes:02d}'


def _pick(source: dict, index, name):
    """A current value (index None) or one entry of an hourly or daily list."""
    values = source.get(name)
    if index is None:
        return values
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]


def _conditions(source: dict, index, offset: int) -> dict:
    """The current conditions (index None) or one hour of the forecast."""
    def pick(name):
        return _pick(source, index, name)

    code = pick('weather_code')
    is_day = pick('is_day')
    direction = pick('wind_direction_10m')
    entry = {
        'time': _local_time(pick('time'), offset),
        'description': weather.describe(code),
        'weather_code': code,
        'is_day': None if is_day is None else bool(is_day),
        'temperature': pick('temperature_2m'),
        'feels_like': pick('apparent_temperature'),
        'humidity': pick('relative_humidity_2m'),
    }
    if index is None:
        entry.update({
            'dew_point': pick('dew_point_2m'),
            'precipitation': pick('precipitation'),
            'rain': pick('rain'),
            'showers': pick('showers'),
            'snowfall': pick('snowfall'),
            'pressure': pick('pressure_msl'),
        })
    else:
        entry.update({
            'precipitation_probability': pick('precipitation_probability'),
            'precipitation': pick('precipitation'),
        })
    entry.update({
        'cloud_cover': pick('cloud_cover'),
        'visibility': pick('visibility'),
        'uv_index': pick('uv_index'),
        'wind_speed': pick('wind_speed_10m'),
        'wind_gusts': pick('wind_gusts_10m'),
        'wind_direction': direction,
        'wind_direction_compass': weather.compass(direction),
    })
    return entry


def _day(daily: dict, index: int, offset: int) -> dict:
    def pick(name):
        return _pick(daily, index, name)

    code = pick('weather_code')
    direction = pick('wind_direction_10m_dominant')
    daylight = pick('daylight_duration')
    return {
        'date': pick('time'),
        'description': weather.describe(code),
        'weather_code': code,
        'temperature_max': pick('temperature_2m_max'),
        'temperature_min': pick('temperature_2m_min'),
        'feels_like_max': pick('apparent_temperature_max'),
        'feels_like_min': pick('apparent_temperature_min'),
        'precipitation_probability': pick('precipitation_probability_max'),
        'precipitation': pick('precipitation_sum'),
        'rain': pick('rain_sum'),
        'snowfall': pick('snowfall_sum'),
        'precipitation_hours': pick('precipitation_hours'),
        'uv_index_max': pick('uv_index_max'),
        'wind_speed_max': pick('wind_speed_10m_max'),
        'wind_gusts_max': pick('wind_gusts_10m_max'),
        'wind_direction': direction,
        'wind_direction_compass': weather.compass(direction),
        'sunrise': _local_time(pick('sunrise'), offset),
        'sunset': _local_time(pick('sunset'), offset),
        'daylight_hours': round(daylight / 3600, 1) if isinstance(daylight, (int, float)) else None,
    }


def _whole(value):
    return round(value) if isinstance(value, (int, float)) else '-'


def payload(data: dict, fetched_at, imperial: bool) -> dict:
    """Open-Meteo's answer reshaped for the API. Coordinates, elevation and time zone name are left out."""
    offset = data.get('utc_offset_seconds') or 0
    hourly_source = data.get('hourly') or {}
    daily_source = data.get('daily') or {}
    current = _conditions(data.get('current') or {}, None, offset)
    hourly = [_conditions(hourly_source, i, offset) for i in range(len(hourly_source.get('time') or []))]
    daily = [_day(daily_source, i, offset) for i in range(len(daily_source.get('time') or []))]
    degrees = '°F' if imperial else '°C'
    units = {
        'temperature': degrees,
        'wind_speed': 'mph' if imperial else 'km/h',
        'wind_direction': '°',
        'precipitation': 'mm',
        'snowfall': 'cm',
        'humidity': '%',
        'cloud_cover': '%',
        'precipitation_probability': '%',
        'pressure': 'hPa',
        # Open-Meteo switches visibility to feet with imperial units; take its word for it
        'visibility': (data.get('current_units') or {}).get('visibility') or ('ft' if imperial else 'm'),
    }
    summary = _('%(description)s, %(temp)s%(unit)s (feels like %(feels)s%(unit)s).',
                description=current['description'] or '-', temp=_whole(current['temperature']),
                feels=_whole(current['feels_like']), unit=degrees)
    if daily:
        today = daily[0]
        summary += ' ' + _('Today %(low)s to %(high)s%(unit)s, %(chance)s%% chance of rain.',
                           low=_whole(today['temperature_min']), high=_whole(today['temperature_max']),
                           unit=degrees, chance=_whole(today['precipitation_probability']))
    return {
        'fetched_at': fetched_at.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'units': units,
        'current': current,
        'hourly': hourly,
        'daily': daily,
        'summary': summary,
    }


@operation('weather_get', tool='weather', level='read', method='GET', path='/weather',
           title='Weather',
           description=('The weather at home, from Open-Meteo for the place set in config.yml (the same data as the '
                        "Welcome page): current conditions, the next 24 hours and the next 7 days, with descriptions in "
                        'the household language and the units used. HomeHub keeps it for 15 minutes before asking again. '
                        'It never says where the home is and takes no location.'),
           idempotent=True)
def weather_get(ctx, args):
    config = tokens._config()
    try:
        data, fetched_at = weather.forecast(config)
    except weather.WeatherError as exc:
        raise _unavailable(exc.reason)
    return payload(data, fetched_at, weather.imperial(config))
