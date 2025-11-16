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

	const TTL_MS = 15 * 60 * 1000; // fixed 15 minutes cache window

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
		if (current.is_day === 0 && [0,1].includes(weatherCode)) {
			weather = { desc: 'Clear sky', icon: 'fa-moon', color: 'text-indigo-400' };
		}

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

		// Map wind direction degrees to compass text (e.g., NE)
		function degToCompass(deg){
			if (typeof deg !== 'number' || isNaN(deg)) return '';
			const dirs=['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];
			return dirs[Math.round(deg/22.5)%16];
		}

		const windDirText = degToCompass(windDir);
		const windLine = `${Math.round(windSpeed)} ${speedUnit}${windDirText ? ' ' + windDirText : ''}${(typeof windGust==='number' && windGust>0) ? `, gusts ${Math.round(windGust)} ${speedUnit}` : ''}`;

		// Prepare daily (detailed) data when available
		let dailyHtml = '';
		const daily = data.daily || null;
		if (cfg && cfg.view === 'detailed' && daily) {
			function fmtTime(s){
				try{
					const d=new Date(s);
					const opts={hour:'2-digit',minute:'2-digit'}; if(cfg.timezone) opts.timeZone=cfg.timezone; return d.toLocaleTimeString(undefined, opts);
				}catch(e){ return String(s).split('T')[1]||String(s); }
			}
			const uv = (daily.uv_index_max && daily.uv_index_max.length) ? daily.uv_index_max[0] : null;
			const rainProb = (daily.precipitation_probability_max && daily.precipitation_probability_max.length) ? daily.precipitation_probability_max[0] : null;
			const tMax = (daily.temperature_2m_max && daily.temperature_2m_max.length) ? daily.temperature_2m_max[0] : null;
			const tMin = (daily.temperature_2m_min && daily.temperature_2m_min.length) ? daily.temperature_2m_min[0] : null;
			const sunrise = (daily.sunrise && daily.sunrise.length) ? fmtTime(daily.sunrise[0]) : '—';
			const sunset = (daily.sunset && daily.sunset.length) ? fmtTime(daily.sunset[0]) : '—';

			dailyHtml = `
				<div class="pt-3 mt-3 border-t">
					<div class="flex items-center justify-between mb-2">
						<div class="text-base font-semibold">Today's Forecast</div>
						<div class="text-sm text-gray-700 flex items-center gap-2"><i class="fa-solid fa-arrow-up-long text-gray-500"></i> H: <span class="font-semibold">${tMax!=null?Math.round(tMax)+tempUnit:'—'}</span> <i class="fa-solid fa-arrow-down-long text-gray-500 ml-3"></i> L: <span class="font-semibold">${tMin!=null?Math.round(tMin)+tempUnit:'—'}</span></div>
					</div>
					<div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm text-gray-700">
						<div class="flex items-center gap-2"><i class="fa-solid fa-sun text-amber-500"></i><span>Sunrise: <span class="font-semibold text-gray-900">${sunrise}</span></span></div>
						<div class="flex items-center gap-2"><i class="fa-solid fa-moon text-indigo-400"></i><span>Sunset: <span class="font-semibold text-gray-900">${sunset}</span></span></div>
						<div class="flex items-center gap-2"><i class="fa-solid fa-sun text-yellow-500"></i><span>UV Index: <span class="font-semibold text-gray-900">${uv!=null?uv:'—'}</span></span></div>
						<div class="flex items-center gap-2"><i class="fa-solid fa-cloud text-gray-500"></i><span>Rain: <span class="font-semibold text-gray-900">${rainProb!=null?rainProb+'%':'—'}</span></span></div>
					</div>
				</div>
			`;
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
						<span>Wind: <span class="font-semibold text-gray-900">${windLine}</span></span>
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
			${ lastUpdated ? `<div class="text-[11px] text-gray-500 mt-3 md:mt-4 text-right">Last updated: ${lastUpdated}</div>` : ''}
		`;
	}

	async function fetchWeather(lat, lon, config, cache) {
		const now = Date.now();
		// cross-session cache via localStorage
		const tzKey = config.timezone ? config.timezone : 'auto';
		const viewKey = config.view || 'compact';
		const unitsKey = (config.units || 'metric');
		const storageKey = `weatherCache:v1:${lat},${lon}:${unitsKey}:${viewKey}:${tzKey}`;
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

		const response = await fetch(`https://api.open-meteo.com/v1/forecast?${params}`);
		if (!response.ok) {
			throw new Error('Weather API request failed');
		}

		const data = await response.json();
		cache.data = data;
		cache.time = now;
		try{ localStorage.setItem(storageKey, JSON.stringify({ time: now, data })); }catch(_){ /* ignore quota */ }
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
		config.view = config.view || 'compact';

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
