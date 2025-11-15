/**
 * Weather Widget for Home Hub
 * Uses Open-Meteo API to display current weather conditions
 */

(function() {
	'use strict';

	// Weather code mapping (WMO Weather interpretation codes)
	const weatherCodes = {
		0: { desc: 'Clear sky', icon: 'fa-sun', color: 'text-yellow-500' },
		1: { desc: 'Mainly clear', icon: 'fa-sun', color: 'text-yellow-500' },
		2: { desc: 'Partly cloudy', icon: 'fa-cloud-sun', color: 'text-gray-500' },
		3: { desc: 'Overcast', icon: 'fa-cloud', color: 'text-gray-600' },
		45: { desc: 'Foggy', icon: 'fa-smog', color: 'text-gray-500' },
		48: { desc: 'Foggy', icon: 'fa-smog', color: 'text-gray-500' },
		51: { desc: 'Light drizzle', icon: 'fa-cloud-rain', color: 'text-blue-400' },
		53: { desc: 'Drizzle', icon: 'fa-cloud-rain', color: 'text-blue-500' },
		55: { desc: 'Heavy drizzle', icon: 'fa-cloud-showers-heavy', color: 'text-blue-600' },
		61: { desc: 'Light rain', icon: 'fa-cloud-rain', color: 'text-blue-400' },
		63: { desc: 'Rain', icon: 'fa-cloud-rain', color: 'text-blue-500' },
		65: { desc: 'Heavy rain', icon: 'fa-cloud-showers-heavy', color: 'text-blue-600' },
		71: { desc: 'Light snow', icon: 'fa-snowflake', color: 'text-blue-300' },
		73: { desc: 'Snow', icon: 'fa-snowflake', color: 'text-blue-400' },
		75: { desc: 'Heavy snow', icon: 'fa-snowflake', color: 'text-blue-500' },
		77: { desc: 'Snow grains', icon: 'fa-snowflake', color: 'text-blue-400' },
		80: { desc: 'Light showers', icon: 'fa-cloud-sun-rain', color: 'text-blue-400' },
		81: { desc: 'Showers', icon: 'fa-cloud-showers-heavy', color: 'text-blue-500' },
		82: { desc: 'Heavy showers', icon: 'fa-cloud-showers-heavy', color: 'text-blue-600' },
		85: { desc: 'Light snow showers', icon: 'fa-snowflake', color: 'text-blue-300' },
		86: { desc: 'Snow showers', icon: 'fa-snowflake', color: 'text-blue-400' },
		95: { desc: 'Thunderstorm', icon: 'fa-bolt', color: 'text-yellow-600' },
		96: { desc: 'Thunderstorm with hail', icon: 'fa-bolt', color: 'text-yellow-700' },
		99: { desc: 'Thunderstorm with hail', icon: 'fa-bolt', color: 'text-yellow-700' }
	};

	function getWeatherIcon(code) {
		return weatherCodes[code] || { desc: 'Unknown', icon: 'fa-question', color: 'text-gray-500' };
	}

	function displayError(container, message) {
		container.innerHTML = `
			<div class="text-center text-red-500 py-4">
				<i class="fa-solid fa-exclamation-triangle text-2xl"></i>
				<p class="mt-2">${message}</p>
			</div>
		`;
	}

	function displayWeather(container, data, units, cfg) {
		const current = data.current;
		
		if (!current) {
			displayError(container, 'Invalid weather data received');
			return;
		}

		// All data from current object
		const temp = current.temperature_2m;
		const weatherCode = current.weather_code || 0;
		const windSpeed = current.wind_speed_10m || 0;
		const humidity = current.relative_humidity_2m ?? null;
		const feelsLike = current.apparent_temperature ?? null;
		const precipitation = current.precipitation ?? null;
		const rain = current.rain ?? null;
		const weather = getWeatherIcon(weatherCode);
		const tempUnit = units === 'imperial' ? '°F' : '°C';
		const speedUnit = units === 'imperial' ? 'mph' : 'km/h';

		let precipLabel = 'No rain';
		const amount = rain ?? precipitation;
		if (typeof amount === 'number' && amount > 0) {
			precipLabel = `${amount.toFixed(1)} mm`;
		}

		const feelsLikeText = typeof feelsLike === 'number'
			? `${Math.round(feelsLike)}${tempUnit}`
			: '—';

		const humidityText = typeof humidity === 'number'
			? `${Math.round(humidity)}%`
			: '—';

		// Format last updated from current.time
		let lastUpdated = '';
		if (current.time) {
			try {
				const dt = new Date(current.time);
				const opts = { year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' };
				if (cfg && cfg.timezone) opts.timeZone = cfg.timezone;
				lastUpdated = dt.toLocaleString(undefined, opts);
			} catch(e) {
				lastUpdated = String(current.time).replace('T',' ');
			}
		}

		// Responsive layout: desktop = left (icon+temp), right (stats). Mobile = stacked.
		container.innerHTML = `
			<div class="flex flex-col md:flex-row items-center md:items-start justify-between gap-4 md:gap-6">
				<!-- Left: Icon + Temp + Condition -->
				<div class="flex items-center md:items-start gap-3 md:gap-4">
					<i class="fa-solid ${weather.icon} ${weather.color} text-5xl md:text-6xl"></i>
					<div>
						<div class="text-4xl md:text-5xl font-bold">${Math.round(temp)}${tempUnit}</div>
						<div class="text-gray-600 text-lg">${weather.desc}</div>
					</div>
				</div>

				<!-- Right: Stats grid -->
				<div class="grid grid-cols-2 gap-x-6 gap-y-2 w-full md:w-auto text-sm md:text-base text-gray-700">
					<div class="flex items-center gap-2">
						<i class="fa-solid fa-temperature-half text-gray-500"></i>
						<span>Feels like: <span class="font-semibold text-gray-900">${feelsLikeText}</span></span>
					</div>
					<div class="flex items-center gap-2">
						<i class="fa-solid fa-wind text-gray-500"></i>
						<span>Wind: <span class="font-semibold text-gray-900">${Math.round(windSpeed)} ${speedUnit}</span></span>
					</div>
					<div class="flex items-center gap-2">
						<i class="fa-solid fa-droplet text-gray-500"></i>
						<span>Humidity: <span class="font-semibold text-gray-900">${humidityText}</span></span>
					</div>
					<div class="flex items-center gap-2">
						<i class="fa-solid fa-cloud-rain text-gray-500"></i>
						<span>Rain: <span class="font-semibold text-gray-900">${precipLabel}</span></span>
					</div>
				</div>
			</div>
			${ lastUpdated ? `<div class="text-[11px] text-gray-500 mt-3 md:mt-4 text-right">Last updated: ${lastUpdated}</div>` : ''}
		`;
	}

	async function fetchWeather(lat, lon, config, cache) {
		// Check cache
		const now = Date.now();
		if (cache.data && cache.time && (now - cache.time) < config.cache * 60 * 1000) {
			return cache.data;
		}

		const params = new URLSearchParams({
			latitude: lat,
			longitude: lon,
			current: 'temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,rain,weather_code,wind_speed_10m',
			temperature_unit: config.units === 'imperial' ? 'fahrenheit' : 'celsius',
			windspeed_unit: config.units === 'imperial' ? 'mph' : 'kmh',
			precipitation_unit: 'mm'
		});

		if (config.timezone) {
			params.append('timezone', config.timezone);
		}

		const response = await fetch(`https://api.open-meteo.com/v1/forecast?${params}`);
		if (!response.ok) {
			throw new Error('Weather API request failed');
		}

		const data = await response.json();
		cache.data = data;
		cache.time = now;
		return data;
	}

	// Initialize weather widget
	window.initWeatherWidget = function(config) {
		const weatherContent = document.getElementById('weatherContent');
		const weatherLocation = document.getElementById('weatherLocation');
		
		if (!weatherContent || !weatherLocation) {
			console.warn('Weather widget elements not found');
			return;
		}

		// Ensure defaults
		config = config || {};
		config.units = config.units || 'metric';
		config.cache = config.cache || 5;

		const cache = { data: null, time: null };

		async function initWithCoords(lat, lon) {
			if (config.label) {
				weatherLocation.textContent = config.label;
			}

			const loadAndRender = async () => {
				try {
					const data = await fetchWeather(lat, lon, config, cache);
					displayWeather(weatherContent, data, config.units, config);
					// If no label configured, set location from API timezone
					if (!config.label && data && data.timezone) {
						weatherLocation.textContent = '';
					}
				} catch (error) {
					console.error('Weather fetch error:', error);
					displayError(weatherContent, 'Failed to load weather data');
				}
			};

			await loadAndRender();
			// Periodic refresh based on cache minutes
			const intervalMs = Math.max(1, Number(config.cache || 5)) * 60 * 1000;
			if (!window.__weatherRefreshTimer) {
				window.__weatherRefreshTimer = setInterval(loadAndRender, intervalMs);
			}
		}

		// Initialize weather widget
		if (config.latitude && config.longitude) {
			// Use configured coordinates
			initWithCoords(config.latitude, config.longitude);
		} else if ('geolocation' in navigator && window.isSecureContext) {
			// Use browser geolocation
			navigator.geolocation.getCurrentPosition(
				position => {
					initWithCoords(position.coords.latitude, position.coords.longitude);
				},
				error => {
					console.error('Geolocation error:', error);
					displayError(weatherContent, 'Unable to get your location. Please configure coordinates in config.yml');
				}
			);
		} else {
			displayError(weatherContent, 'Geolocation not available. Please configure coordinates in config.yml');
		}
	};
})();
