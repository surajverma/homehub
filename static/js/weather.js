/**
 * Weather Widget for Home Hub
 * Uses Open-Meteo API to display current weather conditions
 */

(function() {
	'use strict';

	// Weather code mapping (WMO Weather interpretation codes)
	const weatherCodes = {
		0: { desc: 'Clear', icon: 'fa-sun', color: 'text-yellow-400' },
		1: { desc: 'Mainly Clear', icon: 'fa-cloud-sun', color: 'text-yellow-400' },
		2: { desc: 'Partly Cloudy', icon: 'fa-cloud', color: 'text-gray-400' },
		3: { desc: 'Overcast', icon: 'fa-cloud', color: 'text-gray-500' },
		45: { desc: 'Fog', icon: 'fa-smog', color: 'text-gray-400' },
		48: { desc: 'Freezing Fog', icon: 'fa-smog', color: 'text-cyan-200' },
		51: { desc: 'Light Drizzle', icon: 'fa-cloud-rain', color: 'text-blue-400' },
		53: { desc: 'Drizzle', icon: 'fa-cloud-rain', color: 'text-blue-500' },
		55: { desc: 'Heavy Drizzle', icon: 'fa-cloud-showers-heavy', color: 'text-blue-600' },
		61: { desc: 'Light Rain', icon: 'fa-cloud-rain', color: 'text-blue-400' },
		63: { desc: 'Rain', icon: 'fa-cloud-rain', color: 'text-blue-500' },
		65: { desc: 'Heavy Rain', icon: 'fa-cloud-showers-heavy', color: 'text-blue-600' },
		71: { desc: 'Light Snow', icon: 'fa-snowflake', color: 'text-cyan-300' },
		73: { desc: 'Snow', icon: 'fa-snowflake', color: 'text-cyan-400' },
		75: { desc: 'Heavy Snow', icon: 'fa-snowflake', color: 'text-cyan-500' },
		77: { desc: 'Snow Grains', icon: 'fa-snowflake', color: 'text-cyan-400' },
		80: { desc: 'Light Showers', icon: 'fa-cloud-sun-rain', color: 'text-blue-400' },
		81: { desc: 'Showers', icon: 'fa-cloud-showers-heavy', color: 'text-blue-500' },
		82: { desc: 'Heavy Showers', icon: 'fa-cloud-showers-heavy', color: 'text-blue-600' },
		85: { desc: 'Snow Showers', icon: 'fa-snowflake', color: 'text-cyan-300' },
		86: { desc: 'Heavy Snow Showers', icon: 'fa-snowflake', color: 'text-cyan-400' },
		95: { desc: 'Thunderstorm', icon: 'fa-bolt', color: 'text-yellow-500' },
		96: { desc: 'Thunderstorm + Hail', icon: 'fa-cloud-bolt', color: 'text-yellow-600' },
		99: { desc: 'Thunderstorm + Hail', icon: 'fa-cloud-bolt', color: 'text-yellow-600' }
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

	const TTL_MS = 15 * 60 * 1000; // fixed 15 minutes cache window

	function displayWeather(container, data, units, cfg) {
		const current = data.current;
		if (!current) {
			displayError(container, 'Invalid weather data received');
			return;
		}

		// --- Main current data ---
		const temp = current.temperature_2m;
		const weatherCode = current.weather_code || 0;
		const windSpeed = current.wind_speed_10m || 0;
		const windGust = current.wind_gusts_10m || null;
		const windDir = current.wind_direction_10m;
		const humidity = current.relative_humidity_2m ?? null;
		const feelsLike = current.apparent_temperature ?? null;
		const precipitation = current.precipitation ?? null;
		const rain = current.rain ?? null;
		let weather = getWeatherIcon(weatherCode);
		const tempUnit = units === 'imperial' ? '°F' : '°C';
		const speedUnit = units === 'imperial' ? 'mph' : 'km/h';

		// Night clear icon adjustment
		if (current.is_day === 0 && [0, 1].includes(weatherCode)) {
			weather = { desc: 'Clear', icon: 'fa-moon', color: 'text-indigo-300' };
		}

		// --- Labels and text formatting ---
		const precipLabel = (rain ?? precipitation) > 0 ? `${(rain ?? precipitation).toFixed(1)} mm` : 'No rain';
		const feelsLikeText = typeof feelsLike === 'number' ? `${Math.round(feelsLike)}${tempUnit}` : '—';
		const humidityText = typeof humidity === 'number' ? `${Math.round(humidity)}%` : '—';

		function degToCompass(deg) {
			if (typeof deg !== 'number' || isNaN(deg)) return '';
			const dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
			return dirs[Math.round(deg / 22.5) % 16];
		}
		const windDirText = degToCompass(windDir);
		const windLine = `${Math.round(windSpeed)} ${speedUnit} ${windDirText}`;
		const gustLine = (typeof windGust === 'number' && windGust > 0) ? `${Math.round(windGust)} ${speedUnit}` : '—';

		// --- Daily data (if available) ---
		let dailyHtml = '';
		const daily = data.daily || null;
		if (cfg && cfg.view === 'detailed' && daily) {
			const fmtTime = (s) => {
				try {
					const d = new Date(s);
					const opts = { hour: '2-digit', minute: '2-digit' };
					if (cfg.timezone) opts.timeZone = cfg.timezone;
					return d.toLocaleTimeString(undefined, opts);
				} catch (e) { return String(s).split('T')[1] || String(s); }
			};

			const uv = daily.uv_index_max?.[0] ?? '—';
			const rainProb = daily.precipitation_probability_max?.[0] ?? '—';
			const tMax = daily.temperature_2m_max?.[0];
			const tMin = daily.temperature_2m_min?.[0];
			const sunrise = daily.sunrise?.[0] ? fmtTime(daily.sunrise[0]) : '—';
			const sunset = daily.sunset?.[0] ? fmtTime(daily.sunset[0]) : '—';

			dailyHtml = `
				<div class="pt-3 mt-3 border-t">
					<div class="flex items-center justify-between mb-2">
						<div class="text-base font-semibold">Today's Forecast</div>
						<div class="text-sm text-gray-700 flex items-center gap-2"><i class="fa-solid fa-arrow-up-long text-red-500"></i> H: <span class="font-semibold">${tMax!=null?Math.round(tMax)+tempUnit:'—'}</span> <i class="fa-solid fa-arrow-down-long text-blue-500 ml-3"></i> L: <span class="font-semibold">${tMin!=null?Math.round(tMin)+tempUnit:'—'}</span></div>
					</div>
					<div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm text-gray-700">
						<div class="flex items-center gap-2"><i class="fa-solid fa-sun text-amber-500"></i><span>Sunrise: <span class="font-semibold text-gray-900">${sunrise}</span></span></div>
						<div class="flex items-center gap-2"><i class="fa-solid fa-moon text-indigo-400"></i><span>Sunset: <span class="font-semibold text-gray-900">${sunset}</span></span></div>
						<div class="flex items-center gap-2"><i class="fa-solid fa-sun text-yellow-500"></i><span>UV Index: <span class="font-semibold text-gray-900">${uv!=null?uv:'—'}</span></span></div>
						<div class="flex items-center gap-2"><i class="fa-solid fa-cloud-rain text-blue-500"></i><span>Rain: <span class="font-semibold text-gray-900">${rainProb!=null?rainProb+'%':'—'}</span></span></div>
					</div>
				</div>
			`;
		}

		// --- Final HTML structure ---
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
						<span>Wind: <span class="font-semibold text-gray-900">${windLine}</span></span>
					</div>
					<div class="flex items-center gap-2">
						<i class="fa-solid fa-wind text-gray-500"></i>
						<span>Gusts: <span class="font-semibold text-gray-900">${gustLine}</span></span>
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
			${dailyHtml}
		`;

		// --- Last updated with relative time ---
		let lastUpdatedHtml = '';
		if (current.time) {
			try {
				const dt = new Date(current.time);
				const now = Date.now();
				const diffMin = Math.floor((now - dt.getTime()) / 60000);
				let relativeTime = '';
				if (diffMin < 1) relativeTime = 'just now';
				else if (diffMin === 1) relativeTime = '1 minute ago';
				else if (diffMin < 60) relativeTime = `${diffMin} minutes ago`;
				else if (diffMin < 120) relativeTime = '1 hour ago';
				else relativeTime = `${Math.floor(diffMin/60)} hours ago`;
				
				const opts = { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' };
				if (cfg && cfg.timezone) opts.timeZone = cfg.timezone;
				const absTime = dt.toLocaleString(undefined, opts);
				lastUpdatedHtml = `<div class="text-[11px] text-gray-500 mt-3 md:mt-4 text-right">Last updated: ${absTime} (${relativeTime})</div>`;
			} catch(e) {
				const fallback = String(current.time).replace('T',' ');
				lastUpdatedHtml = `<div class="text-[11px] text-gray-500 mt-3 md:mt-4 text-right">Last updated: ${fallback}</div>`;
			}
		}
		
		// Append last updated to container
		if (lastUpdatedHtml) {
			container.insertAdjacentHTML('beforeend', lastUpdatedHtml);
		}
	}

	const CACHE_VERSION = 'v2'; // Increment to invalidate old caches

	async function fetchWeather(lat, lon, config, cache, retryCount = 0) {
		const now = Date.now();
		// cross-session cache via localStorage with version migration
		const tzKey = config.timezone ? config.timezone : 'auto';
		const viewKey = config.view || 'compact';
		const unitsKey = (config.units || 'metric');
		const storageKey = `weatherCache:${CACHE_VERSION}:${lat},${lon}:${unitsKey}:${viewKey}:${tzKey}`;
		
		// Clear old cache versions
		try {
			const allKeys = Object.keys(localStorage);
			allKeys.forEach(k => {
				if (k.startsWith('weatherCache:') && !k.startsWith(`weatherCache:${CACHE_VERSION}:`)) {
					localStorage.removeItem(k);
				}
			});
		} catch(_) {}
		
		try{
			const raw = localStorage.getItem(storageKey);
			if (raw) {
				const obj = JSON.parse(raw);
				if (obj && obj.time && obj.data && (now - obj.time) < TTL_MS) {
					cache.data = obj.data; cache.time = obj.time; // hydrate in-memory cache
					return obj.data;
				}
			}
		}catch(_){}

		const params = new URLSearchParams({
			latitude: lat,
			longitude: lon,
			current: 'is_day,apparent_temperature,relative_humidity_2m,temperature_2m,precipitation,rain,weather_code,wind_gusts_10m,wind_speed_10m,wind_direction_10m',
			temperature_unit: config.units === 'imperial' ? 'fahrenheit' : 'celsius',
			windspeed_unit: config.units === 'imperial' ? 'mph' : 'kmh',
			precipitation_unit: 'mm'
		});

		if ((config.view || 'compact') === 'detailed') {
			params.append('daily', 'sunrise,sunset,uv_index_max,precipitation_probability_max,temperature_2m_max,temperature_2m_min');
		}

		params.append('timezone', config.timezone ? config.timezone : 'auto');

		try {
			const response = await fetch(`https://api.open-meteo.com/v1/forecast?${params}`);
			if (!response.ok) {
				throw new Error(`Weather API returned ${response.status}`);
			}

			const data = await response.json();
			cache.data = data;
			cache.time = now;
			try{ localStorage.setItem(storageKey, JSON.stringify({ time: now, data })); }catch(_){ /* ignore quota */ }
			return data;
		} catch (error) {
			// Retry with exponential backoff (max 3 attempts)
			if (retryCount < 2) {
				const delay = Math.pow(2, retryCount) * 1000; // 1s, 2s
				console.warn(`Weather fetch failed, retrying in ${delay}ms...`, error);
				await new Promise(resolve => setTimeout(resolve, delay));
				return fetchWeather(lat, lon, config, cache, retryCount + 1);
			}
			throw error; // Final failure after retries
		}
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
		config.view = config.view || 'compact';

		const cache = { data: null, time: null };

		async function initWithCoords(lat, lon) {
			// Validate coordinates
			if (typeof lat !== 'number' || typeof lon !== 'number' || 
			    isNaN(lat) || isNaN(lon) || 
			    lat < -90 || lat > 90 || 
			    lon < -180 || lon > 180) {
				displayError(weatherContent, 'Invalid coordinates. Latitude must be -90 to 90, longitude -180 to 180.');
				return;
			}
			
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
			// Schedule refresh aligned to 15-min TTL
			// After loadAndRender, cache.time will be set (either from localStorage or fresh fetch)
			const now = Date.now();
			const age = cache.time ? (now - cache.time) : TTL_MS;
			const firstDelay = Math.max(1000, TTL_MS - age);
			
			if (window.__weatherRefreshTimer) { clearInterval(window.__weatherRefreshTimer); }
			if (window.__weatherRefreshKickoff) { clearTimeout(window.__weatherRefreshKickoff); }
			
			// Only schedule refresh if data is still fresh; otherwise it will refresh on next load
			if (age < TTL_MS) {
				window.__weatherRefreshKickoff = setTimeout(()=>{
					loadAndRender();
					window.__weatherRefreshTimer = setInterval(loadAndRender, TTL_MS);
				}, firstDelay);
			}
		}

		// Initialize weather widget
		if (config.latitude && config.longitude) {
			// Use configured coordinates (convert to numbers)
			const lat = parseFloat(config.latitude);
			const lon = parseFloat(config.longitude);
			initWithCoords(lat, lon);
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
