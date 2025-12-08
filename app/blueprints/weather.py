from flask import jsonify, request, current_app
import requests
from ..blueprints import main_bp
from datetime import datetime, timezone


# In-memory cache for weather data
_weather_cache = {}


@main_bp.route('/api/weather', methods=['GET'])
def weather_proxy():
    """
    Server-side proxy for Open-Meteo API to bypass CORS restrictions.
    Implements caching based on API's timestamp to reduce external API calls.
    """
    try:
        # Get all query parameters from the client request
        params = request.args.to_dict()
        
        # Validate required parameters
        if 'latitude' not in params or 'longitude' not in params:
            return jsonify({'error': 'Missing latitude or longitude parameter'}), 400
        
        # Validate coordinate ranges
        try:
            lat = float(params['latitude'])
            lon = float(params['longitude'])
            if lat < -90 or lat > 90 or lon < -180 or lon > 180:
                return jsonify({'error': 'Invalid coordinates. Latitude must be -90 to 90, longitude -180 to 180.'}), 400
        except ValueError:
            return jsonify({'error': 'Invalid coordinate format'}), 400
        
        # Create cache key from coordinates and relevant parameters
        cache_key = f"{lat:.3f},{lon:.3f}|{params.get('current','')}|{params.get('daily','')}|{params.get('timezone','auto')}"
        
        # Check cache first
        if cache_key in _weather_cache:
            cached_data = _weather_cache[cache_key]
            
            # Use API's timestamp from cached response
            api_time_str = cached_data.get('current', {}).get('time')
            if api_time_str:
                try:
                    # Parse API time (ISO8601 format without explicit timezone)
                    # API returns time in the requested timezone or UTC if auto
                    api_time = datetime.fromisoformat(api_time_str.replace('Z', '+00:00'))
                    
                    # Get current time - use UTC for comparison since API time may be in any timezone
                    # Calculate age based on elapsed time regardless of timezone
                    utc_offset = cached_data.get('utc_offset_seconds', 0)
                    now_utc = datetime.now(timezone.utc)
                    
                    # Convert API time to UTC for age calculation
                    api_time_utc = api_time.replace(tzinfo=timezone.utc) if api_time.tzinfo is None else api_time.astimezone(timezone.utc)
                    
                    # Adjust for timezone offset if API time doesn't have timezone info
                    from datetime import timedelta
                    if api_time.tzinfo is None:
                        api_time_utc = (api_time - timedelta(seconds=utc_offset)).replace(tzinfo=timezone.utc)
                    
                    age_seconds = (now_utc - api_time_utc).total_seconds()
                    
                    # Cache for 15 minutes (900 seconds)
                    if 0 <= age_seconds < 900:
                        current_app.logger.debug(f'Weather cache HIT for {cache_key}, age: {age_seconds:.0f}s')
                        return jsonify(cached_data)
                    else:
                        current_app.logger.debug(f'Weather cache EXPIRED for {cache_key}, age: {age_seconds:.0f}s')
                except (ValueError, KeyError, AttributeError) as e:
                    current_app.logger.warning(f'Cache time parsing error: {e}, fetching fresh data')
        
        # Forward request to Open-Meteo API
        api_url = 'https://api.open-meteo.com/v1/forecast'
        
        # Set a reasonable timeout to prevent hanging
        response = requests.get(api_url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        # Cache the response using API's timestamp
        _weather_cache[cache_key] = data
        current_app.logger.debug(f'Weather cache MISS for {cache_key}, fetched fresh data')
        
        # Limit cache size to prevent memory issues (keep last 100 entries)
        if len(_weather_cache) > 100:
            # Remove oldest entries
            oldest_keys = list(_weather_cache.keys())[:-50]
            for key in oldest_keys:
                _weather_cache.pop(key, None)
        
        return jsonify(data)
        
    except requests.exceptions.Timeout:
        current_app.logger.error('Weather API timeout')
        return jsonify({'error': 'Weather service timeout'}), 504
    except requests.exceptions.RequestException as e:
        current_app.logger.error(f'Weather API error: {e}')
        return jsonify({'error': 'Failed to fetch weather data'}), 502
    except Exception as e:
        current_app.logger.error(f'Unexpected error in weather proxy: {e}')
        return jsonify({'error': 'Internal server error'}), 500
