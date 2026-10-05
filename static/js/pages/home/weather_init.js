(function() {
	const weatherConfigElement = document.getElementById('weatherConfigData');
	if (typeof window.initWeatherWidget === 'function' && weatherConfigElement) {
		try {
			const weatherConfig = JSON.parse(weatherConfigElement.textContent);
			window.initWeatherWidget(weatherConfig);
		} catch (e) {
			console.error('Failed to parse weather config:', e);
		}
	}
})();
